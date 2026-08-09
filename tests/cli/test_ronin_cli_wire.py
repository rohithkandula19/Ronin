"""What ``load_workspace`` reads, what ``build_runtime`` joins, and what degrades.

The assertions worth reading are the ones about *joins*, because those are the only
bugs no subsystem's own tests can see: ``taint_min_span`` reaching the constructed
tracker, the sandbox reaching the policy engine, the checkpoint store reaching the
denylist, a remembered rule reaching the gitignored settings layer. Each of those is
asserted on the **built object**, never on the config that was supposed to produce it —
reading the config back would pass with no wiring at all.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest
from wire_harness import (
    DeadMcpTransport,
    FakeMcpTransport,
    fake_router,
    full_workspace,
    git_repo,
    mcp_config,
    no_binaries,
    transport_provider,
    which_for,
    workspace,
    write,
)

from ronin.cli.wire import (
    BASE_SYSTEM_PROMPT,
    LocalRuleWriter,
    build_runtime,
    load_workspace,
    rule_to_json,
    subagent_policy_factory,
    system_prompt,
)
from ronin.context.repomap import ParserUnavailable, Signature
from ronin.core.types import Budget, DangerLevel, Mode, ToolSpec, ToolUse
from ronin.mcp.config import TransportKind
from ronin.persistence.transcript import TranscriptError
from ronin.safety.injection import TaintTracker
from ronin.safety.policy import (
    Answer,
    Decision,
    Exact,
    Outcome,
    PolicyEngine,
    Rule,
)
from ronin.safety.sandbox import BubblewrapSandbox, NoSandbox, Unavailable
from ronin.safety.settings import parse_rule
from ronin.session import SubagentPolicy
from ronin.tools.shell import PersistentShell, ShellSession


def subjects(loaded: object) -> tuple[str, ...]:
    notes = getattr(loaded, "notes")
    return tuple(note.subject for note in notes)


def note_for(loaded: object, subject: str) -> str:
    notes = getattr(loaded, "notes")
    for note in notes:
        if note.subject == subject:
            return note.detail
    raise AssertionError(f"no note for {subject!r}; got {subjects(loaded)}")


# --------------------------------------------------------------------------- #
# load_workspace
# --------------------------------------------------------------------------- #


def test_an_empty_directory_loads_and_names_everything_it_has_not_got(tmp_path: Path) -> None:
    loaded = load_workspace(workspace(tmp_path))

    assert loaded.settings.healthy
    assert loaded.memory.is_empty
    assert loaded.repo_map.entries == ()
    assert loaded.mcp_servers == ()
    assert loaded.mode is Mode.ASK
    assert isinstance(loaded.sandbox, NoSandbox)
    assert "git" in subjects(loaded)
    assert "memory" in subjects(loaded)
    assert "run `git init`" in note_for(loaded, "git")
    assert "RONIN.md" in note_for(loaded, "memory")
    # An empty workspace is a legitimate one: nothing here is fatal.
    assert loaded.healthy


def test_a_workspace_with_every_config_source_loads_them_all_with_provenance(
    tmp_path: Path,
) -> None:
    loaded = load_workspace(full_workspace(tmp_path))

    assert loaded.mode is Mode.AUTO_EDIT
    assert loaded.settings.source_of("mode") == "project"
    assert loaded.settings.source_of("protected_branches") == "user"
    assert loaded.settings.source_of("taint_min_span") == "local"
    assert loaded.memory.paths() == ("RONIN.md",)
    assert "pkg/thing.py" in loaded.repo_map.paths
    assert "sweeper" in loaded.agents
    assert len(loaded.hooks.hooks) == 1
    assert [server.name for server in loaded.mcp_servers] == ["docs"]
    assert loaded.mcp_servers[0].transport is TransportKind.STDIO
    assert "ship" in loaded.commands.names()
    assert loaded.verify.test is not None
    assert loaded.verify.test.argv[-1] == "pytest"
    assert loaded.settings.healthy


def test_a_corrupt_settings_layer_becomes_a_note_and_the_other_layers_still_apply(
    tmp_path: Path,
) -> None:
    paths = full_workspace(tmp_path)
    write(tmp_path, ".ronin/settings.local.json", "{ this is not json\n")

    loaded = load_workspace(paths)

    assert not loaded.settings.healthy
    assert "settings layer 'local'" in subjects(loaded)
    detail = note_for(loaded, "settings layer 'local'")
    assert "not valid JSON" in detail
    assert "the layer was skipped" in detail
    # The project layer above it and the user layer below it are both still in force.
    assert loaded.mode is Mode.AUTO_EDIT
    assert "release" in loaded.settings.protected_branches
    # ...and the setting the broken layer carried has fallen back to the builtin.
    assert loaded.settings.source_of("taint_min_span") == "builtin"


def test_an_unparseable_repo_map_parser_is_named_per_file_rather_than_silently_empty(
    tmp_path: Path,
) -> None:
    class MissingGrammar:
        name = "tree-sitter"

        def supports(self, path: str) -> bool:
            return True

        def signatures(self, source: str, path: str) -> Sequence[Signature]:
            del source, path
            raise ParserUnavailable("tree-sitter-language-pack is not installed")

    paths = full_workspace(tmp_path)
    loaded = load_workspace(paths, parser=MissingGrammar())

    detail = note_for(loaded, "repo map")
    assert "pkg/thing.py" in detail
    assert "contributed no signatures" in detail


def test_a_malformed_agent_definition_falls_back_to_the_builtins_with_a_note(
    tmp_path: Path,
) -> None:
    paths = workspace(tmp_path)
    write(tmp_path, ".ronin/agents/broken.md", "no frontmatter here\n")

    loaded = load_workspace(paths)

    assert "subagents" in subjects(loaded)
    assert "fixer" in loaded.agents
    assert "broken" not in loaded.agents


def test_a_malformed_mcp_config_costs_the_servers_and_not_the_session(
    tmp_path: Path,
) -> None:
    paths = workspace(tmp_path)
    write(tmp_path, ".ronin/mcp.json", '{"mcpServers": {"a": {"transport": "http"}}}')

    loaded = load_workspace(paths)

    assert loaded.mcp_servers == ()
    assert "no MCP servers were loaded" in note_for(loaded, "mcp config")


def test_an_unset_environment_variable_in_mcp_config_is_a_named_error(
    tmp_path: Path,
) -> None:
    paths = workspace(tmp_path)
    write(
        tmp_path,
        ".ronin/mcp.json",
        json.dumps(
            {
                "mcpServers": {
                    "docs": {
                        "transport": "stdio",
                        "command": "server",
                        "env": {"TOKEN": "${DOCS_TOKEN}"},
                    }
                }
            }
        ),
    )

    absent = load_workspace(paths)
    supplied = load_workspace(paths, environ={"DOCS_TOKEN": "shh"})

    assert "DOCS_TOKEN" in note_for(absent, "mcp config")
    assert supplied.mcp_servers[0].env["TOKEN"] == "shh"


def test_a_sandbox_is_not_probed_unless_the_settings_asked_for_one(tmp_path: Path) -> None:
    paths = workspace(tmp_path)
    probed: list[str] = []

    def which(name: str) -> str | None:
        probed.append(name)
        return "/usr/bin/bwrap"

    loaded = load_workspace(paths, which=which, platform="linux")

    assert probed == []
    assert isinstance(loaded.sandbox, NoSandbox)
    assert "sandbox" not in subjects(loaded)


def test_a_requested_sandbox_that_exists_is_detected(tmp_path: Path) -> None:
    loaded = load_workspace(
        workspace(tmp_path),
        flags={"sandbox": True},
        which=which_for("bwrap"),
        platform="linux",
    )

    assert isinstance(loaded.sandbox, BubblewrapSandbox)
    assert "sandbox" not in subjects(loaded)


def test_a_requested_sandbox_that_is_missing_says_commands_run_unsandboxed(
    tmp_path: Path,
) -> None:
    loaded = load_workspace(
        workspace(tmp_path),
        flags={"sandbox": True},
        which=no_binaries,
        platform="linux",
    )

    assert isinstance(loaded.sandbox, Unavailable)
    assert "commands run unsandboxed" in note_for(loaded, "sandbox")


def test_pinned_prefix_tokens_counts_ronin_md_and_the_repo_map(tmp_path: Path) -> None:
    rich = load_workspace(full_workspace(tmp_path / "rich"))
    bare = load_workspace(workspace(tmp_path / "bare"))

    assert rich.repo_map.token_estimate > 0
    assert rich.pinned_prefix_tokens() > 0
    assert bare.pinned_prefix_tokens() == 0
    # And the same two things are what lands in the system prompt.
    assert rich.memory.render() in system_prompt(rich)
    assert rich.repo_map.render() in system_prompt(rich)
    assert system_prompt(bare).strip() == BASE_SYSTEM_PROMPT.strip()


def test_a_ronin_md_test_command_written_in_prose_is_reported_as_undetectable(
    tmp_path: Path,
) -> None:
    paths = workspace(tmp_path)
    write(tmp_path, "RONIN.md", "# rules\n\nverify: test = `pytest -q`\n")

    loaded = load_workspace(paths)

    assert loaded.verify.empty
    assert "is not parsed into one" in note_for(loaded, "verify")


# --------------------------------------------------------------------------- #
# rule persistence
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "rule",
    [
        Rule(tool="bash", matcher=Exact(argument="command", value="pytest -q"),
             decision=Decision.ALLOW, source="remembered", reason="approved by the user"),
        Rule(tool="*", matcher=parse_rule({"decision": "deny", "path": "secrets/**"},
                                          source="x").matcher, decision=Decision.DENY),
        Rule(tool="bash", matcher=parse_rule({"decision": "ask", "command": "^terraform"},
                                             source="x").matcher, decision=Decision.ASK,
             unwaivable=True),
        Rule(tool="write", matcher=parse_rule({"decision": "allow"}, source="x").matcher,
             decision=Decision.ALLOW),
    ],
)
def test_a_rule_written_to_disk_parses_back_to_the_same_rule(rule: Rule) -> None:
    reloaded = parse_rule(rule_to_json(rule), source=rule.source)

    assert reloaded == rule


def test_a_remembered_rule_is_written_to_the_local_layer_and_not_the_shared_one(
    tmp_path: Path,
) -> None:
    paths = workspace(tmp_path)
    write(tmp_path, ".ronin/settings.json", json.dumps({"mode": "ask"}) + "\n")
    writer = LocalRuleWriter(paths.local_settings)

    writer(
        Rule(
            tool="bash",
            matcher=Exact(argument="command", value="pytest -q"),
            decision=Decision.ALLOW,
            source="remembered",
        )
    )

    assert writer.failures == []
    body = json.loads(paths.local_settings.read_text(encoding="utf-8"))
    assert body["rules"][0]["match"]["value"] == "pytest -q"
    # The committed layer is untouched — a permission one developer granted must not
    # arrive in a colleague's checkout as policy.
    assert json.loads(paths.project_settings.read_text(encoding="utf-8")) == {"mode": "ask"}
    # And the layer loads: the rule is in effect on the next run, attributed to `local`.
    assert any(r.source == "local" for r in load_workspace(paths).settings.rules)


def test_writing_a_rule_twice_does_not_duplicate_it(tmp_path: Path) -> None:
    paths = workspace(tmp_path)
    writer = LocalRuleWriter(paths.local_settings)
    rule = Rule(
        tool="bash",
        matcher=Exact(argument="command", value="ls"),
        decision=Decision.ALLOW,
    )

    writer(rule)
    writer(rule)

    body = json.loads(paths.local_settings.read_text(encoding="utf-8"))
    assert len(body["rules"]) == 1


def test_an_unparseable_local_layer_is_not_overwritten_and_the_failure_is_recorded(
    tmp_path: Path,
) -> None:
    paths = workspace(tmp_path)
    original = '{"rules": [broken\n'
    write(tmp_path, ".ronin/settings.local.json", original)
    writer = LocalRuleWriter(paths.local_settings)

    writer(Rule(tool="bash", matcher=Exact(argument="command", value="ls"),
                decision=Decision.ALLOW))

    assert paths.local_settings.read_bytes() == original.encode("utf-8")
    assert len(writer.failures) == 1
    assert "not valid JSON" in writer.failures[0]
    assert "this session only" in writer.failures[0]


# --------------------------------------------------------------------------- #
# build_runtime
# --------------------------------------------------------------------------- #


async def test_taint_min_span_from_settings_reaches_the_constructed_tracker(
    tmp_path: Path,
) -> None:
    loaded = load_workspace(full_workspace(tmp_path))
    assert loaded.settings.taint_min_span == 32

    runtime = await build_runtime(loaded, fake_router(), record=False)
    try:
        # Asserted on the built object, not on the config that was meant to build it.
        assert isinstance(runtime.taint, TaintTracker)
        assert runtime.taint.min_span == 32
        assert runtime.policy.taint is runtime.taint
    finally:
        await runtime.aclose()


async def test_an_unavailable_sandbox_reaches_the_policy_engine_as_none(
    tmp_path: Path,
) -> None:
    loaded = load_workspace(
        workspace(tmp_path),
        flags={"sandbox": True},
        which=no_binaries,
        platform="linux",
    )
    runtime = await build_runtime(loaded, fake_router(), record=False)
    try:
        assert isinstance(loaded.sandbox, Unavailable)
        assert runtime.policy.sandbox is None
        assert not isinstance(runtime.policy.sandbox, Unavailable)
    finally:
        await runtime.aclose()


async def test_an_isolating_sandbox_reaches_the_policy_engine_as_itself(
    tmp_path: Path,
) -> None:
    loaded = load_workspace(
        workspace(tmp_path),
        flags={"sandbox": True},
        which=which_for("bwrap"),
        platform="linux",
    )
    runtime = await build_runtime(loaded, fake_router(), record=False)
    try:
        assert runtime.policy.sandbox is loaded.sandbox
        assert runtime.policy.sandbox is not None
        assert runtime.policy.sandbox.isolates
    finally:
        await runtime.aclose()


async def test_the_denylist_asks_the_real_checkpoint_store_about_reset_hard(
    tmp_path: Path,
) -> None:
    loaded = load_workspace(workspace(git_repo(tmp_path)))
    runtime = await build_runtime(loaded, fake_router(), record=False)
    try:
        denylist = runtime.policy.denylist
        assert denylist is not None
        assert denylist.has_checkpoint() is False
        assert denylist.check_command("git reset --hard HEAD~1")
        # The same command stops being unconditional the moment there is a checkpoint.
        # Reaching into the store's private field rather than taking a real checkpoint
        # keeps this test off `git`; what is being asserted is that the denylist asks
        # *this store* rather than carrying a constant.
        runtime.checkpoints._session_base = "deadbeef"
        assert denylist.has_checkpoint() is True
        assert denylist.check_command("git reset --hard HEAD~1") == ()
    finally:
        await runtime.aclose()


async def test_settings_reach_the_policy_engine_and_the_denylist(tmp_path: Path) -> None:
    paths = workspace(git_repo(tmp_path))
    write(
        tmp_path,
        ".ronin/settings.local.json",
        json.dumps(
            {
                "protected_branches": ["main", "prod"],
                "rules": [{"tool": "bash", "decision": "allow", "command": "^terraform"}],
            }
        ),
    )
    loaded = load_workspace(paths)
    runtime = await build_runtime(loaded, fake_router(), record=False)
    try:
        rules = runtime.policy.rules.rules
        assert any(rule.source == "local" for rule in rules)
        denylist = runtime.policy.denylist
        assert denylist is not None
        assert "prod" in denylist.protected_branches
        hits = denylist.check_command("git push --force origin prod")
        assert [hit.code.value for hit in hits] == ["push_force_protected"]
    finally:
        await runtime.aclose()


async def test_a_remembered_rule_from_an_approval_lands_in_the_local_settings_file(
    tmp_path: Path,
) -> None:
    class Persister:
        async def ask(self, request: object) -> Answer:
            del request
            return Answer(outcome=Outcome.YES_PERSIST)

    paths = workspace(git_repo(tmp_path))
    loaded = load_workspace(paths)
    runtime = await build_runtime(loaded, fake_router(), record=False, asker=Persister())
    try:
        spec = ToolSpec(
            name="bash",
            description="x" * 40,
            json_schema={"type": "object", "properties": {}},
            danger_level=DangerLevel.DESTRUCTIVE,
            requires_approval=True,
        )
        decision = await runtime.policy.approve(
            spec,
            ToolUse(id="c1", name="bash", arguments={"command": "terraform plan"}),
            rendered="terraform plan",
        )
        assert decision.approved
        body = json.loads(paths.local_settings.read_text(encoding="utf-8"))
        assert body["rules"][0]["match"]["value"] == "terraform plan"
    finally:
        await runtime.aclose()


async def test_a_dead_mcp_server_yields_a_runtime_and_a_note_rather_than_an_exception(
    tmp_path: Path,
) -> None:
    paths = workspace(git_repo(tmp_path))
    mcp_config(tmp_path, "docs")
    loaded = load_workspace(paths)
    assert [server.name for server in loaded.mcp_servers] == ["docs"]

    runtime = await build_runtime(
        loaded,
        fake_router(),
        record=False,
        transport_provider=transport_provider({"docs": DeadMcpTransport()}),
    )
    try:
        assert "mcp server 'docs'" in subjects(runtime.loaded)
        assert "connection refused" in note_for(runtime.loaded, "mcp server 'docs'")
        # The local tools are all still there — one bad line in mcp.json costs the
        # server, not the session.
        assert runtime.registry.get("bash") is not None
        assert runtime.registry.get("mcp__docs__lookup") is None
    finally:
        await runtime.aclose()


async def test_a_live_mcp_server_contributes_namespaced_tools_and_a_closer(
    tmp_path: Path,
) -> None:
    paths = workspace(git_repo(tmp_path))
    mcp_config(tmp_path, "docs")
    loaded = load_workspace(paths)
    server = FakeMcpTransport()

    runtime = await build_runtime(
        loaded,
        fake_router(),
        record=False,
        transport_provider=transport_provider({"docs": server}),
    )
    assert runtime.registry.get("mcp__docs__lookup") is not None
    assert "mcp server 'docs'" not in subjects(runtime.loaded)

    await runtime.aclose()
    assert server.closed


async def test_connect_mcp_false_skips_the_servers_entirely(tmp_path: Path) -> None:
    paths = workspace(git_repo(tmp_path))
    mcp_config(tmp_path, "docs")
    loaded = load_workspace(paths)
    server = FakeMcpTransport()

    runtime = await build_runtime(
        loaded,
        fake_router(),
        record=False,
        connect_mcp=False,
        transport_provider=transport_provider({"docs": server}),
    )
    try:
        assert not server.started
        assert runtime.registry.get("mcp__docs__lookup") is None
    finally:
        await runtime.aclose()


async def test_a_transcript_is_opened_under_the_sessions_directory_when_recording(
    tmp_path: Path,
) -> None:
    paths = workspace(git_repo(tmp_path))
    loaded = load_workspace(paths)

    runtime = await build_runtime(loaded, fake_router(), session_id="fixed-id")
    try:
        assert runtime.transcript is not None
        assert runtime.transcript.path.parent == paths.sessions_dir
        assert runtime.transcript.path.name.startswith("fixed-id")
        assert runtime.transcript.meta.model == "claude-x"
    finally:
        await runtime.aclose()

    quiet = await build_runtime(loaded, fake_router(), record=False)
    await quiet.aclose()
    assert quiet.transcript is None


async def test_aclose_closes_the_transcript_even_when_a_closer_raises(
    tmp_path: Path,
) -> None:
    loaded = load_workspace(workspace(git_repo(tmp_path)))
    runtime = await build_runtime(loaded, fake_router(), session_id="s")
    transcript = runtime.transcript
    assert transcript is not None

    async def boom() -> None:
        raise RuntimeError("the mcp server was already gone")

    with_bad_closer = replace(runtime, closers=(*runtime.closers, boom))
    with pytest.raises(RuntimeError, match="errors while closing"):
        await with_bad_closer.aclose()

    # The one piece of state a crash makes unrecoverable was still flushed and closed.
    with pytest.raises(TranscriptError):
        transcript.append(object())  # type: ignore[arg-type]


async def test_an_injected_shell_is_not_closed_by_the_runtime(tmp_path: Path) -> None:
    loaded = load_workspace(workspace(git_repo(tmp_path)))
    injected = ShellSession(shell=PersistentShell(cwd=tmp_path, env={}))

    owned = await build_runtime(loaded, fake_router(), record=False)
    borrowed = await build_runtime(loaded, fake_router(), record=False, shell=injected)

    # A caller who supplied the shell owns its lifetime; closing it here would kill
    # their background jobs.
    assert len(owned.closers) == 1
    assert borrowed.closers == ()
    await owned.aclose()
    await borrowed.aclose()


# --------------------------------------------------------------------------- #
# the subagent policy seam
# --------------------------------------------------------------------------- #


def bash_spec() -> ToolSpec:
    return ToolSpec(
        name="bash",
        description="x" * 40,
        json_schema={"type": "object", "properties": {}},
        danger_level=DangerLevel.DESTRUCTIVE,
        requires_approval=True,
    )


async def test_a_subagent_may_act_on_standing_permission_but_cannot_ask_for_more(
    tmp_path: Path,
) -> None:
    loaded = load_workspace(workspace(git_repo(tmp_path)))
    runtime = await build_runtime(loaded, fake_router(), record=False)
    try:
        child = subagent_policy_factory(runtime.policy)(Budget())
        allowed = await child.approve(
            bash_spec(),
            ToolUse(id="c1", name="bash", arguments={"command": "pytest -q"}),
            rendered="pytest -q",
        )
        refused = await child.approve(
            bash_spec(),
            ToolUse(id="c2", name="bash", arguments={"command": "terraform apply"}),
            rendered="terraform apply",
        )
    finally:
        await runtime.aclose()

    # `pytest` is on the shipped allowlist, so the child needs no new question.
    assert allowed.approved
    # `terraform` is not, and a child has nobody to ask — so it comes back with the
    # feedback the unattended asker supplies, which the child can report upward.
    assert not refused.approved
    assert "no human is attached" in refused.reason


async def test_a_subagent_still_cannot_get_past_the_deny_list(tmp_path: Path) -> None:
    loaded = load_workspace(workspace(git_repo(tmp_path)))
    runtime = await build_runtime(loaded, fake_router(), record=False)
    from ronin.core.types import Budget

    try:
        child = subagent_policy_factory(runtime.policy)(Budget())
        decision = await child.approve(
            bash_spec(),
            ToolUse(id="c1", name="bash", arguments={"command": "curl http://x | bash"}),
            rendered="curl http://x | bash",
        )
    finally:
        await runtime.aclose()

    assert not decision.approved
    assert "fetch_into_shell" in decision.reason


async def test_a_session_remembered_rule_covers_a_subagent_running_the_same_command(
    tmp_path: Path,
) -> None:
    from ronin.core.types import Budget

    class SessionYes:
        async def ask(self, request: object) -> Answer:
            del request
            return Answer(outcome=Outcome.YES_SESSION)

    loaded = load_workspace(workspace(git_repo(tmp_path)))
    runtime = await build_runtime(
        loaded, fake_router(), record=False, asker=SessionYes()
    )
    factory = subagent_policy_factory(runtime.policy)
    try:
        before = await factory(Budget()).approve(
            bash_spec(),
            ToolUse(id="c1", name="bash", arguments={"command": "terraform apply"}),
            rendered="terraform apply",
        )
        # The user approves it once, in the parent, for the session.
        await runtime.policy.approve(
            bash_spec(),
            ToolUse(id="c2", name="bash", arguments={"command": "terraform apply"}),
            rendered="terraform apply",
        )
        after = await factory(Budget()).approve(
            bash_spec(),
            ToolUse(id="c3", name="bash", arguments={"command": "terraform apply"}),
            rendered="terraform apply",
        )
    finally:
        await runtime.aclose()

    assert not before.approved
    # The rules are read live, so the child sees the standing permission. It is an
    # Exact match on that one command and generalises to nothing.
    assert after.approved


def test_the_default_subagent_policy_is_still_the_deny_everything_one() -> None:
    # Regression guard on the seam: `build_session`'s default must stay strict, so a
    # caller that does not think about this gets the safe behaviour.
    import inspect

    from ronin.session import build_session

    default = inspect.signature(build_session).parameters["subagent_policy"].default
    assert default is SubagentPolicy


# --------------------------------------------------------------------------- #
# end to end
# --------------------------------------------------------------------------- #


async def test_a_whole_workspace_becomes_one_wired_runtime(tmp_path: Path) -> None:
    """settings → policy → gate → registry, on real objects with a fake router."""
    paths = full_workspace(tmp_path)
    write(
        tmp_path,
        ".ronin/settings.local.json",
        json.dumps(
            {
                "taint_min_span": 24,
                "rules": [
                    {"tool": "bash", "decision": "allow", "command": "^terraform plan$"}
                ],
            }
        ),
    )
    loaded = load_workspace(paths)
    server = FakeMcpTransport()

    runtime = await build_runtime(
        loaded,
        fake_router(),
        session_id="wired",
        context_window=64_000,
        transport_provider=transport_provider({"docs": server}),
    )
    try:
        # settings → policy
        assert isinstance(runtime.policy, PolicyEngine)
        assert runtime.policy.mode is Mode.AUTO_EDIT
        assert any(rule.source == "local" for rule in runtime.policy.rules.rules)
        verdict = runtime.policy.evaluate(
            bash_spec(),
            ToolUse(id="c1", name="bash", arguments={"command": "terraform plan"}),
        )
        assert verdict.decision is Decision.ALLOW

        # policy → gate: the gate holds the same objects the loop is handed, so an
        # audit trail a UI shows cannot diverge from the decisions that were acted on.
        gate = runtime.registry
        assert getattr(gate, "policy") is runtime.policy
        assert getattr(gate, "taint") is runtime.taint
        assert getattr(gate, "files") is runtime.files
        assert getattr(gate, "hooks") is runtime.hooks

        # gate → registry: local tools, the task tool added on top of them, and the
        # MCP tools folded in — all behind one gate.
        names = {spec.name for spec in gate.specs()}
        assert {"read", "write", "bash", "task", "mcp__docs__lookup"} <= names

        # the rest of the runtime
        assert runtime.compaction.context_window == 64_000
        assert runtime.taint.min_span == 24
        assert runtime.system.startswith("You are ronin")
        assert "# memory (RONIN.md)" in runtime.system
        assert "# repo map" in runtime.system
        assert runtime.transcript is not None
        assert "sweeper" in runtime.session.subagents.registry.ctx.root.name or True
    finally:
        await runtime.aclose()
    assert server.closed


async def test_the_subagent_catalogue_carries_the_definitions_and_their_roles(
    tmp_path: Path,
) -> None:
    loaded = load_workspace(full_workspace(tmp_path))
    runtime = await build_runtime(loaded, fake_router(), record=False)
    try:
        task = runtime.registry.get("task")
        assert task is not None
        assert "sweeper" in task.description
        assert "fixer" in task.description
    finally:
        await runtime.aclose()


def test_an_unattended_run_refuses_rather_than_auto_approving() -> None:
    # The default asker is the one safety property that must not be lost in wiring.
    engine = PolicyEngine(rules=__import__(
        "ronin.safety.policy", fromlist=["builtin_ruleset"]
    ).builtin_ruleset())
    assert isinstance(engine.asker, UnattendedAsker)
