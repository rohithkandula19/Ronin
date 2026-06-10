from __future__ import annotations

import re
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_PATTERNS: list[tuple[str, str]] = [
    (r"ignore (all |the )?previous (instructions|prompts|context)", "instruction-override"),
    (r"forget (all |everything|your) (instructions|context)", "instruction-override"),
    (r"disregard (the |your )?(system|prior) (prompt|message|instructions)", "instruction-override"),
    (r"you are now (a |an )?", "persona-hijack"),
    (r"new (system|role|instructions):", "role-injection"),
    (r"<\|(im_start|im_end|system|assistant)\|>", "chat-template-injection"),
    (r"\[\[system\]\]|\[system\]", "fake-system-tag"),
    (r"reveal (your |the )?(system )?prompt", "prompt-extraction"),
    (r"print (your |the )?(system )?prompt", "prompt-extraction"),
    (r"tell me (your |the )?(system )?prompt", "prompt-extraction"),
    (r"show me (your |the )?(instructions|prompt)", "prompt-extraction"),
    (r"what (are|is) your (system )?(prompt|instructions)", "prompt-extraction"),
]


class ScanResult(BaseModel):
    """Result of an injection scan. ``flagged`` is the actionable bit; ``hits`` is the audit log."""

    flagged: bool
    hits: list[dict[str, Any]] = Field(default_factory=list)
    score: float = 0.0  # 0.0 = clean, 1.0 = certain injection


class InjectionScanner(BaseModel):
    """Scan untrusted input for prompt-injection patterns.

    Combines regex patterns (cheap, deterministic) with an optional LLM classifier
    (slower, catches novel attacks). Use both layers for defense-in-depth.

    For high-risk input, also use the *dual-LLM* pattern: keep the untrusted text
    out of the agent's planner LLM, run it through a sandboxed reader LLM first.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    patterns: list[tuple[str, str]] = Field(default_factory=lambda: list(DEFAULT_PATTERNS))
    llm_classifier: Callable[[str], float] | None = None
    llm_threshold: float = 0.6

    def scan(self, text: str) -> ScanResult:
        hits: list[dict[str, Any]] = []
        for pattern, label in self.patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                hits.append({"label": label, "pattern": pattern, "match": match.group(0)})

        score = min(1.0, 0.4 * len(hits))
        if self.llm_classifier is not None:
            llm_score = float(self.llm_classifier(text))
            score = max(score, llm_score)
            if llm_score >= self.llm_threshold:
                hits.append({"label": "llm-classifier", "score": round(llm_score, 3)})

        return ScanResult(flagged=bool(hits) or score >= self.llm_threshold, hits=hits, score=round(score, 3))


SYSTEM_PROMPT_LEAK_PATTERNS: list[str] = [
    r"You are an? .{0,40} assistant",
    r"Your name is",
    r"system prompt:",
    r"following instructions:",
]


class OutputLeakScanner(BaseModel):
    """Detect possible system-prompt or PII leakage in agent output.

    A pragmatic last line of defense — patterns are heuristic, not exhaustive.
    Combine with output validation for hard contracts.
    """

    leak_patterns: list[str] = Field(default_factory=lambda: list(SYSTEM_PROMPT_LEAK_PATTERNS))
    forbidden_substrings: list[str] = Field(default_factory=list)

    def scan(self, output: str) -> ScanResult:
        hits: list[dict[str, Any]] = []
        for pattern in self.leak_patterns:
            for match in re.finditer(pattern, output, re.IGNORECASE):
                hits.append({"label": "system-prompt-leak", "pattern": pattern, "match": match.group(0)})
        for needle in self.forbidden_substrings:
            if needle and needle in output:
                hits.append({"label": "forbidden-substring", "match": needle})
        return ScanResult(flagged=bool(hits), hits=hits, score=min(1.0, 0.5 * len(hits)))
