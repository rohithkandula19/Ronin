"""Demo mode: clearly-labeled fixtures, no real credentials, no external claims.

Every demo artifact is tagged demo=True in its content so the UI can badge it
and nothing here is ever mistaken for real user data. Demo mode is opt-in via
the RONIN_DEMO env flag or the /api/v1/demo/seed endpoint.
"""

from __future__ import annotations

# A synthetic healthcare document containing NO real personal information.
DEMO_HEALTHCARE_DOC = (
    "Visit note (DEMO, synthetic). Patient reports mild seasonal symptoms. "
    "Blood pressure recorded within normal range. Advised routine hydration and rest. "
    "No medications changed at this visit."
)

DEMO_STUDY_NOTES = (
    "Photosynthesis converts light energy into chemical energy. Chlorophyll absorbs "
    "light in the chloroplast. The Calvin cycle fixes carbon dioxide into glucose. "
    "Oxygen is released as a byproduct."
)

DEMO_CODING_DATASET = [
    {
        "id": "demo-1",
        "instruction": "Read the config loader before describing it.",
        "output": "I'll read the file first, then explain how it resolves settings.",
        "source_type": "human_reviewed",
        "license": "owner",
        "consent": "owner_self",
        "redaction": "clean_reviewed",
        "industry": "coding",
    },
]


def demo_banner() -> dict:
    return {
        "demo_mode": True,
        "notice": "Demo data is synthetic and clearly labeled. No real accounts, "
                  "no external actions, no credentials are used.",
    }
