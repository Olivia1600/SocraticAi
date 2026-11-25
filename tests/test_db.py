"""Tests for database schema initialization and FastAPI app bootstrap."""

from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
import pytest

from socratic_ai.db import db_session, get_connection, init_db
from socratic_ai.main import create_app


EXPECTED_TABLES = {
    "users",
    "textbooks",
    "concepts",
    "chunks",
    "student_concept_mastery",
    "tutoring_sessions",
    "tutoring_messages",
    "practice_items",
    "assignments",
}


def test_schema_creates_all_tables_and_enables_wal_and_fk(tmp_path: Path) -> None:
    """Verify init_db creates expected tables with WAL and foreign keys active."""
    db_file = tmp_path / "test.db"
    init_db(db_file)

    conn = get_connection(db_file)
    try:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert EXPECTED_TABLES.issubset(tables)

        fk_enabled = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
        assert fk_enabled == 1

        journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        assert journal_mode.lower() == "wal"

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO concepts(concept_id, book_id, title) VALUES(?,?,?)",
                ("c1", "missing-book", "Missing Parent"),
            )
    finally:
        conn.close()


def test_db_session_rolls_back_on_error(tmp_path: Path) -> None:
    """Verify db_session rolls back uncommitted writes on exception."""
    db_file = tmp_path / "rollback.db"
    init_db(db_file)

    with pytest.raises(RuntimeError):
        with db_session(db_file) as conn:
            conn.execute(
                "INSERT INTO users(user_id, name, role, class_name, password_hash, created_at)"
                " VALUES(?,?,?,?,?,?)",
                ("u1", "Ada", "student", "CS101", "hash", "2026-01-01T00:00:00"),
            )
            raise RuntimeError("boom")

    with db_session(db_file) as conn:
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        assert count == 0


def test_app_health_endpoint(tmp_path: Path) -> None:
    """Verify FastAPI app factory initializes cleanly and responds on /api/health."""
    app = create_app(db_path=tmp_path / "app.db", upload_dir=tmp_path / "uploads")
    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
