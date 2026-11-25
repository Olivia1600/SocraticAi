# Proposal

## Why

Socratic AI needs a clean, spec-driven foundation from scratch because the previous prototype mixed Flask routing, brittle keyword-frequency concept guessing, single-turn prompt templates, and UI state without automated tests or clean architectural boundaries. Rebuilding on FastAPI, SQLite, and a structured four-layer tutoring architecture ensures textbook concepts, Socratic guardrails, Elo-based mastery tracking, and classroom analytics evolve reliably under living OpenSpec specifications.

## What Changes

- Introduce a modular **FastAPI + SQLite + browser UI** application built around four decoupled backend layers (`ingestion/rag`, `tutor`, `mastery`, `analytics`) and a clean split-screen student & teacher web interface.
- Add role-based authentication (`student`, `teacher`) with secure password hashing and session cookies.
- Add textbook PDF ingestion with hierarchical concept graph extraction, typed chunk metadata (`definition`, `worked_example`, `theorem`, `exercise`), and hybrid retrieval with source citations.
- Add a stateful Socratic tutoring engine (`Diagnosis -> Mastery/State Update -> Pedagogical Planner -> Response Guard`) that prevents direct answer leakage and steps back to prerequisite concepts when learners are repeatedly stuck.
- Add Elo-based per-student, per-concept mastery tracking (`elo_score`, `hints_used_avg`, `status`) and calibrated adaptive practice generation with hidden solution steps and misconception tagging.
- Add a teacher class analytics view featuring a Student x Concept mastery heatmap, top misconception clusters, intervention alerts, and curated practice assignment pushing.

## Capabilities

### New Capabilities

- `user-auth`: Registration, login, session persistence, logout, and role-based access control for students and teachers within classes.
- `textbook-rag`: PDF upload, heading/section-aware chunking, concept dependency graph extraction, content-type metadata tagging, and targeted hybrid retrieval with citations.
- `socratic-tutor`: Stateful multi-turn Socratic dialogue loop (diagnosis, scaffolding state, pedagogical move selection, answer-leakage response guard, and prerequisite step-back) with split-screen textbook context.
- `adaptive-practice`: Elo mastery rating per `(student, concept)`, Zone of Proximal Development practice question generation with hidden solution steps, answer evaluation, and misconception logging.
- `teacher-analytics`: Teacher-scoped class mastery heatmap, aggregated weekly misconception clusters, automated intervention alerts, and one-click targeted practice assignment distribution.

### Modified Capabilities

<!-- None: greenfield baseline -->

## Impact

- Creates the initial Python FastAPI application package, SQLite schema and migrations, static frontend (`HTML/CSS/JS`), dependency manifest (`requirements.txt`), and automated `pytest` suite.
- Runs locally on Windows and POSIX with zero mandatory external services (supports optional LLM API keys via environment configuration with deterministic local fallbacks).
