"""Hosted csk SaaS — FastAPI backend.

Exposes:
- POST /signup            — create a user, get an API key
- POST /connections       — store an encrypted service credential
- GET  /connections       — list configured services (names only, no creds returned)
- POST /briefings         — run the briefing for the calling user
- GET  /briefings         — list past briefings
- POST /briefings/schedule — enable weekly briefing (cron worker picks it up)

Web dashboard (ronin ui), read-only + self-contained + offline:
- GET  /                  : the single self-contained dashboard HTML page
- GET  /ui/runs           : recent runs (orchestrations + chat sessions)
- GET  /ui/runs/{id}      : a run's planner to sub-agent tree + faithfulness scores
- GET  /ui/memory         : durable memory entries
- GET  /ui/skills         : crystallized repo-local skills
"""
__version__ = "0.0.1"
