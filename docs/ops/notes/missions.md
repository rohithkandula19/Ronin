# Missions

Missions are the unit of work Ronin runs against a repo or workspace.

- Keep mission prompts in-repo; keep credentials out of mission files.
- Record success/failure and the doctor snapshot used at start.
- Prefer small, restartable missions over one long unattended run.
- Do not commit generated mission artifacts that include secrets or PII.
