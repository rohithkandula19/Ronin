"""The destructive floor must not stop at the shell.

Before this, ``is_floored_tool_call`` returned False for every tool that was not
literally named ``run_command``/``run_background``. But a tool's NAME proves
nothing: MCP servers and plugins pick their own. So under ``--yolo`` a
``postgres.query`` carrying ``DROP TABLE users``, or an MCP shell tool carrying
``rm -rf /``, auto-approved without ever meeting the floor — even though
DESTRUCTIVE_MARKERS could already recognise both.

The floor now inspects the executable PAYLOAD (command / script / query / sql …)
on any tool, while deliberately ignoring content-carrying args — writing a file
that *mentions* ``rm -rf`` is not running it.
"""
from __future__ import annotations

import pathlib
import tempfile

import pytest

from ronin_cli import investigate_mode
from ronin_cli.approvals import is_floored_tool_call
from ronin_cli.code_mode import _selective_gate


# (tool name, args) that MUST be floored — none of these are run_command.
FLOORED = [
    ("postgres_query", {"query": "DROP TABLE users"}),           # MCP database tool
    ("postgres_query", {"sql": "TRUNCATE TABLE orders"}),
    ("db", {"statement": "drop database production"}),
    ("mcp_shell", {"command": "rm -rf /"}),                      # MCP shell tool
    ("execute", {"cmd": "git reset --hard"}),
    ("my_plugin_tool", {"script": "git clean -fdx"}),            # plugin tool
    ("runner", {"commands": ["ls -la", "rm -rf ~"]}),            # a LIST of commands
    ("anything", {"bash": "dd if=/dev/zero of=/dev/sda"}),
    ("weird_name", {"exec": "mkfs.ext4 /dev/sda"}),
    # the built-in shell tools still work
    ("run_command", {"command": "rm -rf /"}),
    ("run_background", {"command": "git reset --hard"}),
]

# (tool name, args) that must NOT be floored — a false positive here is how a
# safety gate ends up disabled by an annoyed user.
NOT_FLOORED = [
    ("write_file", {"path": "notes.md", "content": "never run rm -rf / on prod"}),
    ("edit_file", {"path": "a.py", "diff": "- os.system('rm -rf /')"}),
    ("write_file", {"path": "x.sql", "content": "DROP TABLE users"}),   # writing != running
    ("postgres_query", {"query": "SELECT * FROM users LIMIT 10"}),
    ("run_command", {"command": "ls -la"}),
    ("mcp_shell", {"command": "git status"}),
    ("my_plugin_tool", {"script": "print('hello')"}),
    ("read_file", {"path": "/etc/passwd"}),
    ("search", {"pattern": "rm -rf"}),                                  # searching for it
]


@pytest.mark.parametrize("name,args", FLOORED)
def test_destructive_payload_is_floored_on_any_tool(name, args):
    assert is_floored_tool_call(name, args) is True, f"floor missed {name} {args}"


@pytest.mark.parametrize("name,args", NOT_FLOORED)
def test_safe_call_is_not_floored(name, args):
    assert is_floored_tool_call(name, args) is False, f"false positive: {name} {args}"


def test_non_mapping_args_are_not_floored():
    assert is_floored_tool_call("run_command", None) is False
    assert is_floored_tool_call("run_command", "rm -rf /") is False


# ---------- it must actually hold at the real gates, under --yolo ----------

def test_code_gate_floors_mcp_sql_under_yolo():
    gate = _selective_gate(None, True, pathlib.Path(tempfile.mkdtemp()))
    r = gate("postgres_query", {"query": "DROP TABLE users"})
    assert r is not True, "an MCP DROP TABLE auto-approved under --yolo"


def test_code_gate_floors_mcp_shell_under_yolo():
    gate = _selective_gate(None, True, pathlib.Path(tempfile.mkdtemp()))
    r = gate("mcp_shell", {"command": "rm -rf /"})
    assert r is not True, "an MCP shell rm -rf auto-approved under --yolo"


def test_code_gate_still_auto_approves_safe_tools_under_yolo():
    gate = _selective_gate(None, True, pathlib.Path(tempfile.mkdtemp()))
    assert gate("postgres_query", {"query": "SELECT 1"}) is True
    assert gate("read_file", {"path": "x"}) is True


def test_investigate_gate_floors_non_shell_payload_under_yolo():
    gate = investigate_mode._gate(None, True)
    # investigate only routes run_command, but the floor decision itself must agree
    assert is_floored_tool_call("run_command", {"command": "rm -rf /"}) is True
    assert gate("run_command", {"command": "rm -rf /"}) is not True


# ---------------------------------------------------------------------------
# Round 2 — three defects found by adversarially attacking my own first fix.
# ---------------------------------------------------------------------------

# (1) NESTED payloads. The first version scanned only TOP-LEVEL arg keys, so any
# MCP tool that nests its arguments — which is an ordinary shape — walked straight
# through the "fixed" floor.
NESTED_FLOORED = [
    ("mcp_tool", {"params": {"command": "rm -rf /"}}),
    ("mcp_tool", {"arguments": {"cmd": "git reset --hard"}}),
    ("mcp_tool", {"steps": [{"cmd": "rm -rf /"}]}),
    ("mcp_tool", {"a": {"b": {"c": {"sql": "DROP TABLE t"}}}}),
    ("mcp_tool", {"batch": [{"name": "ok", "run": "ls"}, {"name": "bad", "run": "rm -rf ~"}]}),
]


@pytest.mark.parametrize("name,args", NESTED_FLOORED)
def test_nested_payload_is_floored(name, args):
    assert is_floored_tool_call(name, args) is True, f"nested payload escaped: {args}"


# (2) FALSE POSITIVES. A floor that blocks commit messages and greps is a floor
# people switch off — so the SQL markers must not fire on text tools.
EVERYDAY_NOT_FLOORED = [
    ("run_command", {"command": "git commit -m 'fix delete from cart flow'"}),
    ("run_command", {"command": "git commit -m 'add drop table migration'"}),
    ("run_command", {"command": "grep -r 'drop table' ."}),
    ("run_command", {"command": "rg 'delete from' src/"}),
    ("run_command", {"command": "echo 'delete from t'"}),
    ("postgres_query", {"query": "SELECT * FROM audit WHERE action = 'drop table'"}),
    ("postgres_query", {"query": "SELECT * FROM users LIMIT 10"}),
    ("run_command", {"command": "pytest -q"}),
    ("run_command", {"command": "npm test"}),
]


@pytest.mark.parametrize("name,args", EVERYDAY_NOT_FLOORED)
def test_everyday_call_is_not_floored(name, args):
    assert is_floored_tool_call(name, args) is False, f"false positive: {args}"


# …but a shell command that ACTUALLY executes destructive SQL still is.
REAL_SQL_EXECUTION_FLOORED = [
    ("run_command", {"command": 'psql -c "DROP TABLE users"'}),
    ("run_command", {"command": "mysql -e 'TRUNCATE TABLE orders'"}),
    ("postgres_query", {"query": "DROP TABLE users"}),
    ("postgres_query", {"query": "SELECT 1; DROP TABLE users"}),   # multi-statement
    ("db", {"sql": "delete from accounts"}),
]


@pytest.mark.parametrize("name,args", REAL_SQL_EXECUTION_FLOORED)
def test_real_sql_destruction_is_still_floored(name, args):
    assert is_floored_tool_call(name, args) is True, f"floor missed real SQL: {args}"


def test_is_destructive_sql_is_anchored_not_a_substring_scan():
    from ronin_cli.approvals import is_destructive_sql
    assert is_destructive_sql("DROP TABLE users") is True
    assert is_destructive_sql("TRUNCATE TABLE t") is True
    assert is_destructive_sql("SELECT 1; DROP TABLE users") is True
    # a SELECT that merely MENTIONS it destroys nothing
    assert is_destructive_sql("SELECT * FROM audit WHERE a = 'drop table'") is False
    assert is_destructive_sql("SELECT * FROM users") is False


# (3) The THIRD gate. I fixed the ordering in code_mode's two gates and missed
# investigate_mode's, which kept auto-approving anything outside its sensitive set.
def test_investigate_gate_floors_a_non_shell_payload_under_yolo():
    gate = investigate_mode._gate(None, True)
    r = gate("postgres_query", {"query": "DROP TABLE users"})
    assert r is not True, "investigate gate auto-approved DROP TABLE under --yolo"


def test_investigate_gate_still_allows_safe_tools():
    gate = investigate_mode._gate(None, True)
    assert gate("read_file", {"path": "x"}) is True
    assert gate("run_command", {"command": "ls -la"}) is True
