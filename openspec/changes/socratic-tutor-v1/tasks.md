# Tasks

## 1. Project Foundation & Database Schema

- [x] 1.1 Create `.gitignore`, `requirements.txt` (FastAPI, uvicorn, pypdf, scikit-learn, itsdangerous, python-multipart, pytest, httpx), and `socratic_ai/` package skeleton, verifying `pip install -r requirements.txt` succeeds and `uvicorn socratic_ai.main:app` imports cleanly.
- [x] 1.2 Implement `socratic_ai/db.py` with WAL-enabled SQLite connection management and schema initialization (`users`, `textbooks`, `concepts`, `chunks`, `student_concept_mastery`, `tutoring_sessions`, `tutoring_messages`, `practice_items`, `assignments`) and verify schema creation and foreign key constraints pass in a `pytest` unit test.

## 2. Authentication & Role Authorization

- [ ] 2.1 Implement `socratic_ai/auth.py` with PBKDF2-SHA256 password hashing, registration, login, session inspection, logout, and FastAPI role dependencies (`student`, `teacher`) and verify all `user-auth` spec scenarios pass with `pytest tests/test_auth.py`.

## 3. Textbook PDF Ingestion & Concept-Aware RAG

- [ ] 3.1 Implement `socratic_ai/llm.py` provider interface with deterministic local fallback and optional `GEMINI_API_KEY` / `OPENAI_API_KEY` adapter, verifying unit tests cover offline fallback behavior.
- [ ] 3.2 Implement `socratic_ai/rag.py` for PDF upload validation, heading/chapter/section parsing, `content_type` classification (`definition`, `theorem`, `worked_example`, `exercise`, `narrative`), hierarchical `concepts` extraction (`parent_concept_id`), and hybrid TF-IDF/BM25 retrieval with citations, verifying API and unit tests pass in `pytest tests/test_rag.py`.

## 4. Socratic State Machine & Answer-Leakage Guard

- [ ] 4.1 Implement `socratic_ai/tutor.py` four-stage pipeline (`diagnose_turn`, `update_state_and_mastery`, `plan_pedagogical_move` including 3-turn stuck prerequisite step-back, and `AnswerLeakageGuard`) plus session/message persistence endpoints, verifying all `socratic-tutor` scenarios pass in `pytest tests/test_tutor.py`.

## 5. Adaptive Practice & Elo Mastery Engine

- [ ] 5.1 Implement `socratic_ai/practice.py` Elo rating engine (default `1000`, status bands, hint-weighted deltas, floor `600`), ZPD practice generation (`elo_score + 50`) storing `hidden_solution_steps` and `common_misconceptions` server-side, and misconception-logging answer evaluator, verifying all `adaptive-practice` scenarios pass in `pytest tests/test_practice.py`.

## 6. Teacher Analytics & Assignment Distribution

- [ ] 6.1 Implement `socratic_ai/analytics.py` for class-scoped Student x Concept mastery heatmap, aggregated weekly misconception clusters, intervention alerts (`hints_used_avg > 2.5` or consecutive Elo drops), and one-click curated practice assignment creation, verifying all `teacher-analytics` scenarios pass in `pytest tests/test_analytics.py`.

## 7. Split-Screen Student & Teacher Web UI

- [ ] 7.1 Build `static/index.html`, `static/styles.css`, and `static/app.js` with auth screen, split-screen student workspace (embedded PDF/citation viewer + Socratic dialogue + adaptive practice dialog + mastery view + assigned practice list), and teacher dashboard (heatmap grid, misconception clusters, intervention alerts, assignment builder), verifying DOM/API integration in `pytest tests/test_e2e.py` and documenting local startup in `README.md`.
