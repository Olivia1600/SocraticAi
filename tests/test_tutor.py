"""Tests for the 4-stage Socratic tutoring state machine and leakage guard."""

from pathlib import Path

from fastapi.testclient import TestClient

from socratic_ai.main import create_app
from socratic_ai.tutor import guard_socratic_response
from tests.test_rag import _build_pdf_bytes


def _setup_tutor_client(tmp_path: Path) -> tuple[TestClient, str, dict[str, str]]:
    app = create_app(db_path=tmp_path / "tutor.db", upload_dir=tmp_path / "uploads")
    client = TestClient(app)
    client.post(
        "/api/auth/register",
        json={
            "name": "Socratic Student",
            "password": "password123",
            "role": "student",
            "class_name": "Physics 101",
        },
    )
    pdf_bytes = _build_pdf_bytes(
        [
            "Chapter 1: Motion\n"
            "Definition: Motion refers to a change in position over time.\n"
            "1.1 Velocity\n"
            "Definition: Velocity is defined as displacement divided by time.\n"
        ]
    )
    book = client.post(
        "/api/textbooks/upload",
        files={"file": ("motion.pdf", pdf_bytes, "application/pdf")},
    ).json()["textbook"]
    detail = client.get(f"/api/textbooks/{book['book_id']}").json()
    concepts = {c["title"]: c["concept_id"] for c in detail["concepts"]}
    return client, book["book_id"], concepts


def test_misconception_diagnosis_triggers_counter_example(tmp_path: Path) -> None:
    """Verify contradictory claims trigger misconception diagnosis and counter-example move."""
    client, book_id, concepts = _setup_tutor_client(tmp_path)
    resp = client.post(
        "/api/tutor/chat",
        json={
            "book_id": book_id,
            "concept_id": concepts["Velocity"],
            "message": "Velocity is displacement multiplied by time.",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["diagnosis"] == "misconception"
    assert data["pedagogical_move"] == "counter_example"
    assert data["misconception_tags"]
    assert "?" in data["reply"]


def test_three_stuck_turns_trigger_prerequisite_step_back(tmp_path: Path) -> None:
    """Verify 3 consecutive stuck turns step back to the parent prerequisite concept."""
    client, book_id, concepts = _setup_tutor_client(tmp_path)
    session_id = None
    last = None
    for turn in range(3):
        resp = client.post(
            "/api/tutor/chat",
            json={
                "book_id": book_id,
                "concept_id": concepts["Velocity"],
                "session_id": session_id,
                "message": "I don't know, I am stuck.",
            },
        )
        assert resp.status_code == 200
        last = resp.json()
        session_id = last["session_id"]

    assert last is not None
    assert last["stepped_back"] is True
    assert last["pedagogical_move"] == "prerequisite_step_back"
    assert last["concept"]["concept_id"] == concepts["Motion"]


def test_response_guard_blocks_answer_leakage_and_persists_history(tmp_path: Path) -> None:
    """Verify AnswerLeakageGuard rewrites spoiler drafts and sessions persist across reloads."""
    guarded = guard_socratic_response(
        draft="The final answer is 10 N.",
        concept_title="Force",
        passages=[{"section": "1.1 Force", "page_number": 2, "content_type": "definition", "text": "F = ma"}],
        student_message="just give me the answer",
    )
    assert "10 N" not in guarded
    assert "?" in guarded

    client, book_id, concepts = _setup_tutor_client(tmp_path)
    chat = client.post(
        "/api/tutor/chat",
        json={
            "book_id": book_id,
            "concept_id": concepts["Velocity"],
            "message": "Just give me the answer for velocity.",
        },
    )
    assert chat.status_code == 200
    assert "?" in chat.json()["reply"]

    sessions = client.get("/api/tutor/sessions", params={"book_id": book_id}).json()
    assert len(sessions) == 1
    assert len(sessions[0]["messages"]) == 2
