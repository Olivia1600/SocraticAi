"""Tests for PDF ingestion, concept graph extraction, and targeted retrieval."""

from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient

from socratic_ai.main import create_app


def _build_pdf_bytes(pages: list[str]) -> bytes:
    """Generate a minimal multi-page PDF with embedded text streams."""
    objects: list[bytes] = []

    def add_obj(body: str | bytes) -> int:
        raw = body.encode("latin-1") if isinstance(body, str) else body
        objects.append(raw)
        return len(objects)

    catalog_id = add_obj("")
    pages_id = add_obj("")
    font_id = add_obj("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    page_ids: list[int] = []
    for text in pages:
        lines = [line for line in text.splitlines() if line.strip()]
        stream_cmds = ["BT", "/F1 12 Tf", "50 750 Td"]
        for line in lines:
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            stream_cmds.append(f"({escaped}) Tj")
            stream_cmds.append("0 -16 Td")
        stream_cmds.append("ET")
        stream_data = "\n".join(stream_cmds)
        content_id = add_obj(
            f"<< /Length {len(stream_data)} >>\nstream\n{stream_data}\nendstream"
        )
        page_id = add_obj(
            f"<< /Type /Page /Parent {pages_id} 0 R "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
            f"/MediaBox [0 0 612 792] /Contents {content_id} 0 R >>"
        )
        page_ids.append(page_id)

    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects[catalog_id - 1] = f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode("latin-1")
    objects[pages_id - 1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("latin-1")

    buf = BytesIO()
    buf.write(b"%PDF-1.4\n")
    offsets = [0]
    for idx, obj in enumerate(objects, start=1):
        offsets.append(buf.tell())
        buf.write(f"{idx} 0 obj\n".encode("latin-1"))
        buf.write(obj)
        buf.write(b"\nendobj\n")

    xref_pos = buf.tell()
    buf.write(f"xref\n0 {len(objects) + 1}\n".encode("latin-1"))
    buf.write(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        buf.write(f"{off:010d} 00000 n \n".encode("latin-1"))
    buf.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n".encode("latin-1")
    )
    return buf.getvalue()


def _authed_client(tmp_path: Path) -> TestClient:
    app = create_app(db_path=tmp_path / "rag.db", upload_dir=tmp_path / "uploads")
    client = TestClient(app)
    client.post(
        "/api/auth/register",
        json={
            "name": "Student RAG",
            "password": "password123",
            "role": "student",
            "class_name": "Physics 101",
        },
    )
    return client


def test_upload_valid_pdf_extracts_concepts_and_serves_file(tmp_path: Path) -> None:
    """Verify PDF ingestion extracts concept tree, typed chunks, and serves raw PDF."""
    client = _authed_client(tmp_path)
    pdf_bytes = _build_pdf_bytes(
        [
            "Chapter 1: Kinematics\n"
            "1.1 Velocity\n"
            "Definition: Velocity refers to the rate of change of displacement with respect to time.\n"
            "Worked Example: A car moves 100 meters in 5 seconds. Solution: divide 100 by 5.",
            "1.2 Acceleration\n"
            "Theorem: Acceleration principle describes how velocity changes over time.\n"
            "Exercise: Practice problem calculating acceleration from rest.",
        ]
    )

    resp = client.post(
        "/api/textbooks/upload",
        files={"file": ("kinematics.pdf", pdf_bytes, "application/pdf")},
    )
    assert resp.status_code == 201
    book = resp.json()["textbook"]
    assert book["page_count"] == 2
    assert book["concepts_count"] >= 3
    assert book["chunks_count"] >= 2

    detail = client.get(f"/api/textbooks/{book['book_id']}").json()
    titles = [c["title"] for c in detail["concepts"]]
    assert "Kinematics" in titles
    assert "Velocity" in titles
    assert "Acceleration" in titles

    vel = next(c for c in detail["concepts"] if c["title"] == "Velocity")
    kin = next(c for c in detail["concepts"] if c["title"] == "Kinematics")
    assert vel["parent_concept_id"] == kin["concept_id"]

    pdf_resp = client.get(book["pdf_url"])
    assert pdf_resp.status_code == 200
    assert pdf_resp.headers["content-type"].startswith("application/pdf")


def test_reject_invalid_pdf_and_clean_up(tmp_path: Path) -> None:
    """Verify non-PDF uploads are rejected with 400 and leave no orphan files."""
    client = _authed_client(tmp_path)
    bad = client.post(
        "/api/textbooks/upload",
        files={"file": ("notes.txt", b"plain text", "text/plain")},
    )
    assert bad.status_code == 400
    assert list((tmp_path / "uploads").glob("*")) == []


def test_targeted_retrieval_prefers_definitions_in_tutor_mode(tmp_path: Path) -> None:
    """Verify tutor mode prioritizes definitions over worked examples while returning citations."""
    client = _authed_client(tmp_path)
    pdf_bytes = _build_pdf_bytes(
        [
            "Chapter 1: Forces\n"
            "1.1 Newton Second Law\n"
            "Definition: Force is defined as mass multiplied by acceleration.\n",
            "1.1 Newton Second Law\n"
            "Worked Example: To find force from mass and acceleration, Solution: Step 1: Multiply 5 kg mass by 2 m/s^2 acceleration to get 10 N force.\n",
        ]
    )
    book_id = client.post(
        "/api/textbooks/upload",
        files={"file": ("forces.pdf", pdf_bytes, "application/pdf")},
    ).json()["textbook"]["book_id"]

    tutor_hits = client.get(
        f"/api/textbooks/{book_id}/search",
        params={"q": "force mass acceleration", "mode": "tutor"},
    ).json()["results"]
    assert tutor_hits
    assert tutor_hits[0]["content_type"] == "definition"
    assert tutor_hits[0]["page_number"] == 1
    assert tutor_hits[0]["book_title"] == "forces"

    practice_hits = client.get(
        f"/api/textbooks/{book_id}/search",
        params={"q": "force mass acceleration", "mode": "practice"},
    ).json()["results"]
    assert practice_hits[0]["content_type"] == "worked_example"
