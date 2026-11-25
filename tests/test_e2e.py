"""End-to-end UI asset and multi-role workflow integration tests."""

from pathlib import Path

from fastapi.testclient import TestClient

from socratic_ai.main import create_app
from tests.test_rag import _build_pdf_bytes


def test_static_ui_assets_served_and_contain_split_screen_elements(tmp_path: Path) -> None:
    """Verify FastAPI serves index.html, styles.css, and app.js with core workspace elements."""
    app = create_app(db_path=tmp_path / "e2e.db", upload_dir=tmp_path / "uploads")
    client = TestClient(app)

    index_resp = client.get("/")
    assert index_resp.status_code == 200
    html = index_resp.text
    for element_id in (
        "authScreen",
        "pdfFrame",
        "citationList",
        "chatForm",
        "practiceDialog",
        "heatmapTable",
        "clusterList",
        "alertList",
    ):
        assert element_id in html

    assert client.get("/static/styles.css").status_code == 200
    assert client.get("/static/app.js").status_code == 200


def test_full_student_and_teacher_journey(tmp_path: Path) -> None:
    """Exercise complete flow: auth -> upload -> tutor -> practice -> teacher assign -> complete."""
    app = create_app(db_path=tmp_path / "journey.db", upload_dir=tmp_path / "uploads")
    student = TestClient(app)
    teacher = TestClient(app)

    student.post(
        "/api/auth/register",
        json={
            "name": "Elena Rostova",
            "password": "password123",
            "role": "student",
            "class_name": "Mechanics 201",
        },
    )
    teacher.post(
        "/api/auth/register",
        json={
            "name": "Prof. Lagrange",
            "password": "password123",
            "role": "teacher",
            "class_name": "Mechanics 201",
        },
    )

    pdf_bytes = _build_pdf_bytes(
        [
            "Chapter 1: Energy\n"
            "Definition: Energy refers to the capacity to do work.\n"
            "1.1 Kinetic Energy\n"
            "Theorem: Kinetic energy principle relates mass and velocity squared.\n"
        ]
    )
    book = student.post(
        "/api/textbooks/upload",
        files={"file": ("energy.pdf", pdf_bytes, "application/pdf")},
    ).json()["textbook"]
    detail = student.get(f"/api/textbooks/{book['book_id']}").json()
    ke_cid = next(c["concept_id"] for c in detail["concepts"] if c["title"] == "Kinetic Energy")

    # Student chats and receives Socratic guidance with citation
    chat = student.post(
        "/api/tutor/chat",
        json={
            "book_id": book["book_id"],
            "concept_id": ke_cid,
            "message": "What determines kinetic energy?",
        },
    )
    assert chat.status_code == 200
    assert chat.json()["citations"]
    assert "?" in chat.json()["reply"]

    # Teacher pushes targeted assignment
    assigned = teacher.post(
        "/api/teacher/assignments",
        json={"concept_id": ke_cid, "title": "Review Kinetic Energy"},
    )
    assert assigned.status_code == 201
    assert assigned.json()["assigned_count"] == 1

    # Student sees pending assignment, submits correct explanation, assignment completes
    prog_before = student.get("/api/practice/progress").json()
    assert prog_before["assignments"][0]["status"] == "pending"
    qid = prog_before["assignments"][0]["question_id"]

    submit = student.post(
        f"/api/practice/{qid}/answer",
        json={"answer": "Kinetic energy relates mass and velocity squared in motion."},
    )
    assert submit.status_code == 200
    assert submit.json()["correct"] is True
    assert submit.json()["elo_score"] == 1032

    prog_after = student.get("/api/practice/progress").json()
    assert prog_after["assignments"][0]["status"] == "completed"
