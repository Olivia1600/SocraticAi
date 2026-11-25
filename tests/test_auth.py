"""Tests for user registration, login, session checks, and role authorization."""

from pathlib import Path

from fastapi.testclient import TestClient

from socratic_ai.db import db_session
from socratic_ai.main import create_app


def _client(tmp_path: Path) -> TestClient:
    app = create_app(db_path=tmp_path / "auth.db", upload_dir=tmp_path / "uploads")
    return TestClient(app)


def test_register_student_and_reject_duplicate_name(tmp_path: Path) -> None:
    """Verify registration hashes password, creates session, and blocks case-insensitive duplicates."""
    client = _client(tmp_path)
    resp = client.post(
        "/api/auth/register",
        json={
            "name": "Ada Lovelace",
            "password": "secure-password",
            "role": "student",
            "class_name": "CS 101",
        },
    )
    assert resp.status_code == 201
    user = resp.json()["user"]
    assert user["name"] == "Ada Lovelace"
    assert user["role"] == "student"
    assert user["class_name"] == "CS 101"
    assert "password_hash" not in user

    with db_session(tmp_path / "auth.db") as conn:
        row = conn.execute(
            "SELECT password_hash FROM users WHERE user_id=?", (user["user_id"],)
        ).fetchone()
        assert row["password_hash"].startswith("pbkdf2_sha256$")

    dup = client.post(
        "/api/auth/register",
        json={
            "name": "ada lovelace",
            "password": "another-password",
            "role": "student",
            "class_name": "CS 101",
        },
    )
    assert dup.status_code == 409


def test_login_session_me_and_logout(tmp_path: Path) -> None:
    """Verify login succeeds with valid credentials, rejects invalid, and logout clears session."""
    client = _client(tmp_path)
    client.post(
        "/api/auth/register",
        json={
            "name": "Grace Hopper",
            "password": "compiler-password",
            "role": "student",
            "class_name": "CS 101",
        },
    )
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").json() == {"authenticated": False}

    bad = client.post(
        "/api/auth/login",
        json={"name": "Grace Hopper", "password": "wrong-password"},
    )
    assert bad.status_code == 401

    good = client.post(
        "/api/auth/login",
        json={"name": "grace hopper", "password": "compiler-password"},
    )
    assert good.status_code == 200
    me = client.get("/api/auth/me").json()
    assert me["authenticated"] is True
    assert me["user"]["name"] == "Grace Hopper"

    out = client.post("/api/auth/logout")
    assert out.status_code == 200
    assert out.json() == {"authenticated": False}
    assert client.get("/api/auth/me").json() == {"authenticated": False}


def test_teacher_role_enforcement(tmp_path: Path) -> None:
    """Verify teacher-only endpoints reject students with 403 and allow teachers."""
    student_client = _client(tmp_path)
    student_client.post(
        "/api/auth/register",
        json={
            "name": "Student One",
            "password": "password123",
            "role": "student",
            "class_name": "Physics 101",
        },
    )
    forbidden = student_client.get("/api/auth/class-students")
    assert forbidden.status_code == 403

    teacher_client = TestClient(student_client.app)
    teacher_client.post(
        "/api/auth/register",
        json={
            "name": "Prof. Taylor",
            "password": "password123",
            "role": "teacher",
            "class_name": "Physics 101",
        },
    )
    allowed = teacher_client.get("/api/auth/class-students")
    assert allowed.status_code == 200
    payload = allowed.json()
    assert payload["class_name"] == "Physics 101"
    assert [s["name"] for s in payload["students"]] == ["Student One"]
