"""Offline task engine.

Used when the model planner is missing or returns nothing. It only splits and
labels the task. It does not call a provider.
"""
from __future__ import annotations

import re

from .plan_cache import Plan

_SPLIT = re.compile(r"\s+(?:then|and then|after that)\s+|;\s+|\n+|^\s*\d+[.)]\s+", re.IGNORECASE | re.MULTILINE)
_RISK = re.compile(r"\b(delete|drop|migrate|deploy|publish|secret|credential|production|force)\b", re.I)
_TEST = re.compile(r"\b(test|pytest|assert|spec)\b", re.I)
_REVIEW = re.compile(r"\b(review|check|verify|audit)\b", re.I)
_DOCS = re.compile(r"\b(doc|readme|changelog|comment)\b", re.I)


def role_for(step: str) -> str:
    if _TEST.search(step):
        return "test"
    if _REVIEW.search(step):
        return "review"
    if _DOCS.search(step):
        return "docs"
    return "implement"


def risk_for(step: str) -> str:
    return "high" if _RISK.search(step) else "low"


def decompose(task: str) -> Plan:
    """Turn a plain task into a plan with labeled steps."""
    text = " ".join(task.split())
    if not text:
        return Plan(goal="", steps=[])
    raw = [part.strip(" .-") for part in _SPLIT.split(task) if part.strip(" .-")]
    if not raw:
        raw = [text]
    steps: list[str] = []
    seen: set[str] = set()
    for part in raw:
        key = " ".join(part.lower().split())
        if key in seen:
            continue
        seen.add(key)
        steps.append(f"[{role_for(part)}/{risk_for(part)}] {part}")
    goal = text if len(text) <= 160 else text[:157] + "..."
    return Plan(goal=goal, steps=steps)


def pack(steps: list[str], budget: int) -> list[str]:
    """Keep a prefix of steps that fits in ``budget`` characters."""
    if budget < 0:
        raise ValueError("budget must be >= 0")
    kept: list[str] = []
    used = 0
    for step in steps:
        extra = len(step) + (1 if kept else 0)
        if kept and used + extra > budget:
            break
        if not kept and len(step) > budget:
            return [step[:budget]]
        kept.append(step)
        used += extra
    return kept
