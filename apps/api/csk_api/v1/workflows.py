"""Education & Healthcare world workflows.

These are DETERMINISTIC, grounded workflows: they build structured artifacts
from the user's own uploaded text, so they run with zero credentials and never
fabricate. A model-enhanced variant would swap the deterministic builder for a
provider call behind the same interface (BLOCKED_CREDENTIALS here) — but the
grounding, disclosures, and structure are the product contract and are tested.
"""

from __future__ import annotations

import re

# The disclosures a Healthcare Information output must always carry (mirrors
# industry-packs/healthcare/policies/healthcare-information.yaml).
HEALTHCARE_DISCLOSURES = {
    "informational_purpose": (
        "This is educational information, not medical advice, and not a diagnosis."
    ),
    "professional_review": (
        "A qualified clinician should review anything here before you act on it."
    ),
    "emergency_boundary": (
        "If this may be an emergency, contact your local emergency services now."
    ),
}


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _keywords(text: str, k: int = 8) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z-]{3,}", text.lower())
    stop = {"the", "and", "that", "with", "from", "this", "have", "will", "your",
            "about", "which", "these", "their", "into", "when", "then", "than"}
    freq: dict[str, int] = {}
    for w in words:
        if w not in stop:
            freq[w] = freq.get(w, 0) + 1
    return [w for w, _ in sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))[:k]]


# --------------------------------------------------------------- education

def build_study_plan(*, topic: str, goal: str, notes: str, weeks: int = 3) -> dict:
    """A source-grounded study plan artifact body. Grounded in the user's notes."""
    if not notes.strip():
        raise ValueError("study plan requires uploaded notes to ground against")
    concepts = _keywords(notes, k=max(weeks * 2, 6))
    per_week = max(1, len(concepts) // weeks)
    plan = []
    for w in range(weeks):
        chunk = concepts[w * per_week : (w + 1) * per_week] or concepts[-per_week:]
        plan.append({"week": w + 1, "focus": chunk,
                     "activity": f"Review notes on {', '.join(chunk)}; self-test."})
    return {
        "topic": topic,
        "goal": goal,
        "grounded_in": "user notes",  # not invented content
        "source_excerpt": notes[:200],
        "weeks": plan,
        "integrity_note": (
            "This plan supports your learning; it is not work to submit as your own."
        ),
    }


def build_quiz(*, notes: str, n: int = 3) -> dict:
    """A quiz whose questions/answers derive from the notes (answer key grounded)."""
    sentences = _sentences(notes)
    if not sentences:
        raise ValueError("quiz requires notes")
    items = []
    for s in sentences[:n]:
        kws = _keywords(s, k=1)
        answer = kws[0] if kws else s.split()[0]
        question = s.replace(answer, "_____", 1) if answer in s else f"Fill in: {s}"
        items.append({"question": question, "answer": answer, "from_sentence": s})
    return {"items": items, "grounded_in": "user notes"}


# -------------------------------------------------------------- healthcare

def build_health_summary(*, document_text: str, country: str = "US") -> dict:
    """An informational summary of an uploaded doc, with mandatory disclosures.

    Never diagnoses. Surfaces what is present, what is uncertain/missing, and
    always carries the informational/professional-review/emergency disclosures.
    """
    if not document_text.strip():
        raise ValueError("summary requires an uploaded document")
    sentences = _sentences(document_text)
    present = sentences[:5]
    # Deterministically flag "uncertain/missing" rather than assert certainty.
    missing = []
    for field in ("date", "dosage", "provider", "follow-up"):
        if field not in document_text.lower():
            missing.append(field)
    return {
        "informational_purpose": HEALTHCARE_DISCLOSURES["informational_purpose"],
        "country": country,
        "summary_points": present,
        "uncertain_or_missing": missing,
        "sources": [{"kind": "uploaded_document", "note": "your document (no external source retrieved)"}],
        "appointment_questions": [
            f"Can you explain what '{present[0][:60]}' means for me?" if present else
            "Can you walk me through this document?",
            "What follow-up or tests do you recommend?",
        ],
        "professional_review": HEALTHCARE_DISCLOSURES["professional_review"],
        "emergency_boundary": HEALTHCARE_DISCLOSURES["emergency_boundary"],
    }


def refuses_diagnosis(prompt: str) -> bool:
    """True if a healthcare prompt is a diagnosis/treatment request we must refuse."""
    p = prompt.lower()
    triggers = ("what is my diagnosis", "do i have", "should i take", "should i stop",
                "double my dose", "increase my dose", "what should i do about my chest pain")
    return any(t in p for t in triggers)
