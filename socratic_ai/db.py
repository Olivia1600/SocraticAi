"""SQLite persistence layer for Socratic AI."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Iterator

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "socratic.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('student', 'teacher')),
    class_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_name_lower
    ON users (lower(name));

CREATE TABLE IF NOT EXISTS textbooks (
    book_id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    title TEXT NOT NULL,
    file_path TEXT NOT NULL,
    uploaded_by TEXT REFERENCES users(user_id) ON DELETE SET NULL,
    page_count INTEGER NOT NULL DEFAULT 0,
    uploaded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS concepts (
    concept_id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL REFERENCES textbooks(book_id) ON DELETE CASCADE,
    parent_concept_id TEXT REFERENCES concepts(concept_id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    chapter_number INTEGER NOT NULL DEFAULT 1,
    section_label TEXT NOT NULL DEFAULT '',
    order_index INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL REFERENCES textbooks(book_id) ON DELETE CASCADE,
    concept_id TEXT REFERENCES concepts(concept_id) ON DELETE SET NULL,
    chapter TEXT NOT NULL DEFAULT '',
    section TEXT NOT NULL DEFAULT '',
    page_number INTEGER NOT NULL DEFAULT 1,
    content_type TEXT NOT NULL CHECK(
        content_type IN ('definition', 'theorem', 'worked_example', 'exercise', 'narrative')
    ),
    text TEXT NOT NULL,
    chunk_index INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS student_concept_mastery (
    student_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    concept_id TEXT NOT NULL REFERENCES concepts(concept_id) ON DELETE CASCADE,
    elo_score INTEGER NOT NULL DEFAULT 1000,
    attempts_count INTEGER NOT NULL DEFAULT 0,
    hints_used_total INTEGER NOT NULL DEFAULT 0,
    hints_used_avg REAL NOT NULL DEFAULT 0.0,
    consecutive_drops INTEGER NOT NULL DEFAULT 0,
    last_elo_delta INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'not_started' CHECK(
        status IN ('not_started', 'struggling', 'developing', 'mastered')
    ),
    last_practiced TEXT,
    PRIMARY KEY (student_id, concept_id)
);

CREATE TABLE IF NOT EXISTS tutoring_sessions (
    session_id TEXT PRIMARY KEY,
    student_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    book_id TEXT REFERENCES textbooks(book_id) ON DELETE CASCADE,
    concept_id TEXT REFERENCES concepts(concept_id) ON DELETE SET NULL,
    scaffolding_level INTEGER NOT NULL DEFAULT 1,
    stuck_count INTEGER NOT NULL DEFAULT 0,
    misconception_tags TEXT NOT NULL DEFAULT '[]',
    turns_to_resolution INTEGER NOT NULL DEFAULT 0,
    resolved INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tutoring_messages (
    message_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES tutoring_sessions(session_id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('student', 'tutor')),
    content TEXT NOT NULL,
    diagnosis TEXT,
    pedagogical_move TEXT,
    scaffolding_level INTEGER,
    citations_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS practice_items (
    question_id TEXT PRIMARY KEY,
    student_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    book_id TEXT REFERENCES textbooks(book_id) ON DELETE SET NULL,
    concept_id TEXT NOT NULL REFERENCES concepts(concept_id) ON DELETE CASCADE,
    assignment_id TEXT,
    difficulty_score INTEGER NOT NULL,
    question_text TEXT NOT NULL,
    hidden_solution_steps TEXT NOT NULL,
    common_misconceptions TEXT NOT NULL,
    source_excerpt TEXT NOT NULL DEFAULT '',
    hints_used INTEGER NOT NULL DEFAULT 0,
    completed INTEGER NOT NULL DEFAULT 0,
    is_correct INTEGER,
    misconception_tags TEXT NOT NULL DEFAULT '[]',
    student_answer TEXT,
    feedback TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS assignments (
    assignment_id TEXT PRIMARY KEY,
    teacher_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    class_name TEXT NOT NULL,
    concept_id TEXT NOT NULL REFERENCES concepts(concept_id) ON DELETE CASCADE,
    student_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    question_id TEXT NOT NULL REFERENCES practice_items(question_id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'completed')),
    created_at TEXT NOT NULL
);
"""


def get_connection(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open a SQLite connection configured with WAL mode and foreign keys."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


@contextmanager
def db_session(db_path: Path | str = DEFAULT_DB_PATH) -> Iterator[sqlite3.Connection]:
    """Yield a transactional SQLite connection and close it cleanly."""
    conn = get_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path | str = DEFAULT_DB_PATH) -> None:
    """Initialize all Socratic AI database tables and indexes."""
    with db_session(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
