# Design

## Context

See [proposal.md](proposal.md) for motivation and capability boundaries.
This is a greenfield rebuild of Socratic AI constrained to Python 3.11+, FastAPI, SQLite, and a lightweight browser UI served directly by FastAPI on Windows and POSIX with zero mandatory external services.

## Goals / Non-Goals

**Goals:**
- Clean four-layer separation: `ingestion/rag`, `tutor` (state machine + response guard), `mastery` (Elo + adaptive practice), and `analytics` (teacher intelligence).
- Deterministic, testable offline execution by default (pure Python + `pypdf` + `scikit-learn` / BM25-TF-IDF + rule-based Socratic planner and leakage guard), with optional LLM enhancement via an abstracted provider interface when `GEMINI_API_KEY` or `OPENAI_API_KEY` is set.
- Full persistence of concept graphs, chunk metadata, Elo scores, tutoring session states, misconception tags, and teacher assignments in SQLite.
- Responsive split-screen student workspace (PDF source & citations on left, Socratic dialogue & practice on right) and teacher analytics dashboard.

**Non-Goals:**
- Distributed infrastructure (PostgreSQL, Redis, Qdrant, Docker, or Celery workers).
- Separate Node/React build pipelines or SSR frameworks.
- Multi-tenant organization billing or OAuth/SSO providers in v1.

## Decisions

### 1. FastAPI + SQLite + Modular Package Layout
- **Decision**: Organize backend into `socratic_ai/` (`db.py`, `auth.py`, `rag.py`, `tutor.py`, `practice.py`, `analytics.py`, `llm.py`, `main.py`) with `static/` served by FastAPI and `tests/` using `pytest` + `fastapi.testclient`.
- **Rationale**: Keeps local setup to `pip install -r requirements.txt` and `uvicorn socratic_ai.main:app`, while avoiding monolithic single-file coupling.
- **Alternative considered**: Flask (lacks native Pydantic request/response contracts and OpenAPI schemas) or Next.js + Postgres (adds heavy build/runtime dependencies).

### 2. Local-First Hybrid Provider (`llm.py`)
- **Decision**: Encapsulate LLM calls (concept graph refinement, diagnosis, Socratic response drafting, answer-leakage check, practice generation) behind a provider interface that uses `GEMINI_API_KEY` / `OPENAI_API_KEY` if configured, and deterministically falls back to structured NLP/TF-IDF + rule-based pipelines when offline or unconfigured.
- **Rationale**: Ensures out-of-the-box local usability, fast deterministic `pytest` tests, and seamless upgrade to frontier LLM reasoning when keys are provided.

### 3. Hierarchical Concept & Typed Chunk Ingestion (`rag.py`)
- **Decision**: Parse PDFs page-by-page using `pypdf`, detect chapter/section headings via regex and line heuristics, infer `content_type` (`definition`, `theorem`, `worked_example`, `exercise`, `narrative`), and construct a per-book concept tree (`concepts.parent_concept_id`). Retrieval combines TF-IDF cosine similarity and BM25-style term weighting with optional `content_type` filtering.
- **Rationale**: Replaces brittle word-frequency labels with meaningful textbook structure and enables prerequisite step-backs without requiring external LlamaParse or vector DB servers.

### 4. Explicit 4-Stage Socratic State Machine (`tutor.py`)
- **Decision**: Every `/api/chat` turn executes four explicit stages:
  1. `diagnose_turn`: classifies `correct | partial | misconception | stuck | off_topic` and extracts `misconception_tags`.
  2. `update_state_and_mastery`: updates consecutive `stuck_count`, `scaffolding_level` (1..4), and logs turn telemetry.
  3. `plan_pedagogical_move`: picks `probe | hint | counter_example | prerequisite_step_back` (triggering step-back to `parent_concept_id` after 3 consecutive `stuck` turns).
  4. `draft_and_guard_response`: generates the Socratic question + textbook citation and runs an `AnswerLeakageGuard` check that strips/rewrites direct numerical or definitional spoilers.
- **Rationale**: Enforces Socratic pedagogy structurally rather than relying on a single fragile prompt.

### 5. Elo Mastery + Hidden-Step Practice (`practice.py`)
- **Decision**: Store `(student_id, concept_id)` Elo initialized at `1000` (status bands: `<900` struggling, `900-1150` developing, `>1150` mastered, or `not_started` when `attempts_count == 0`). Practice items store `hidden_solution_steps` and `common_misconceptions` JSON in SQLite, excluded from `GET`/`POST` student question payloads.
- **Rationale**: Calibrated ZPD difficulty (`elo_score + 50`) gives meaningful signal for both adaptive questions and the teacher heatmap.

### 6. Signed Session Cookies via `itsdangerous` / HMAC
- **Decision**: Use FastAPI `SessionMiddleware` (backed by `itsdangerous` or HMAC-signed cookie) with `SOCRATIC_SECRET_KEY` environment variable and `werkzeug` / `passlib` PBKDF2-SHA256 password hashing.
- **Rationale**: Zero extra session store required while keeping cookies tamper-proof.

## Risks / Trade-offs

- **Offline heuristic accuracy vs. LLM mode** -> The deterministic fallback for concept extraction, diagnosis, and leakage detection uses structural heuristics and keyword overlap, which is less nuanced than an LLM. Mitigation: Design clean interfaces in `llm.py` so plugging in Gemini/OpenAI improves quality without changing API contracts or DB schema.
- **PDF formatting variance** -> Scanned PDFs without embedded text yield empty pages. Mitigation: Detect low-text extraction cleanly on upload, return actionable `400` error, and delete orphaned uploads.
- **SQLite write concurrency** -> Concurrent requests could hit SQLite lock contention. Mitigation: Enable `WAL` mode and short-lived context-managed connections with foreign keys enabled.
