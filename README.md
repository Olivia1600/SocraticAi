# Socratic AI Tutor

A spec-driven, local-first Socratic tutoring platform built with **FastAPI**, **SQLite**, and a split-screen browser UI.

## Architecture

- `socratic_ai/db.py` — WAL-enabled SQLite persistence for users, textbooks, concept trees, typed chunks, Elo mastery, sessions, practice items, and assignments.
- `socratic_ai/auth.py` — PBKDF2-SHA256 registration/login, signed session cookies, and `student` / `teacher` role enforcement.
- `socratic_ai/rag.py` — PDF ingestion (`pypdf`), chapter/section concept dependency graph extraction, content-type tagging (`definition`, `theorem`, `worked_example`, `exercise`, `narrative`), and hybrid TF-IDF + BM25 retrieval with page citations.
- `socratic_ai/tutor.py` — Four-stage Socratic state machine (`diagnose_turn` -> `update_state_and_mastery` -> `plan_pedagogical_move` with 3-turn stuck prerequisite step-back -> `guard_socratic_response` answer-leakage evaluator).
- `socratic_ai/practice.py` — Elo rating engine (baseline `1000`, ZPD `elo + 50`, server-only `hidden_solution_steps`, hint-weighted deltas, misconception tagging).
- `socratic_ai/analytics.py` — Teacher class mastery heatmap, aggregated misconception clusters, intervention alerts, and targeted practice assignment distribution.
- `static/` — Split-screen PDF + Socratic tutor workspace, progress view, adaptive practice dialog, and teacher dashboard.

## Quick Start

```powershell
python -m pip install -r requirements.txt
uvicorn socratic_ai.main:app --reload
```

Open `http://127.0.0.1:8000`.

### Optional LLM Enhancement

The app runs deterministically offline by default. To enable LLM-augmented diagnosis, Socratic phrasing, and practice generation, set either:

```powershell
$env:GEMINI_API_KEY="your-key"
# or
$env:OPENAI_API_KEY="your-key"
```

## Verification

```powershell
python -m pytest -v
```
