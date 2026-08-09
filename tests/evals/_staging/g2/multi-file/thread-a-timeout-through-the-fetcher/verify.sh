#!/usr/bin/env bash
# Checks that a per-call `timeout` was threaded through every layer -- client,
# retry helper, base transport, the fake transport and the caching wrapper -- and
# that the 10s default survived.
# usage: verify.sh [workspace-root]   (defaults to the current directory)
set -u
ROOT="${1:-$PWD}"
cd "$ROOT" 2>/dev/null || { echo "FAIL: workspace root '$ROOT' is not a directory"; exit 1; }
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  if command -v python3 >/dev/null 2>&1; then PY=python3; else PY=python; fi
fi

"$PY" - <<'CHECK'
import inspect
import sys

sys.path.insert(0, ".")


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    raise SystemExit(1)


def load(name):
    try:
        __import__(name)
    except Exception as exc:  # noqa: BLE001
        fail(f"`import {name}` raised {type(exc).__name__}: {exc}")
    return sys.modules[name]


fetchkit = load("fetchkit")
transport_mod = load("fetchkit.transport")
cached_mod = load("fetchkit.backends.cached")
Client, Response = fetchkit.Client, fetchkit.Response
FakeTransport, CachingTransport = fetchkit.FakeTransport, fetchkit.CachingTransport

if transport_mod.DEFAULT_TIMEOUT != 10.0:
    fail(f"DEFAULT_TIMEOUT is {transport_mod.DEFAULT_TIMEOUT!r}; the 10 second default was supposed to stay")

sites = [
    ("fetchkit/client.py", "Client.request", Client.request),
    ("fetchkit/client.py", "Client.get", Client.get),
    ("fetchkit/client.py", "Client.post", Client.post),
    ("fetchkit/retry.py", "get_with_retry", fetchkit.get_with_retry),
    ("fetchkit/transport.py", "Transport.send", transport_mod.Transport.send),
    ("fetchkit/transport.py", "FakeTransport.send", FakeTransport.send),
    ("fetchkit/backends/cached.py", "CachingTransport.send", cached_mod.CachingTransport.send),
]
for where, what, func in sites:
    params = inspect.signature(func).parameters
    if "timeout" not in params:
        fail(f"{what} ({where}) still takes {tuple(params)} -- it never got a timeout parameter")

def fake():
    return FakeTransport(
        responses={"/things": Response(200, "[]"), "/slow": Response(200, "late")},
        latency={"/slow": 1.0, "/glacial": 60.0},
    )

t = fake()
client = Client(t)
try:
    tight = client.get("/slow", timeout=0.5)
except TypeError as exc:
    fail(f"client.get('/slow', timeout=0.5) raised TypeError: {exc}")
if tight.status != 504:
    fail(f"with timeout=0.5 a 1.0s path returned {tight.status}, expected 504 -- the timeout never reached the transport")
if client.get("/slow", timeout=5.0).status != 200:
    fail("with timeout=5.0 a 1.0s path should succeed, but it did not")
seen = [entry["timeout"] for entry in t.log]
if seen != [0.5, 5.0]:
    fail(f"the transport recorded timeouts {seen!r}, expected [0.5, 5.0]")

t = fake()
if Client(t).get("/slow").status != 200:
    fail("a plain client.get('/slow') should still use the 10s default and succeed")
if t.log[-1]["timeout"] != 10.0:
    fail(f"with no timeout given the transport saw {t.log[-1]['timeout']!r}, expected the 10.0 default")
t = fake()
if Client(t).post("/things", timeout=2.5).status != 200:
    fail("client.post does not accept a timeout")
if t.log[-1]["timeout"] != 2.5:
    fail(f"client.post passed {t.log[-1]['timeout']!r} to the transport instead of 2.5")

inner = fake()
wrapped = Client(CachingTransport(inner))
try:
    first = wrapped.get("/slow", timeout=0.25)
except TypeError as exc:
    fail(f"a timeout through CachingTransport raised TypeError: {exc} -- the wrapper's send() was not updated")
if first.status != 504:
    fail(f"through CachingTransport a 1.0s path with timeout=0.25 returned {first.status}, expected 504")
if inner.log[-1]["timeout"] != 0.25:
    fail(f"CachingTransport handed {inner.log[-1]['timeout']!r} to the inner transport instead of 0.25")
if wrapped.get("/slow", timeout=5.0).status != 200:
    fail("through CachingTransport a generous timeout should succeed")

inner = fake()
try:
    result = fetchkit.get_with_retry(Client(inner), "/glacial", attempts=2, timeout=0.25)
except TypeError as exc:
    fail(f"get_with_retry(..., timeout=0.25) raised TypeError: {exc} -- the retry helper was not updated")
if result.status != 504:
    fail(f"get_with_retry on a 60s path with timeout=0.25 returned {result.status}, expected 504")
if [entry["timeout"] for entry in inner.log] != [0.25, 0.25]:
    fail(f"get_with_retry passed {[e['timeout'] for e in inner.log]!r} across its attempts, expected [0.25, 0.25]")
CHECK
rc=$?
[ "$rc" -eq 0 ] || exit "$rc"

if [ ! -f tests/test_client.py ]; then
  echo "FAIL: tests/test_client.py is missing -- it was supposed to be updated, not deleted"
  exit 1
fi
out=$("$PY" -m unittest tests.test_client 2>&1)
if [ $? -ne 0 ]; then
  reason=$(printf '%s\n' "$out" | grep -E '^[A-Za-z_.]*(Error|FAILED|ERROR)' | head -1)
  echo "FAIL: tests/test_client.py does not pass -- its own Transport subclass still has the old send() signature (${reason:-see unittest output})"
  exit 1
fi
ran=$(printf '%s\n' "$out" | grep -oE '^Ran [0-9]+ test' | grep -oE '[0-9]+' | head -1)
if [ "${ran:-0}" -lt 9 ]; then
  echo "FAIL: expected the 9 existing tests in tests/test_client.py to still run, unittest ran ${ran:-0}"
  exit 1
fi
exit 0
