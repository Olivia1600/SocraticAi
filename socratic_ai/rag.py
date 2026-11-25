"""Textbook PDF ingestion, concept graph extraction, and hybrid retrieval."""

from collections import Counter
from datetime import datetime, timezone
import math
from pathlib import Path
import re
from typing import Any, Literal
import unicodedata
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from pypdf import PdfReader

from socratic_ai.auth import get_current_user
from socratic_ai.db import db_session

router = APIRouter(prefix="/api/textbooks", tags=["textbooks"])

CHAPTER_RE = re.compile(
    r"^(?:chapter|unit|module)\s+(\d+)\s*[:.\-]?\s*(.+)$", re.IGNORECASE
)
SUBSECTION_RE = re.compile(r"^(\d+\.\d+(?:\.\d+)?)\s+([A-Z][^\n]{2,90})$")
NUMBERED_SECTION_RE = re.compile(r"^(\d+)\.\s+([A-Z][^\n]{2,90})$")
TOKEN_RE = re.compile(r"[A-Za-z0-9]{2,}")


def clean_text(text: str) -> str:
    """Normalize extracted PDF text while preserving meaningful punctuation."""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\ufffd", "").replace("\x7f", "")
    text = re.sub(r"[^\x20-\x7E\n\r\u00A0\u2013\u2014\u2018\u2019\u201C\u201D]", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def classify_content_type(text: str) -> str:
    """Classify a textbook chunk into a pedagogical content type."""
    lower = text.lower()
    if any(
        marker in lower
        for marker in ("worked example", "example ", "example:", "solution:", "step 1:")
    ):
        return "worked_example"
    if any(
        marker in lower
        for marker in ("exercise", "practice problem", "problem ", "try it", "question ")
    ):
        return "exercise"
    if any(
        marker in lower
        for marker in ("theorem", "lemma", "axiom", "principle", "law of", "corollary")
    ):
        return "theorem"
    if any(
        marker in lower
        for marker in ("definition", "is defined as", "refers to", "means that", "denotes")
    ):
        return "definition"
    return "narrative"


def _split_chunk_text(text: str, size: int = 750, overlap: int = 120) -> list[str]:
    normalized = " ".join(text.split())
    if not normalized:
        return []
    if len(normalized) <= size:
        return [normalized]
    pieces: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(start + size, len(normalized))
        if end < len(normalized):
            boundary = normalized.rfind(" ", start + size // 2, end)
            if boundary > start:
                end = boundary
        pieces.append(normalized[start:end].strip())
        if end >= len(normalized):
            break
        start = max(start + 1, end - overlap)
    return [piece for piece in pieces if piece]


def parse_textbook_pages(
    pages: list[str],
    book_title: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse extracted PDF pages into a concept hierarchy and metadata-tagged chunks."""
    concepts: list[dict[str, Any]] = []
    chunks: list[dict[str, Any]] = []

    chapter_concepts: dict[int, str] = {}
    last_chapter_id: str | None = None
    current_chapter_num = 1
    current_chapter_label = "Chapter 1"
    current_section_label = book_title
    current_concept_id: str | None = None
    buffer_lines: list[str] = []
    buffer_page = 1

    def ensure_chapter_concept(chap_num: int, title: str, section_label: str) -> str:
        nonlocal last_chapter_id
        if chap_num in chapter_concepts:
            return chapter_concepts[chap_num]
        cid = str(uuid.uuid4())
        concepts.append(
            {
                "concept_id": cid,
                "parent_concept_id": last_chapter_id,
                "title": title.strip()[:120],
                "chapter_number": chap_num,
                "section_label": section_label,
                "order_index": len(concepts),
            }
        )
        chapter_concepts[chap_num] = cid
        last_chapter_id = cid
        return cid

    def flush_buffer(page_num: int) -> None:
        nonlocal buffer_lines, current_concept_id
        raw = "\n".join(buffer_lines).strip()
        buffer_lines = []
        if not raw:
            return
        if current_concept_id is None:
            current_concept_id = ensure_chapter_concept(
                current_chapter_num, book_title, current_section_label
            )
        for piece in _split_chunk_text(raw):
            chunks.append(
                {
                    "chunk_id": str(uuid.uuid4()),
                    "concept_id": current_concept_id,
                    "chapter": current_chapter_label,
                    "section": current_section_label,
                    "page_number": page_num,
                    "content_type": classify_content_type(piece),
                    "text": piece,
                    "chunk_index": len(chunks),
                }
            )

    for page_index, raw_page in enumerate(pages, start=1):
        cleaned_page = clean_text(raw_page)
        if not cleaned_page:
            continue
        for line in cleaned_page.splitlines():
            stripped = line.strip()
            if not stripped:
                continue

            chap_match = CHAPTER_RE.match(stripped)
            if chap_match:
                flush_buffer(buffer_page)
                current_chapter_num = int(chap_match.group(1))
                chap_title = chap_match.group(2).strip(" .:-") or f"Chapter {current_chapter_num}"
                current_chapter_label = f"Chapter {current_chapter_num}: {chap_title}"
                current_section_label = chap_title
                current_concept_id = ensure_chapter_concept(
                    current_chapter_num, chap_title, f"Chapter {current_chapter_num}"
                )
                buffer_page = page_index
                continue

            sub_match = SUBSECTION_RE.match(stripped) or NUMBERED_SECTION_RE.match(stripped)
            if sub_match and len(stripped.split()) <= 12:
                flush_buffer(buffer_page)
                sec_code = sub_match.group(1)
                sec_title = sub_match.group(2).strip(" .:-")
                if "." in sec_code:
                    current_chapter_num = int(sec_code.split(".")[0])
                parent_id = ensure_chapter_concept(
                    current_chapter_num,
                    current_chapter_label.split(": ", 1)[-1],
                    f"Chapter {current_chapter_num}",
                )
                current_section_label = f"{sec_code} {sec_title}"
                cid = str(uuid.uuid4())
                concepts.append(
                    {
                        "concept_id": cid,
                        "parent_concept_id": parent_id,
                        "title": sec_title[:120],
                        "chapter_number": current_chapter_num,
                        "section_label": sec_code,
                        "order_index": len(concepts),
                    }
                )
                current_concept_id = cid
                buffer_page = page_index
                continue

            if not buffer_lines:
                buffer_page = page_index
            buffer_lines.append(stripped)

        flush_buffer(buffer_page)

    if not concepts:
        cid = str(uuid.uuid4())
        concepts.append(
            {
                "concept_id": cid,
                "parent_concept_id": None,
                "title": book_title[:120],
                "chapter_number": 1,
                "section_label": "1",
                "order_index": 0,
            }
        )
        for chunk in chunks:
            chunk["concept_id"] = cid

    return concepts, chunks


def _bm25_scores(docs: list[str], query: str) -> list[float]:
    query_tokens = [t.lower() for t in TOKEN_RE.findall(query)]
    if not query_tokens or not docs:
        return [0.0] * len(docs)

    doc_tokens = [[t.lower() for t in TOKEN_RE.findall(d)] for d in docs]
    avgdl = sum(len(t) for t in doc_tokens) / max(len(doc_tokens), 1)
    n_docs = len(doc_tokens)
    k1 = 1.5
    b = 0.75

    df: dict[str, int] = {}
    for tokens in doc_tokens:
        for tok in set(tokens):
            df[tok] = df.get(tok, 0) + 1

    scores: list[float] = []
    for tokens in doc_tokens:
        dl = len(tokens) or 1
        tf_map: dict[str, int] = {}
        for tok in tokens:
            tf_map[tok] = tf_map.get(tok, 0) + 1
        score = 0.0
        for qtok in query_tokens:
            if qtok not in tf_map:
                continue
            tf = tf_map[qtok]
            idf = math.log(1 + (n_docs - df.get(qtok, 0) + 0.5) / (df.get(qtok, 0) + 0.5))
            denom = tf + k1 * (1 - b + b * dl / max(avgdl, 1.0))
            score += idf * (tf * (k1 + 1)) / max(denom, 1e-9)
        scores.append(score)
    max_s = max(scores) if scores else 0.0
    return [s / max_s if max_s > 0 else 0.0 for s in scores]


def _tfidf_cosine_scores(docs: list[str], query: str) -> list[float]:
    query_tokens = [t.lower() for t in TOKEN_RE.findall(query)]
    if not query_tokens or not docs:
        return [0.0] * len(docs)

    doc_tokens = [[t.lower() for t in TOKEN_RE.findall(d)] for d in docs]
    n_docs = len(doc_tokens)
    df: Counter[str] = Counter()
    for tokens in doc_tokens:
        df.update(set(tokens))

    def vectorize(tokens: list[str]) -> dict[str, float]:
        tf = Counter(tokens)
        vec: dict[str, float] = {}
        for tok, count in tf.items():
            idf = math.log((n_docs + 1) / (df.get(tok, 0) + 1)) + 1.0
            vec[tok] = count * idf
        return vec

    q_vec = vectorize(query_tokens)
    q_norm = math.sqrt(sum(v * v for v in q_vec.values()))
    if q_norm == 0:
        return [0.0] * len(docs)

    scores: list[float] = []
    for tokens in doc_tokens:
        d_vec = vectorize(tokens)
        d_norm = math.sqrt(sum(v * v for v in d_vec.values()))
        if d_norm == 0:
            scores.append(0.0)
            continue
        dot = sum(q_vec[t] * d_vec[t] for t in q_vec if t in d_vec)
        scores.append(dot / (q_norm * d_norm))
    return scores


def retrieve_chunks(
    db_path: Path | str,
    query: str,
    book_id: str | None = None,
    concept_id: str | None = None,
    mode: Literal["tutor", "practice", "all"] = "all",
    limit: int = 4,
) -> list[dict[str, Any]]:
    """Retrieve ranked textbook chunks with hybrid TF-IDF + BM25 and mode preferences."""
    with db_session(db_path) as conn:
        sql = (
            "SELECT c.chunk_id, c.book_id, c.concept_id, c.chapter, c.section, "
            "c.page_number, c.content_type, c.text, t.title AS book_title, "
            "co.title AS concept_title "
            "FROM chunks c "
            "JOIN textbooks t ON t.book_id = c.book_id "
            "LEFT JOIN concepts co ON co.concept_id = c.concept_id "
            "WHERE 1=1"
        )
        params: list[Any] = []
        if book_id:
            sql += " AND c.book_id = ?"
            params.append(book_id)
        if concept_id:
            sql += " AND c.concept_id = ?"
            params.append(concept_id)
        rows = conn.execute(sql, tuple(params)).fetchall()

    if not rows:
        return []

    texts = [row["text"] for row in rows]
    bm25 = _bm25_scores(texts, query)
    tfidf_scores = _tfidf_cosine_scores(texts, query)

    if mode == "tutor":
        type_weights = {
            "definition": 1.45,
            "theorem": 1.35,
            "narrative": 1.15,
            "worked_example": 0.4,
            "exercise": 0.5,
        }
    elif mode == "practice":
        type_weights = {
            "worked_example": 1.8,
            "exercise": 1.7,
            "definition": 0.9,
            "theorem": 0.9,
            "narrative": 0.75,
        }
    else:
        type_weights = {t: 1.0 for t in ("definition", "theorem", "worked_example", "exercise", "narrative")}

    scored: list[tuple[float, dict[str, Any]]] = []
    for idx, row in enumerate(rows):
        base = 0.55 * tfidf_scores[idx] + 0.45 * bm25[idx]
        if base <= 0 and not concept_id:
            continue
        weight = type_weights.get(row["content_type"], 1.0)
        final_score = round((base if base > 0 else 0.05) * weight, 4)
        item = dict(row)
        item["score"] = final_score
        scored.append((final_score, item))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored[:limit]]


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_textbook(
    request: Request,
    file: UploadFile = File(...),
    user: dict[str, str] = Depends(get_current_user),
) -> dict[str, Any]:
    """Upload, parse, and index a textbook PDF into concepts and chunks."""
    filename = file.filename or ""
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please upload a valid .pdf file.",
        )

    book_id = str(uuid.uuid4())
    upload_dir: Path = request.app.state.upload_dir
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / f"{book_id}.pdf"
    content = await file.read()
    dest.write_bytes(content)

    try:
        reader = PdfReader(dest)
        pages = [(page.extract_text() or "") for page in reader.pages]
    except Exception as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not read PDF: {exc}",
        ) from exc

    if len("".join(pages).strip()) < 25:
        dest.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No readable text was found in this PDF.",
        )

    book_title = filename.rsplit(".", 1)[0]
    concepts, chunks = parse_textbook_pages(pages, book_title)
    uploaded_at = datetime.now(timezone.utc).isoformat()

    with db_session(request.app.state.db_path) as conn:
        conn.execute(
            "INSERT INTO textbooks(book_id, filename, title, file_path, uploaded_by, page_count, uploaded_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (book_id, filename, book_title, str(dest), user["user_id"], len(pages), uploaded_at),
        )
        for concept in concepts:
            conn.execute(
                "INSERT INTO concepts(concept_id, book_id, parent_concept_id, title, chapter_number, section_label, order_index)"
                " VALUES(?,?,?,?,?,?,?)",
                (
                    concept["concept_id"],
                    book_id,
                    concept["parent_concept_id"],
                    concept["title"],
                    concept["chapter_number"],
                    concept["section_label"],
                    concept["order_index"],
                ),
            )
        for chunk in chunks:
            conn.execute(
                "INSERT INTO chunks(chunk_id, book_id, concept_id, chapter, section, page_number, content_type, text, chunk_index)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    chunk["chunk_id"],
                    book_id,
                    chunk["concept_id"],
                    chunk["chapter"],
                    chunk["section"],
                    chunk["page_number"],
                    chunk["content_type"],
                    chunk["text"],
                    chunk["chunk_index"],
                ),
            )

    return {
        "textbook": {
            "book_id": book_id,
            "filename": filename,
            "title": book_title,
            "page_count": len(pages),
            "concepts_count": len(concepts),
            "chunks_count": len(chunks),
            "pdf_url": f"/api/textbooks/{book_id}/file",
        }
    }


@router.get("")
def list_textbooks(
    request: Request,
    _: dict[str, str] = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """List uploaded textbooks with concept and chunk counts."""
    with db_session(request.app.state.db_path) as conn:
        rows = conn.execute(
            "SELECT t.book_id, t.filename, t.title, t.page_count, t.uploaded_at, "
            "(SELECT COUNT(*) FROM concepts co WHERE co.book_id = t.book_id) AS concepts_count, "
            "(SELECT COUNT(*) FROM chunks ch WHERE ch.book_id = t.book_id) AS chunks_count "
            "FROM textbooks t ORDER BY t.uploaded_at DESC"
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["pdf_url"] = f"/api/textbooks/{row['book_id']}/file"
        result.append(item)
    return result


@router.get("/{book_id}")
def get_textbook(
    book_id: str,
    request: Request,
    _: dict[str, str] = Depends(get_current_user),
) -> dict[str, Any]:
    """Get textbook details and its concept dependency hierarchy."""
    with db_session(request.app.state.db_path) as conn:
        book = conn.execute(
            "SELECT book_id, filename, title, page_count, uploaded_at FROM textbooks WHERE book_id=?",
            (book_id,),
        ).fetchone()
        if not book:
            raise HTTPException(status_code=404, detail="Textbook not found.")
        concepts = conn.execute(
            "SELECT concept_id, parent_concept_id, title, chapter_number, section_label, order_index "
            "FROM concepts WHERE book_id=? ORDER BY order_index",
            (book_id,),
        ).fetchall()
    payload = dict(book)
    payload["pdf_url"] = f"/api/textbooks/{book_id}/file"
    payload["concepts"] = [dict(c) for c in concepts]
    return payload


@router.get("/{book_id}/file")
def get_textbook_file(
    book_id: str,
    request: Request,
    _: dict[str, str] = Depends(get_current_user),
) -> FileResponse:
    """Serve the uploaded PDF file for split-screen viewing."""
    with db_session(request.app.state.db_path) as conn:
        book = conn.execute(
            "SELECT filename, file_path FROM textbooks WHERE book_id=?",
            (book_id,),
        ).fetchone()
    if not book:
        raise HTTPException(status_code=404, detail="Textbook not found.")
    file_path = Path(book["file_path"])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="PDF file missing on disk.")
    return FileResponse(file_path, media_type="application/pdf", filename=book["filename"])


@router.get("/{book_id}/search")
def search_textbook(
    book_id: str,
    request: Request,
    q: str = Query(..., min_length=1),
    mode: Literal["tutor", "practice", "all"] = Query(default="tutor"),
    limit: int = Query(default=4, ge=1, le=10),
    _: dict[str, str] = Depends(get_current_user),
) -> dict[str, Any]:
    """Search textbook chunks with pedagogical mode weighting and citations."""
    results = retrieve_chunks(
        request.app.state.db_path, query=q, book_id=book_id, mode=mode, limit=limit
    )
    return {"book_id": book_id, "query": q, "mode": mode, "results": results}
