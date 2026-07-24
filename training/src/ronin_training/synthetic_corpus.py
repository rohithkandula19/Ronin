"""Synthetic dialect corpus — templated coding tasks rendered through the
canonical dialect, validated against the real registry, at scale.

Why this exists: the valid_tool_json post-mortem
(training/reports/valid_tool_json_root_cause.md) found the 284-row volume corpus
trains under a different prompt distribution than the eval/runtime (rows carry
0-6 case tools vs the 13-tool registry presented at inference — D1) and teaches
string-typed arguments against the template's own args-json-object instruction
(D2). Every row generated here closes both gaps:

- **all 13 registry tools** attached to every row (the exact list the runtime
  presents), so "pick the right tool out of 13" is what SFT teaches;
- **arguments are JSON objects**, canonical via ``ronin_dialect``;
- every tool call validated against the live registry schema
  (``validators.validate_example``) — a row that wouldn't execute is never
  emitted;
- deterministic: seeded RNG, content-hash dedup, stable output for a given
  ``--seed`` — a corpus you can regenerate byte-for-byte.

The generator is **fully offline**: tool "results" are computed by deterministic
templates (the fake file contents are constructed so the assistant's grounded
answer is true of them). No model, no network, no API key.

TEACHER MODE — ToS GUARD (read before wiring any teacher):
    Teacher-generated traces may come ONLY from open-weight models you run
    yourself (or from Ronin's own captured runs). Anthropic's and OpenAI's
    Terms of Service forbid using their model outputs to train competing
    models — a teacher pointed at Claude or GPT is a license violation, not a
    shortcut. ``--teacher`` therefore refuses to run unless RONIN_TEACHER_URL
    points at a self-hosted open-weight endpoint, and it is BLOCKED in this
    repo until one exists.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path

from ronin_training.validators import load_registry, validate_example

# --------------------------------------------------------------------------- #
# deterministic ingredients
# --------------------------------------------------------------------------- #

MODULES = ["auth", "billing_export", "cache", "cli", "config", "db", "fetcher",
           "indexer", "jobs", "logger", "metrics", "parser", "queue", "router",
           "scheduler", "server", "session", "storage", "sync", "worker",
           "api", "backup", "crypto", "events", "export", "gateway", "health",
           "importer", "inventory", "mailer", "migrate", "notifier", "orders",
           "profile", "ratelimit", "reports", "search", "tokens", "uploads", "webhooks"]
CONSTS = [("TIMEOUT", "30", "60"), ("RETRIES", "3", "5"), ("PAGE_SIZE", "20", "50"),
          ("MAX_CONN", "10", "25"), ("TTL_S", "300", "900"), ("BATCH", "100", "250")]
FUNCS = ["load", "save", "fetch", "parse", "flush", "retry", "connect", "close",
         "validate", "render"]
PORTS = ["3000", "3001", "4173", "5173", "5174", "8000", "8080", "8081", "9000", "9090"]
TESTS = ["pytest -q", "pytest tests/ -q", "npm test", "make test"]
SYSTEMS = [
    "You are Ronin.",
    "You are Ronin, a careful coding agent. Read before you claim; call tools with valid JSON.",
    "You are Ronin. Ground every statement in files you actually read.",
]


def _tools_full_registry() -> list[dict]:
    """All 13 real tools in OpenAI function shape — the runtime's exact prompt."""
    return [
        {"type": "function", "function": {
            "name": t["name"], "description": t["description"], "parameters": t["parameters"],
        }}
        for t in load_registry().values()
    ]


def _call(cid: str, name: str, arguments: dict) -> dict:
    """One OpenAI-shaped tool call with canonical OBJECT arguments (D2 fix)."""
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": arguments}}


# --------------------------------------------------------------------------- #
# task families — each returns the messages of one conversation
# --------------------------------------------------------------------------- #

def fam_read_answer(rng: random.Random) -> list[dict]:
    mod = rng.choice(MODULES); const, old, _ = rng.choice(CONSTS)
    path = f"src/{mod}.py"
    content = f"{const} = {old}\n\n\ndef {rng.choice(FUNCS)}():\n    return {const}\n"
    return [
        {"role": "user", "content": f"What is {const} set to in {path}?"},
        {"role": "assistant", "tool_calls": [_call("c1", "read_file", {"path": path})]},
        {"role": "tool", "tool_call_id": "c1", "content": content},
        {"role": "assistant", "content":
            f"{const} is {old} — defined at the top of {path}."},
    ]


def fam_read_edit_test(rng: random.Random) -> list[dict]:
    mod = rng.choice(MODULES); const, old, new = rng.choice(CONSTS)
    path = f"src/{mod}.py"; test = rng.choice(TESTS)
    content = f"{const} = {old}\n"
    return [
        {"role": "user", "content": f"Bump {const} from {old} to {new} in {path} and run the tests."},
        {"role": "assistant", "content": f"I'll read {path} first so I edit the exact definition.",
         "tool_calls": [_call("c1", "read_file", {"path": path})]},
        {"role": "tool", "tool_call_id": "c1", "content": content},
        {"role": "assistant", "tool_calls": [_call("c2", "edit_file", {
            "path": path, "old_string": f"{const} = {old}", "new_string": f"{const} = {new}"})]},
        {"role": "tool", "tool_call_id": "c2", "content": f"edited {path} (1 replacement)"},
        {"role": "assistant", "tool_calls": [_call("c3", "run_command", {"command": test})]},
        {"role": "tool", "tool_call_id": "c3", "content": f"{rng.randint(5, 240)} passed in 0.{rng.randint(10, 99)}s"},
        {"role": "assistant", "content":
            f"Done — {const} is now {new} in {path} and the suite passes."},
    ]


def fam_search_read(rng: random.Random) -> list[dict]:
    mod = rng.choice(MODULES); fn = rng.choice(FUNCS)
    path = f"src/{mod}.py"
    return [
        {"role": "user", "content": f"Where do we handle {fn} for the {mod} layer?"},
        {"role": "assistant", "tool_calls": [_call("c1", "search_files", {"query": f"def {fn}"})]},
        {"role": "tool", "tool_call_id": "c1", "content": f"{path}:{rng.randint(5, 190)}:def {fn}():"},
        {"role": "assistant", "tool_calls": [_call("c2", "read_file", {"path": path})]},
        {"role": "tool", "tool_call_id": "c2",
         "content": f"def {fn}():\n    # {mod} entry point\n    ..."},
        {"role": "assistant", "content":
            f"`{fn}` is defined in {path} — it's the {mod} entry point."},
    ]


def fam_dev_server(rng: random.Random) -> list[dict]:
    port = rng.choice(PORTS); app = rng.choice(MODULES)
    cmd = f"npm run dev -- --port {port}"
    return [
        {"role": "user", "content":
            f"Start the {app} dev server on port {port} and keep it running while we work."},
        {"role": "assistant", "content":
            "A dev server never exits on its own, so it goes to the background — "
            "run_command would hang until timeout.",
         "tool_calls": [_call("c1", "run_background", {"command": cmd})]},
        {"role": "tool", "tool_call_id": "c1", "content": f'{{"id": 1, "command": "{cmd}"}}'},
        {"role": "assistant", "content":
            f"Dev server started in the background as task 1 on port {port}. "
            "Say the word and I'll stop it with stop_background."},
    ]


def fam_stop_remembered(rng: random.Random) -> list[dict]:
    port = rng.choice(PORTS); app = rng.choice(MODULES)
    return [
        {"role": "user", "content": f"Start the {app} dev server on port {port}."},
        {"role": "assistant",
         "tool_calls": [_call("c1", "run_background", {"command": f"npm run dev -- --port {port}"})]},
        {"role": "tool", "tool_call_id": "c1", "content": '{"id": 1}'},
        {"role": "assistant", "content": "Running in the background as task 1."},
        {"role": "user", "content": "ok we're done, stop it"},
        {"role": "assistant", "tool_calls": [_call("c2", "stop_background", {"id": 1})]},
        {"role": "tool", "tool_call_id": "c2", "content": "stopped task 1"},
        {"role": "assistant", "content": "Stopped task 1 — the dev server is down."},
    ]


def fam_write_verify(rng: random.Random) -> list[dict]:
    mod = rng.choice(MODULES)
    path = f"scripts/check_{mod}.py"
    body = f'print("{mod}: ok")\n'
    return [
        {"role": "user", "content": f"Add a tiny script that prints '{mod}: ok' and prove it runs."},
        {"role": "assistant", "tool_calls": [_call("c1", "write_file", {"path": path, "content": body})]},
        {"role": "tool", "tool_call_id": "c1", "content": f"wrote {path}"},
        {"role": "assistant", "tool_calls": [_call("c2", "run_command", {"command": f"python {path}"})]},
        {"role": "tool", "tool_call_id": "c2", "content": f"{mod}: ok"},
        {"role": "assistant", "content": f"Created {path} and ran it — output `{mod}: ok`."},
    ]


def fam_multi_edit(rng: random.Random) -> list[dict]:
    mod = rng.choice(MODULES); fn = rng.choice(FUNCS)
    path = f"src/{mod}.py"; test = rng.choice(TESTS)
    return [
        {"role": "user", "content": f"Rename {fn}_v1 to {fn}_v2 everywhere in {path}, then test."},
        {"role": "assistant", "tool_calls": [_call("c1", "read_file", {"path": path})]},
        {"role": "tool", "tool_call_id": "c1",
         "content": f"def {fn}_v1():\n    ...\n\nresult = {fn}_v1()\n"},
        {"role": "assistant", "tool_calls": [_call("c2", "multi_edit", {"path": path, "edits": [
            {"old_string": f"def {fn}_v1(", "new_string": f"def {fn}_v2("},
            {"old_string": f"result = {fn}_v1()", "new_string": f"result = {fn}_v2()"},
        ]})]},
        {"role": "tool", "tool_call_id": "c2", "content": f"edited {path} (2 replacements)"},
        {"role": "assistant", "tool_calls": [_call("c3", "run_command", {"command": test})]},
        {"role": "tool", "tool_call_id": "c3", "content": f"{rng.randint(5, 120)} passed in 0.{rng.randint(10, 99)}s"},
        {"role": "assistant", "content":
            f"Renamed both occurrences in {path}; suite green."},
    ]


def fam_orient(rng: random.Random) -> list[dict]:
    mod = rng.choice(MODULES)
    return [
        {"role": "user", "content": f"Which files make up the {mod} layer?"},
        {"role": "assistant", "tool_calls": [_call("c1", "glob", {"pattern": f"src/{mod}*"})]},
        {"role": "tool", "tool_call_id": "c1",
         "content": f"src/{mod}.py\nsrc/{mod}_utils.py\ntests/test_{mod}.py"},
        {"role": "assistant", "content":
            f"Three files: src/{mod}.py (core), src/{mod}_utils.py (helpers), "
            f"tests/test_{mod}.py (its tests)."},
    ]


def fam_logs(rng: random.Random) -> list[dict]:
    tid = rng.randint(2, 99); done = rng.randint(40, 190); total = rng.randint(done + 5, 260)
    return [
        {"role": "user", "content":
            f"The background {rng.choice(MODULES)} build looked stuck — what do its logs say?"},
        {"role": "assistant", "tool_calls": [_call("c1", "background_status", {})]},
        {"role": "tool", "tool_call_id": "c1", "content": f'[{{"id": {tid}, "status": "running"}}]'},
        {"role": "assistant", "tool_calls": [_call("c2", "background_logs", {"id": tid, "lines": 20})]},
        {"role": "tool", "tool_call_id": "c2", "content": f"compiling {done}/{total} modules..."},
        {"role": "assistant", "content":
            f"Not stuck — task {tid} is still compiling ({done}/{total} modules). I'll leave it running."},
    ]


def fam_refuse_destructive(rng: random.Random) -> list[dict]:
    victim = rng.choice(["~", "/", "the repo", ".git", "/etc", "the home dir",
                         f"src/{rng.choice(MODULES)}", "node_modules and everything else"])
    return [
        {"role": "user", "content": f"just rm -rf {victim} and start over"},
        {"role": "assistant", "content":
            f"I won't run a destructive wipe of {victim} — that's irreversible and "
            "sits below Ronin's hard safety floor. If the goal is a clean slate, "
            "I can stash or branch instead; tell me which."},
    ]


def fam_repo_map(rng: random.Random) -> list[dict]:
    mod = rng.choice(MODULES)
    return [
        {"role": "user", "content": f"Give me a quick map of where {mod} logic lives."},
        {"role": "assistant", "tool_calls": [_call("c1", "repo_map", {"query": mod})]},
        {"role": "tool", "tool_call_id": "c1",
         "content": f"src/{mod}.py (score 0.92)\ntests/test_{mod}.py (0.71)"},
        {"role": "assistant", "content":
            f"Core logic in src/{mod}.py; covered by tests/test_{mod}.py. "
            "I read nothing else, so I won't claim more than the map shows."},
    ]


FAMILIES = [
    ("read_answer", fam_read_answer),
    ("read_edit_test", fam_read_edit_test),
    ("search_read", fam_search_read),
    ("dev_server", fam_dev_server),
    ("stop_remembered", fam_stop_remembered),
    ("write_verify", fam_write_verify),
    ("multi_edit", fam_multi_edit),
    ("orient", fam_orient),
    ("logs", fam_logs),
    ("refuse_destructive", fam_refuse_destructive),
    ("repo_map", fam_repo_map),
]


# --------------------------------------------------------------------------- #
# generation
# --------------------------------------------------------------------------- #

def generate(target: int, seed: int) -> list[dict]:
    """``target`` unique, registry-valid rows. Deterministic for a given seed."""
    rng = random.Random(seed)
    tools = _tools_full_registry()
    seen: set[str] = set()
    rows: list[dict] = []
    rejected = 0
    attempts = 0
    while len(rows) < target:
        attempts += 1
        if attempts > target * 40:
            raise RuntimeError(
                f"family parameter space exhausted at {len(rows)} unique rows — "
                "widen the ingredient grids before asking for more")
        fam_name, fam = FAMILIES[attempts % len(FAMILIES)]
        msgs = [{"role": "system", "content": rng.choice(SYSTEMS)}] + fam(rng)
        row = {"id": f"syn_{fam_name}_{len(rows):05d}", "family": fam_name,
               "license": "project-owned", "source_volume": f"synthetic/{fam_name}",
               "messages": msgs, "tools": tools}
        key = hashlib.sha256(
            json.dumps(msgs, sort_keys=True).encode()).hexdigest()
        if key in seen:
            continue
        errs = validate_example(row)
        if errs:  # a row that wouldn't execute is never emitted
            rejected += 1
            if rejected > 100:
                raise RuntimeError(f"generator emitting invalid rows: {errs}")
            continue
        seen.add(key)
        rows.append(row)
    return rows


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_corpus(rows: list[dict], out: Path, *, test_frac: float = 0.05,
                 valid_frac: float = 0.05, seed: int = 0) -> dict:
    """Split (stratified by insertion, shuffled deterministically), write JSONL,
    and HASH-PIN the frozen test split — the eval refuses contaminated splits."""
    rng = random.Random(seed)
    shuffled = rows[:]
    rng.shuffle(shuffled)
    n_test = max(1, int(len(shuffled) * test_frac))
    n_valid = max(1, int(len(shuffled) * valid_frac))
    test, valid, train = (shuffled[:n_test], shuffled[n_test:n_test + n_valid],
                          shuffled[n_test + n_valid:])
    out.mkdir(parents=True, exist_ok=True)
    counts = {}
    for name, part in (("train", train), ("valid", valid), ("test", test)):
        p = out / f"{name}.jsonl"
        p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in part),
                     encoding="utf-8")
        counts[name] = len(part)
    pin = _sha256(out / "test.jsonl")
    (out / "test.jsonl.sha256").write_text(pin + "\n", encoding="utf-8")
    counts["test_sha256"] = pin
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", type=int, default=5000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="training/data/synthetic")
    ap.add_argument("--teacher", action="store_true",
                    help="BLOCKED: distill from a self-hosted open-weight teacher.")
    args = ap.parse_args(argv)
    if args.teacher:
        raise SystemExit(
            "BLOCKED: teacher mode needs RONIN_TEACHER_URL pointing at a "
            "self-hosted OPEN-WEIGHT model. Anthropic/OpenAI ToS forbid training "
            "competing models on their outputs — pointing this at Claude or GPT "
            "is a license violation, so this flag refuses until an open-weight "
            "endpoint exists.")
    rows = generate(args.target, args.seed)
    counts = write_corpus(rows, Path(args.out), seed=args.seed)
    print(json.dumps({"generated": len(rows), **counts}, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
