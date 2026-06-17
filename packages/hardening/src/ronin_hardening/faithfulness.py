"""Faithfulness / grounding harness for agent answers and proposed file edits.

A sibling safety check to ``injection`` (which guards the *input*): this module
guards the *output*. Given the agent's proposed answer or proposed file edit
plus the sources it actually used (files read via tools, tool outputs, retrieved
context), it asks one question: *is the answer grounded in what the agent
actually saw?*

It does four things, all offline and deterministic (no network, no keys):

1. **Decompose** the answer into atomic claims (sentence/clause segmentation).
2. **Ground** each claim against the union of sources, via lexical overlap on
   content words plus exact code-symbol presence.
3. **Hallucinated-symbol detection** for code: extract referenced symbols
   (identifiers, dotted attribute paths, file paths, function calls) and flag
   any that do not appear in the sources the agent actually read.
4. Produce a **calibrated 0..1 faithfulness score** with a per-claim
   grounded/ungrounded breakdown, and an **abstain** verdict when the evidence
   is too thin to score honestly (few sources, sparse coverage, or a score that
   lands in the uncertain band).

The scoring is intentionally lexical, not model-based: it is cheap, runs in
tests with no provider, and a stubbed "always grounded" implementation fails the
tests because the grounding signal is computed from real overlap with real
sources. For semantic paraphrase that lexical overlap misses, pass an optional
``semantic_judge`` callback (e.g. a sandboxed LLM via Ronin's provider
abstraction); it is consulted only for claims the lexical pass leaves ungrounded,
so the harness degrades gracefully to a fully offline check when none is given.

Hooking the agent loop:

    from ronin_hardening import FaithfulnessHarness, sources_from_trace

    result = agent.run(user_message)          # AgentResult with .trace + .output
    sources = sources_from_trace(result.trace)
    report = FaithfulnessHarness().check(result.output, sources)
    if report.abstain or report.score < 0.5:
        ...  # surface ungrounded claims / ask the agent to cite, or hold the edit

For a proposed edit, score the new code against the files the agent read:

    report = FaithfulnessHarness().check_edit(new_string, sources)
"""
from __future__ import annotations

import re
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Source extraction from an agent trace
# ---------------------------------------------------------------------------

# Tools whose results are evidence the agent actually observed. ``read_file`` is
# the strongest (real file contents); search/list/glob and generic tool output
# also count as observed context. Write/edit results are the agent's own actions,
# not evidence, so they are excluded by default.
_EVIDENCE_TOOLS_DEFAULT: frozenset[str] = frozenset(
    {"read_file", "search_files", "list_files", "glob", "grep", "web_fetch", "fetch"}
)


class Source(BaseModel):
    """One piece of evidence the agent actually had in context.

    ``origin`` is a human-readable label (a tool name, a file path, "retrieved").
    ``text`` is the raw content the agent saw.
    """

    origin: str
    text: str


def sources_from_trace(
    trace: list[Any],
    *,
    evidence_tools: "frozenset[str] | set[str] | None" = None,
    include_all_tools: bool = False,
) -> list[Source]:
    """Pull the sources an agent actually observed out of its execution trace.

    ``trace`` is a list of ``Step`` objects (``AgentResult.trace`` from
    ``ronin_agent_patterns``). We read every ``tool_result`` step whose tool is
    an evidence tool and whose result is not an error. The step ``content`` is
    ``{"name", "result", "is_error"}`` (see the ReAct loop's ``_record``); we are
    tolerant of dict-like or attribute-like steps so callers can pass their own
    shapes.

    Set ``include_all_tools=True`` to treat every non-error tool result as
    evidence (useful when an agent uses custom read-only tools the default set
    does not name).
    """
    allow = (
        frozenset(evidence_tools)
        if evidence_tools is not None
        else _EVIDENCE_TOOLS_DEFAULT
    )
    sources: list[Source] = []
    for step in trace:
        kind = _attr(step, "kind")
        if kind != "tool_result":
            continue
        content = _attr(step, "content")
        if not isinstance(content, dict):
            continue
        if content.get("is_error"):
            continue
        name = content.get("name") or "tool"
        if not include_all_tools and name not in allow:
            continue
        result = content.get("result")
        text = result if isinstance(result, str) else _stringify(result)
        if text and text.strip():
            sources.append(Source(origin=str(name), text=text))
    return sources


def _attr(obj: Any, name: str) -> Any:
    """Read ``name`` from a pydantic model, an object, or a dict — whichever it is."""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _stringify(value: Any) -> str:
    import json

    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)


# ---------------------------------------------------------------------------
# Tokenisation and claim decomposition
# ---------------------------------------------------------------------------

# Words too common to carry grounding signal. Kept small and generic on purpose.
_STOPWORDS: frozenset[str] = frozenset(
    """a an the this that these those is are was were be been being am
    and or but if then else so because as of to in on at by for with from into
    out up down over under again further it its their there here we you they i he
    she them his her our your my me us do does did done has have had having will
    would can could should may might must shall not no nor only own same than too
    very just also which who whom whose what when where why how all any both each
    few more most other some such own about above below between""".split()
)

# File extensions we treat as a real "path" symbol (kept short and code-centric
# so a sentence-ending word + period like "auth." is never a fake path).
_FILE_EXT_RE = (
    r"py|pyi|js|jsx|ts|tsx|go|rs|rb|java|kt|c|h|cpp|hpp|cs|php|swift|"
    r"json|yaml|yml|toml|cfg|ini|md|txt|sh|sql|html|css|tf"
)

# A relative file path (must end in a known code extension), a dotted attribute
# path (``foo.bar.baz``), a call (``do_thing(``), or a bare identifier
# (snake_case, CamelCase, or a Capitalized class-like name). These are the
# "symbols" we ground exactly.
_SYMBOL_RE = re.compile(
    rf"""
    (?P<path>[\w./-]+\.(?:{_FILE_EXT_RE})\b)        # file path: src/foo/bar.py
    | (?P<dotted>[A-Za-z_]\w*(?:\.[A-Za-z_]\w+)+)   # dotted path: a.b.c
    | (?P<call>[A-Za-z_]\w*)\s*\(                    # call: do_thing(
    | (?P<ident>[A-Za-z_]\w*)                        # bare identifier
    """,
    re.VERBOSE,
)

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]+")

# Common English words that look like identifiers but are not code symbols. These
# would otherwise create false "hallucinated symbol" noise.
_SYMBOL_STOPWORDS: frozenset[str] = frozenset(
    """the and for with this that from into your their function method returns
    return value values input output result results should there where which
    because also into using used uses call calls called given takes take then
    these those while when does done make makes made need needs file files
    it its system also here there note first next finally however therefore
    authentication validation configuration implementation""".split()
)


def split_claims(answer: str) -> list[str]:
    """Decompose an answer into atomic claims.

    Splits on sentence boundaries and on hard clause separators (``;``, bullet
    markers, newlines), keeping each fragment that carries a real assertion.
    Code fences are kept whole as a single claim each (so a code block is graded
    as one unit, by its symbols).
    """
    if not answer or not answer.strip():
        return []

    claims: list[str] = []
    # Pull fenced code blocks out first; grade each as one claim.
    parts = re.split(r"(```.*?```)", answer, flags=re.DOTALL)
    for part in parts:
        if not part.strip():
            continue
        if part.startswith("```"):
            claims.append(part.strip())
            continue
        # Prose: split on newlines / bullets first, then sentences.
        for line in re.split(r"[\n\r]+|^\s*[-*]\s+|\s+[-*]\s+", part):
            if not line or not line.strip():
                continue
            for sent in re.split(r"(?<=[.!?;])\s+", line.strip()):
                s = sent.strip(" \t-*•")
                # Keep fragments with at least a few characters and one word.
                if len(s) >= 4 and _WORD_RE.search(s):
                    claims.append(s)
    return claims


def _content_words(text: str) -> set[str]:
    return {
        w.lower()
        for w in _WORD_RE.findall(text)
        if w.lower() not in _STOPWORDS and len(w) > 2
    }


def extract_symbols(text: str) -> set[str]:
    """Extract code-ish symbols (identifiers, dotted paths, file paths, calls).

    Used both for hallucinated-symbol detection and as exact grounding anchors.
    Filters out ordinary English words that masquerade as identifiers.
    """
    symbols: set[str] = set()
    for m in _SYMBOL_RE.finditer(text):
        sym = m.group("path") or m.group("dotted") or m.group("call") or m.group("ident")
        if not sym:
            continue
        low = sym.lower()
        if low in _SYMBOL_STOPWORDS:
            continue
        if _is_symbol_shaped(sym):
            symbols.add(sym)
    return symbols


def _is_symbol_shaped(sym: str) -> bool:
    """True if ``sym`` looks like code, not prose.

    Accepts: dotted paths, file paths, snake_case, internal-capital CamelCase
    (``fooBar``), and Capitalized class-like names (``Session``) that are not
    ordinary sentence-leading words. Rejects bare lowercase words.
    """
    if "." in sym or "/" in sym or "_" in sym:
        return True
    if any(c.isupper() for c in sym[1:]):  # internal capital: makeToken, FooBar
        return True
    # A single Capitalized token (Session, PaymentGateway already covered above):
    # treat as a symbol only if it is not a common sentence word. We keep this
    # permissive but lean on _SYMBOL_STOPWORDS to drop "The", "This", etc.
    if sym[:1].isupper() and len(sym) >= 3:
        return True
    return False


def _symbol_grounded(sym: str, source_symbols: set[str], source_lower: str) -> bool:
    """True if ``sym`` is supported by the sources.

    Grounded when it matches an extracted source symbol, appears as a substring
    of the source text, or (for a dotted path) every component is present. The
    last rule keeps a legitimately-composed attribute access (``session.token``
    built from ``session`` + ``.token`` in the file) from being flagged as a
    fabricated symbol.
    """
    if sym in source_symbols or sym.lower() in source_lower:
        return True
    if "." in sym:
        parts = [p for p in sym.split(".") if p]
        return bool(parts) and all(p.lower() in source_lower for p in parts)
    return False


# ---------------------------------------------------------------------------
# Grounding
# ---------------------------------------------------------------------------


class ClaimVerdict(BaseModel):
    """Grounding verdict for a single atomic claim."""

    claim: str
    grounded: bool
    support: float  # 0..1 strength of lexical support
    method: str  # "lexical" | "symbol" | "semantic" | "trivial"
    ungrounded_symbols: list[str] = Field(default_factory=list)


class FaithfulnessReport(BaseModel):
    """Full faithfulness assessment of an answer against its sources."""

    score: float  # 0..1 calibrated faithfulness
    abstain: bool
    reason: str  # why we abstained, or "ok"
    claims: list[ClaimVerdict] = Field(default_factory=list)
    hallucinated_symbols: list[str] = Field(default_factory=list)
    n_sources: int = 0

    @property
    def grounded_fraction(self) -> float:
        scored = [c for c in self.claims if c.method != "trivial"]
        if not scored:
            return 1.0
        return sum(1 for c in scored if c.grounded) / len(scored)

    @property
    def ungrounded_claims(self) -> list[ClaimVerdict]:
        return [c for c in self.claims if not c.grounded and c.method != "trivial"]


class FaithfulnessHarness(BaseModel):
    """Score how well an answer (or a proposed edit) is grounded in its sources.

    Tunables:
    - ``support_threshold``  — min lexical overlap fraction for a claim to count
      as grounded by content words.
    - ``abstain_low``/``abstain_high`` — a score strictly between these lands in
      the uncertain band and the harness abstains instead of guessing.
    - ``min_sources`` — below this many sources, the harness abstains (it cannot
      ground anything against nothing).
    - ``semantic_judge`` — optional ``(claim, sources_text) -> float in [0,1]``
      callback consulted ONLY for claims the lexical pass leaves ungrounded.
      Keep it offline-safe (Ronin's mock provider) in tests.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    support_threshold: float = 0.6
    abstain_low: float = 0.45
    abstain_high: float = 0.65
    min_sources: int = 1
    min_claims_for_confidence: int = 1
    semantic_judge: Callable[[str, str], float] | None = None
    semantic_threshold: float = 0.6

    def check(self, answer: str, sources: list[Source]) -> FaithfulnessReport:
        """Assess ``answer`` against ``sources`` and return a full report."""
        source_text = "\n".join(s.text for s in sources)
        source_words = _content_words(source_text)
        source_symbols = extract_symbols(source_text)
        # Lowercased raw text lets us catch a symbol that appears as a substring
        # (e.g. ``foo`` inside ``foo_bar``) without tokenising the whole corpus.
        source_lower = source_text.lower()

        claims = split_claims(answer)
        verdicts: list[ClaimVerdict] = []
        all_hallucinated: set[str] = set()

        for claim in claims:
            verdict = self._grade_claim(
                claim, source_words, source_symbols, source_lower, source_text
            )
            verdicts.append(verdict)
            all_hallucinated.update(verdict.ungrounded_symbols)

        report = FaithfulnessReport(
            claims=verdicts,
            hallucinated_symbols=sorted(all_hallucinated),
            n_sources=len(sources),
            score=0.0,
            abstain=False,
            reason="ok",
        )
        report.score = self._score(report)
        self._decide_abstain(report)
        return report

    def check_edit(self, new_code: str, sources: list[Source]) -> FaithfulnessReport:
        """Assess a proposed file edit (the new code) against the read sources.

        Grades the edit primarily by hallucinated-symbol detection: a write that
        references functions / attributes / files absent from everything the agent
        read is the classic ungrounded edit. Prose lines in the snippet are graded
        as claims too.
        """
        return self.check(new_code, sources)

    # -- internals ----------------------------------------------------------

    def _grade_claim(
        self,
        claim: str,
        source_words: set[str],
        source_symbols: set[str],
        source_lower: str,
        source_text: str,
    ) -> ClaimVerdict:
        words = _content_words(claim)
        symbols = extract_symbols(claim)

        # Hallucinated symbols: referenced in the claim, absent from sources.
        # A symbol is grounded if it appears as an exact symbol, as a substring of
        # the source text, or — for a dotted path like ``session.token`` — when
        # every one of its components is present in the sources (the agent read
        # ``session`` and ``.token`` separately and the claim composed them).
        ungrounded_syms = sorted(
            s
            for s in symbols
            if not _symbol_grounded(s, source_symbols, source_lower)
        )

        # Trivial claim: nothing real to ground. No symbols and at most one
        # content word means a header / connective fragment ("Here is the
        # result:", "In summary:") that carries no checkable assertion. It does
        # not count for or against the score.
        if not symbols and len(words) <= 1:
            return ClaimVerdict(
                claim=claim, grounded=True, support=1.0, method="trivial"
            )

        # Symbol grounding: if the claim names symbols and ANY is hallucinated,
        # the claim is ungrounded regardless of lexical overlap.
        if symbols and ungrounded_syms:
            return ClaimVerdict(
                claim=claim,
                grounded=False,
                support=0.0,
                method="symbol",
                ungrounded_symbols=ungrounded_syms,
            )

        # Lexical grounding: fraction of the claim's content words present in the
        # sources. Symbol-only claims (all symbols grounded, no content words)
        # pass on the symbol evidence alone.
        if not words:
            return ClaimVerdict(
                claim=claim, grounded=True, support=1.0, method="symbol"
            )

        # Grounded code symbols are strong anchors: a claim that names real
        # functions / paths / attributes and whose every symbol is present in the
        # sources is well-grounded even when its connective prose ("calls",
        # "returns") is absent from the source text. We credit those symbols by
        # counting them as matched content and ignoring generic prose verbs in
        # the denominator. Pure-prose claims (no symbols) fall back to plain
        # content-word overlap, so this never inflates an unsupported sentence.
        matched = words & source_words
        if symbols:  # all grounded here (ungrounded handled above)
            # The symbols are confirmed in the sources; require only that the
            # claim is *about* them (it is — they were extracted from it) and
            # that its remaining content words don't contradict (no hallucinated
            # symbol). Support reflects how much of the claim is corroborated.
            anchored = len(matched | {s.lower() for s in symbols}) / max(
                len(words | {s.lower() for s in symbols}), 1
            )
            support = max(anchored, len(matched) / len(words))
            if support >= self.support_threshold or matched:
                return ClaimVerdict(
                    claim=claim, grounded=True, support=round(support, 3), method="symbol"
                )

        overlap = len(matched) / len(words)
        if overlap >= self.support_threshold:
            return ClaimVerdict(
                claim=claim, grounded=True, support=round(overlap, 3), method="lexical"
            )

        # Lexical pass left it ungrounded → optional semantic second opinion.
        if self.semantic_judge is not None:
            try:
                sem = float(self.semantic_judge(claim, source_text))
            except Exception:  # noqa: BLE001 — a judge must never break the check
                sem = 0.0
            if sem >= self.semantic_threshold:
                return ClaimVerdict(
                    claim=claim, grounded=True, support=round(sem, 3), method="semantic"
                )

        return ClaimVerdict(
            claim=claim,
            grounded=False,
            support=round(overlap, 3),
            method="lexical",
            ungrounded_symbols=ungrounded_syms,
        )

    def _score(self, report: FaithfulnessReport) -> float:
        """Calibrated 0..1 faithfulness.

        Blends the grounded-claim fraction with a hallucinated-symbol penalty so
        a single fabricated API drags the score down even when prose overlaps.
        With zero scorable claims the score is 0.0 and abstain handles it; we do
        not award a free 1.0 for an empty/trivial answer.
        """
        scored = [c for c in report.claims if c.method != "trivial"]
        if not scored:
            return 0.0
        grounded_frac = sum(1 for c in scored if c.grounded) / len(scored)
        # Penalty grows with the share of claims carrying a hallucinated symbol.
        with_halluc = sum(1 for c in scored if c.ungrounded_symbols)
        halluc_penalty = 0.5 * (with_halluc / len(scored))
        return round(max(0.0, grounded_frac - halluc_penalty), 3)

    def _decide_abstain(self, report: FaithfulnessReport) -> None:
        scored = [c for c in report.claims if c.method != "trivial"]
        if report.n_sources < self.min_sources:
            report.abstain = True
            report.reason = (
                f"too few sources ({report.n_sources} < {self.min_sources}); "
                "cannot ground the answer"
            )
            return
        if len(scored) < self.min_claims_for_confidence:
            report.abstain = True
            report.reason = "no scorable claims in the answer"
            return
        if self.abstain_low < report.score < self.abstain_high:
            report.abstain = True
            report.reason = (
                f"score {report.score} is in the uncertain band "
                f"({self.abstain_low}..{self.abstain_high})"
            )
            return
        report.abstain = False
        report.reason = "ok"
