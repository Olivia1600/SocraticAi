"""Stateful 4-stage Socratic tutoring loop and answer-leakage guard."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from socratic_ai.auth import get_current_user
from socratic_ai.db import db_session
from socratic_ai.llm import default_llm
from socratic_ai.rag import TOKEN_RE, retrieve_chunks

router = APIRouter(prefix="/api/tutor", tags=["tutor"])

STUCK_PHRASES = (
    "i don't know",
    "i dont know",
    "idk",
    "no idea",
    "stuck",
    "confused",
    "give me the answer",
    "just tell me",
    "tell me the answer",
    "solve this",
    "what is the answer",
    "help me",
)

OFF_TOPIC_MARKERS = ("pizza", "weather", "movie", "football", "recipe", "joke")

STOP_WORDS = {
    "what", "when", "where", "which", "while", "with", "that", "this", "these",
    "those", "from", "into", "about", "there", "their", "them", "they", "have",
    "does", "will", "would", "could", "should", "been", "being", "than", "then",
    "some", "more", "most", "only", "just", "know", "explain", "means", "mean",
}

OPPOSITE_PAIRS = (
    ("multiplied", "divided"),
    ("multiply", "divide"),
    ("increases", "decreases"),
    ("increase", "decrease"),
    ("always", "never"),
    ("direct", "inverse"),
    ("scalar", "vector"),
)

SPOILER_PATTERNS = (
    re.compile(r"\b(?:the\s+final\s+answer\s+is|the\s+answer\s+is|therefore\s+the\s+answer)\b", re.I),
    re.compile(r"\bsolution\s*:\s*[^?]{8,}", re.I),
    re.compile(r"\b(?:equals|=)\s*-?\d+(?:\.\d+)?\s*[A-Za-z/^0-9]*", re.I),
)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    book_id: str | None = None
    concept_id: str | None = None
    session_id: str | None = None


@dataclass
class DiagnosisResult:
    diagnosis: str
    misconception_tags: list[str]


def _content_tokens(text: str) -> set[str]:
    return {
        t.lower()
        for t in TOKEN_RE.findall(text)
        if len(t) >= 3 and t.lower() not in STOP_WORDS
    }


def diagnose_turn(
    message: str,
    concept_title: str,
    passages: list[dict[str, Any]],
) -> DiagnosisResult:
    """Classify a student's message into correct, partial, misconception, stuck, or off_topic."""
    lower = message.lower().strip()
    if any(phrase in lower for phrase in STUCK_PHRASES):
        return DiagnosisResult(diagnosis="stuck", misconception_tags=[])

    if any(marker in lower for marker in OFF_TOPIC_MARKERS):
        return DiagnosisResult(diagnosis="off_topic", misconception_tags=[])

    passage_text = " ".join(p["text"] for p in passages)
    passage_lower = passage_text.lower()
    msg_tokens = _content_tokens(message)
    ref_tokens = _content_tokens(f"{concept_title} {passage_text}")

    if not msg_tokens:
        return DiagnosisResult(diagnosis="stuck", misconception_tags=[])

    # Check opposite-term contradictions against textbook context
    tags: list[str] = []
    for a, b in OPPOSITE_PAIRS:
        if a in passage_lower and b in lower and a not in lower:
            tags.append(f"inverted_{a}_vs_{b}")
        elif b in passage_lower and a in lower and b not in lower:
            tags.append(f"inverted_{b}_vs_{a}")

    if ("not " in lower or "never " in lower) and (
        "is defined as" in passage_lower or "refers to" in passage_lower
    ):
        overlap = msg_tokens & _content_tokens(passage_text)
        if len(overlap) >= 2:
            tags.append("negation_contradiction")

    if tags:
        return DiagnosisResult(diagnosis="misconception", misconception_tags=tags)

    overlap = msg_tokens & ref_tokens
    if not overlap:
        if len(msg_tokens) <= 2:
            return DiagnosisResult(diagnosis="stuck", misconception_tags=[])
        return DiagnosisResult(diagnosis="off_topic", misconception_tags=[])

    # LLM refinement when online
    llm_diag = default_llm.generate_json(
        "Classify the student's response relative to the textbook context. "
        "Return JSON: {\"diagnosis\": \"correct|partial|misconception|stuck|off_topic\", \"misconception_tags\": []}",
        f"Concept: {concept_title}\nContext: {passage_text[:700]}\nStudent: {message}",
    )
    if llm_diag and llm_diag.get("diagnosis") in {
        "correct", "partial", "misconception", "stuck", "off_topic"
    }:
        return DiagnosisResult(
            diagnosis=llm_diag["diagnosis"],
            misconception_tags=[str(t) for t in llm_diag.get("misconception_tags", [])][:5],
        )

    is_question = lower.endswith("?") or lower.startswith(
        ("what", "why", "how", "explain", "can you", "tell me")
    )
    if not is_question and len(overlap) >= 3:
        return DiagnosisResult(diagnosis="correct", misconception_tags=[])
    return DiagnosisResult(diagnosis="partial", misconception_tags=[])


def guard_socratic_response(
    draft: str,
    concept_title: str,
    passages: list[dict[str, Any]],
    student_message: str,
) -> str:
    """Verify and rewrite drafts that leak direct answers or lack a guiding question."""
    leaked = any(pat.search(draft) for pat in SPOILER_PATTERNS)

    # Also ensure we don't verbatim leak worked_example solution lines
    for p in passages:
        if p.get("content_type") == "worked_example" and "solution:" in p["text"].lower():
            sol_part = p["text"].lower().split("solution:", 1)[1].strip()[:60]
            if sol_part and sol_part in draft.lower():
                leaked = True

    if not leaked and "?" in draft:
        return draft.strip()

    citation = ""
    if passages:
        top = passages[0]
        citation = f" (see {top['section']}, p. {top['page_number']})"

    if leaked:
        return (
            f"Let's work through {concept_title} step by step rather than jumping to the result{citation}. "
            f"Looking at the core definition, what quantities or relationships do you notice first?"
        )

    return f"{draft.strip()} What step do you think comes next for {concept_title}?"


def _draft_socratic_reply(
    move: str,
    concept_title: str,
    passages: list[dict[str, Any]],
    scaffolding_level: int,
    stepped_back_from: str | None = None,
) -> str:
    citation_label = ""
    clue = ""
    if passages:
        top = passages[0]
        citation_label = f" [{top['section']}, p. {top['page_number']}]"
        raw = top["text"]
        clue = raw[:220].rstrip() + ("..." if len(raw) > 220 else "")

    llm_reply = default_llm.generate_text(
        "You are a strict Socratic tutor. Never give the final answer or solve the problem directly. "
        "Ask one focused guiding question grounded in the textbook excerpt.",
        f"Move: {move}\nConcept: {concept_title}\nScaffolding level: {scaffolding_level}/4\n"
        f"Stepped back from: {stepped_back_from or 'none'}\nExcerpt: {clue}",
    )
    if llm_reply:
        return llm_reply

    if move == "prerequisite_step_back" and stepped_back_from:
        return (
            f"Let's take one step back from {stepped_back_from} to its foundation: {concept_title}{citation_label}. "
            f"Before combining ideas, how would you describe {concept_title} in your own words?"
        )
    if move == "counter_example":
        return (
            f"Wait—test that assumption against the textbook on {concept_title}{citation_label}. "
            f"If we check the opposite case, does your relationship still hold, or what needs to change?"
        )
    if move == "hint":
        hint_lead = f"Clue{citation_label}: “{clue}” " if clue else ""
        return (
            f"{hint_lead}At scaffolding level {scaffolding_level}, which single term or variable in {concept_title} "
            f"should we identify first?"
        )
    return (
        f"You're engaging with {concept_title}{citation_label}. "
        f"What key connection or edge case can you infer from the excerpt?"
    )


def _format_citation(chunk: dict[str, Any]) -> dict[str, Any]:
    return {
        "chunk_id": chunk["chunk_id"],
        "book_id": chunk["book_id"],
        "book_title": chunk["book_title"],
        "concept_id": chunk["concept_id"],
        "concept_title": chunk["concept_title"],
        "chapter": chunk["chapter"],
        "section": chunk["section"],
        "page_number": chunk["page_number"],
        "content_type": chunk["content_type"],
        "excerpt": chunk["text"][:280],
    }


def _ensure_mastery_row(conn: Any, student_id: str, concept_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT elo_score, status FROM student_concept_mastery "
        "WHERE student_id=? AND concept_id=?",
        (student_id, concept_id),
    ).fetchone()
    if row:
        return dict(row)
    conn.execute(
        "INSERT INTO student_concept_mastery(student_id, concept_id, elo_score, status) "
        "VALUES(?,?,1000,'not_started')",
        (student_id, concept_id),
    )
    return {"elo_score": 1000, "status": "not_started"}


@router.post("/chat")
def tutor_chat(
    payload: ChatRequest,
    request: Request,
    user: dict[str, str] = Depends(get_current_user),
) -> dict[str, Any]:
    """Execute one stateful Socratic tutoring turn."""
    message = payload.message.strip()
    if not message:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Message cannot be empty.",
        )

    db_path: Path = request.app.state.db_path
    now = datetime.now(timezone.utc).isoformat()

    with db_session(db_path) as conn:
        session_row = None
        if payload.session_id:
            session_row = conn.execute(
                "SELECT * FROM tutoring_sessions WHERE session_id=? AND student_id=?",
                (payload.session_id, user["user_id"]),
            ).fetchone()
            if not session_row:
                raise HTTPException(status_code=404, detail="Tutoring session not found.")

        book_id = payload.book_id or (session_row["book_id"] if session_row else None)
        concept_id = payload.concept_id or (session_row["concept_id"] if session_row else None)

        # Resolve concept via retrieval if not explicitly pinned
        initial_passages = retrieve_chunks(
            db_path,
            query=message,
            book_id=book_id,
            concept_id=concept_id,
            mode="tutor",
            limit=3,
        )
        if not concept_id and initial_passages:
            concept_id = initial_passages[0]["concept_id"]
            book_id = book_id or initial_passages[0]["book_id"]

        if not concept_id and book_id:
            first_concept = conn.execute(
                "SELECT concept_id FROM concepts WHERE book_id=? ORDER BY order_index LIMIT 1",
                (book_id,),
            ).fetchone()
            if first_concept:
                concept_id = first_concept["concept_id"]

        concept_row = None
        if concept_id:
            concept_row = conn.execute(
                "SELECT * FROM concepts WHERE concept_id=?", (concept_id,)
            ).fetchone()
        concept_title = concept_row["title"] if concept_row else "Core Concept"

        if not initial_passages and concept_id:
            initial_passages = retrieve_chunks(
                db_path,
                query=concept_title,
                book_id=book_id,
                concept_id=concept_id,
                mode="tutor",
                limit=3,
            )

        # Stage 1: Diagnosis
        diag = diagnose_turn(message, concept_title, initial_passages)

        # Create session if needed
        if session_row is None:
            session_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO tutoring_sessions("
                "session_id, student_id, book_id, concept_id, scaffolding_level, "
                "stuck_count, misconception_tags, turns_to_resolution, resolved, created_at, updated_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (session_id, user["user_id"], book_id, concept_id, 1, 0, "[]", 0, 0, now, now),
            )
            session_row = conn.execute(
                "SELECT * FROM tutoring_sessions WHERE session_id=?", (session_id,)
            ).fetchone()
        else:
            session_id = session_row["session_id"]

        # Stage 2: State & Mastery Updater
        stuck_count = session_row["stuck_count"]
        scaffolding_level = session_row["scaffolding_level"]
        existing_tags: list[str] = json.loads(session_row["misconception_tags"] or "[]")
        for tag in diag.misconception_tags:
            if tag not in existing_tags:
                existing_tags.append(tag)

        if diag.diagnosis == "stuck":
            stuck_count += 1
            scaffolding_level = min(4, scaffolding_level + 1)
        elif diag.diagnosis == "misconception":
            stuck_count = 0
            scaffolding_level = min(4, scaffolding_level + 1)
        elif diag.diagnosis == "correct":
            stuck_count = 0
            scaffolding_level = max(1, scaffolding_level - 1)
        else:
            stuck_count = 0

        turns = session_row["turns_to_resolution"] + 1
        resolved = 1 if diag.diagnosis == "correct" else session_row["resolved"]

        # Stage 3: Pedagogical Planner (including 3-turn stuck prerequisite step-back)
        stepped_back = False
        stepped_back_from: str | None = None
        active_concept_row = concept_row
        active_passages = initial_passages

        if stuck_count >= 3 and concept_row and concept_row["parent_concept_id"]:
            parent_row = conn.execute(
                "SELECT * FROM concepts WHERE concept_id=?",
                (concept_row["parent_concept_id"],),
            ).fetchone()
            if parent_row:
                stepped_back = True
                stepped_back_from = concept_title
                active_concept_row = parent_row
                concept_id = parent_row["concept_id"]
                concept_title = parent_row["title"]
                stuck_count = 0
                scaffolding_level = 1
                move = "prerequisite_step_back"
                active_passages = retrieve_chunks(
                    db_path,
                    query=concept_title,
                    book_id=book_id,
                    concept_id=concept_id,
                    mode="tutor",
                    limit=3,
                )
            else:
                move = "hint"
        elif diag.diagnosis == "misconception":
            move = "counter_example"
        elif diag.diagnosis == "stuck":
            move = "hint"
        else:
            move = "probe"

        # Stage 4: Draft & Response Guard
        draft = _draft_socratic_reply(
            move=move,
            concept_title=concept_title,
            passages=active_passages,
            scaffolding_level=scaffolding_level,
            stepped_back_from=stepped_back_from,
        )
        reply = guard_socratic_response(
            draft=draft,
            concept_title=concept_title,
            passages=active_passages,
            student_message=message,
        )

        citations = [_format_citation(c) for c in active_passages]
        conn.execute(
            "UPDATE tutoring_sessions SET concept_id=?, scaffolding_level=?, stuck_count=?, "
            "misconception_tags=?, turns_to_resolution=?, resolved=?, updated_at=? "
            "WHERE session_id=?",
            (
                concept_id,
                scaffolding_level,
                stuck_count,
                json.dumps(existing_tags),
                turns,
                resolved,
                now,
                session_id,
            ),
        )
        conn.execute(
            "INSERT INTO tutoring_messages("
            "message_id, session_id, role, content, diagnosis, pedagogical_move, "
            "scaffolding_level, citations_json, created_at"
            ") VALUES(?,?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), session_id, "student", message, diag.diagnosis, None, scaffolding_level, "[]", now),
        )
        conn.execute(
            "INSERT INTO tutoring_messages("
            "message_id, session_id, role, content, diagnosis, pedagogical_move, "
            "scaffolding_level, citations_json, created_at"
            ") VALUES(?,?,?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4()),
                session_id,
                "tutor",
                reply,
                diag.diagnosis,
                move,
                scaffolding_level,
                json.dumps(citations),
                now,
            ),
        )

        mastery = (
            _ensure_mastery_row(conn, user["user_id"], concept_id)
            if concept_id
            else {"elo_score": 1000, "status": "not_started"}
        )

    return {
        "session_id": session_id,
        "reply": reply,
        "diagnosis": diag.diagnosis,
        "misconception_tags": diag.misconception_tags,
        "pedagogical_move": move,
        "scaffolding_level": scaffolding_level,
        "stuck_count": stuck_count,
        "stepped_back": stepped_back,
        "concept": {
            "concept_id": active_concept_row["concept_id"] if active_concept_row else None,
            "title": concept_title,
            "parent_concept_id": active_concept_row["parent_concept_id"] if active_concept_row else None,
        },
        "citations": citations,
        "elo_score": mastery["elo_score"],
        "status": mastery["status"],
    }


@router.get("/sessions")
def list_sessions(
    request: Request,
    book_id: str | None = Query(default=None),
    user: dict[str, str] = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """List persisted tutoring sessions and messages for the active student."""
    with db_session(request.app.state.db_path) as conn:
        sql = (
            "SELECT s.*, c.title AS concept_title "
            "FROM tutoring_sessions s "
            "LEFT JOIN concepts c ON c.concept_id = s.concept_id "
            "WHERE s.student_id = ?"
        )
        params: list[Any] = [user["user_id"]]
        if book_id:
            sql += " AND s.book_id = ?"
            params.append(book_id)
        sql += " ORDER BY s.updated_at DESC"
        sessions = conn.execute(sql, tuple(params)).fetchall()

        output: list[dict[str, Any]] = []
        for s in sessions:
            msgs = conn.execute(
                "SELECT message_id, role, content, diagnosis, pedagogical_move, "
                "scaffolding_level, citations_json, created_at "
                "FROM tutoring_messages WHERE session_id=? ORDER BY created_at, rowid",
                (s["session_id"],),
            ).fetchall()
            item = dict(s)
            item["misconception_tags"] = json.loads(s["misconception_tags"] or "[]")
            item["messages"] = [
                {
                    **dict(m),
                    "citations": json.loads(m["citations_json"] or "[]"),
                }
                for m in msgs
            ]
            output.append(item)
    return output
