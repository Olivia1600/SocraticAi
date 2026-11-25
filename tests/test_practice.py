"""Tests for Elo mastery updates, ZPD practice generation, and misconception logging."""

from pathlib import Path

from fastapi.testclient import TestClient

from socratic_ai.main import create_app
from tests.test_rag import _build_pdf_bytes


def _setup_practice_client(tmp_path: Path) -> tuple[TestClient, str, dict[str, str]]:
    app = create_app(db_path=tmp_path / "practice.db", upload_dir=tmp_path / "uploads")
    client = TestClient(app)
    client.post(
        "/api/auth/register",
        json={
            "name": "Practice Student",
            "password": "password123",
            "role": "student",
            "class_name": "Physics 101",
        },
    )
    pdf_bytes = _build_pdf_bytes(
        [
            "Chapter 1: Dynamics\n"
            "1.1 Force and Acceleration\n"
            "Definition: Force is defined as mass multiplied by acceleration.\n"
            "Worked Example: Multiply mass by acceleration to determine net force.\n"
        ]
    )
    book = client.post(
        "/api/textbooks/upload",
        files={"file": ("dynamics.pdf", pdf_bytes, "application/pdf")},
    ).json()["textbook"]
    detail = client.get(f"/api/textbooks/{book['book_id']}").json()
    concepts = {c["title"]: c["concept_id"] for c in detail["concepts"]}
    return client, book["book_id"], concepts


def test_practice_omits_hidden_steps_and_calibrates_zpd(tmp_path: Path) -> None:
    """Verify generated practice omits hidden_solution_steps and targets elo_score + 50."""
    client, book_id, concepts = _setup_practice_client(tmp_path)
    resp = client.post(
        "/api/practice/generate",
        json={"book_id": book_id, "concept_id": concepts["Force and Acceleration"]},
    )
    assert resp.status_code == 201
    item = resp.json()
    assert item["difficulty_score"] == 1050
    assert "hidden_solution_steps" not in item
    assert "common_misconceptions" not in item


def test_elo_increases_more_unassisted_than_hinted(tmp_path: Path) -> None:
    """Verify unassisted solution yields larger Elo gain than 2+ hints."""
    client, book_id, concepts = _setup_practice_client(tmp_path)
    cid = concepts["Force and Acceleration"]

    q1 = client.post(
        "/api/practice/generate", json={"book_id": book_id, "concept_id": cid}
    ).json()
    a1 = client.post(
        f"/api/practice/{q1['question_id']}/answer",
        json={"answer": "Force equals mass multiplied by acceleration in dynamics."},
    ).json()
    assert a1["correct"] is True
    unassisted_delta = a1["elo_delta"]
    assert 25 <= unassisted_delta <= 40

    q2 = client.post(
        "/api/practice/generate", json={"book_id": book_id, "concept_id": cid}
    ).json()
    client.post(f"/api/practice/{q2['question_id']}/hint")
    client.post(f"/api/practice/{q2['question_id']}/hint")
    a2 = client.post(
        f"/api/practice/{q2['question_id']}/answer",
        json={"answer": "Force equals mass multiplied by acceleration in dynamics."},
    ).json()
    assert a2["correct"] is True
    hinted_delta = a2["elo_delta"]
    assert 5 <= hinted_delta <= 10
    assert unassisted_delta > hinted_delta


def test_incorrect_answer_logs_misconception_and_decreases_elo(tmp_path: Path) -> None:
    """Verify misconception detection logs tags, drops Elo, and updates progress."""
    client, book_id, concepts = _setup_practice_client(tmp_path)
    cid = concepts["Force and Acceleration"]

    q = client.post(
        "/api/practice/generate", json={"book_id": book_id, "concept_id": cid}
    ).json()
    res = client.post(
        f"/api/practice/{q['question_id']}/answer",
        json={"answer": "Force is mass divided by acceleration."},
    ).json()
    assert res["correct"] is False
    assert -25 <= res["elo_delta"] <= -15
    assert res["elo_score"] == 980
    assert "inverted_multiplied_vs_divided" in res["misconception_tags"]

    progress = client.get("/api/practice/progress").json()
    assert len(progress["mastery"]) == 1
    assert progress["mastery"][0]["elo_score"] == 980
    assert progress["mastery"][0]["status"] == "developing"
