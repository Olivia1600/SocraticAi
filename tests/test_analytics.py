"""Tests for teacher class analytics heatmap, misconception clusters, alerts, and assignments."""

from pathlib import Path

from fastapi.testclient import TestClient

from socratic_ai.main import create_app
from tests.test_rag import _build_pdf_bytes


def test_teacher_heatmap_clusters_alerts_and_assignments(tmp_path: Path) -> None:
    """Verify teacher dashboard aggregates Elo bands, misconceptions, alerts, and pushes assignments."""
    app = create_app(db_path=tmp_path / "analytics.db", upload_dir=tmp_path / "uploads")

    student_client = TestClient(app)
    s_user = student_client.post(
        "/api/auth/register",
        json={
            "name": "Maya Lin",
            "password": "password123",
            "role": "student",
            "class_name": "Physics 101",
        },
    ).json()["user"]

    pdf_bytes = _build_pdf_bytes(
        [
            "Chapter 1: Dynamics\n"
            "1.1 Force\n"
            "Definition: Force is defined as mass multiplied by acceleration.\n"
        ]
    )
    book = student_client.post(
        "/api/textbooks/upload",
        files={"file": ("forces.pdf", pdf_bytes, "application/pdf")},
    ).json()["textbook"]
    detail = student_client.get(f"/api/textbooks/{book['book_id']}").json()
    force_cid = next(c["concept_id"] for c in detail["concepts"] if c["title"] == "Force")

    # Student gets 2 consecutive incorrect answers + 3 hints on item 1 -> triggers both alert rules
    q1 = student_client.post(
        "/api/practice/generate", json={"book_id": book["book_id"], "concept_id": force_cid}
    ).json()
    for _ in range(3):
        student_client.post(f"/api/practice/{q1['question_id']}/hint")
    student_client.post(
        f"/api/practice/{q1['question_id']}/answer",
        json={"answer": "Force is mass divided by acceleration."},
    )

    q2 = student_client.post(
        "/api/practice/generate", json={"book_id": book["book_id"], "concept_id": force_cid}
    ).json()
    for _ in range(3):
        student_client.post(f"/api/practice/{q2['question_id']}/hint")
    student_client.post(
        f"/api/practice/{q2['question_id']}/answer",
        json={"answer": "Force is mass divided by acceleration."},
    )

    teacher_client = TestClient(app)
    teacher_client.post(
        "/api/auth/register",
        json={
            "name": "Dr. Euler",
            "password": "password123",
            "role": "teacher",
            "class_name": "Physics 101",
        },
    )

    dash = teacher_client.get("/api/teacher/dashboard")
    assert dash.status_code == 200
    data = dash.json()
    assert data["class_name"] == "Physics 101"
    assert len(data["heatmap"]) == 1
    assert data["heatmap"][0]["student_name"] == "Maya Lin"

    force_cell = next(c for c in data["heatmap"][0]["cells"] if c["concept_id"] == force_cid)
    assert force_cell["elo_score"] == 960
    assert force_cell["band"] == "developing"

    assert data["misconception_clusters"]
    assert data["misconception_clusters"][0]["tag"] == "inverted_multiplied_vs_divided"
    assert data["misconception_clusters"][0]["affected_students"] == 1

    assert len(data["intervention_alerts"]) == 1
    alert = data["intervention_alerts"][0]
    assert "high_hint_usage" in alert["reasons"]
    assert "consecutive_elo_drops" in alert["reasons"]

    assigned = teacher_client.post(
        "/api/teacher/assignments",
        json={"concept_id": force_cid, "student_ids": [s_user["user_id"]]},
    )
    assert assigned.status_code == 201
    assert assigned.json()["assigned_count"] == 1

    student_progress = student_client.get("/api/practice/progress").json()
    assert len(student_progress["assignments"]) == 1
    assert student_progress["assignments"][0]["status"] == "pending"
