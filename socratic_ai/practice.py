"""Elo-based concept mastery tracking and ZPD adaptive practice engine."""

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from socratic_ai.auth import get_current_user
from socratic_ai.db import db_session
from socratic_ai.llm import default_llm
from socratic_ai.rag import TOKEN_RE, retrieve_chunks
from socratic_ai.tutor import OPPOSITE_PAIRS, STOP_WORDS

router = APIRouter(prefix="/api/practice", tags=["practice"])


class GeneratePracticeRequest(BaseModel):
    book_id: str | None = None
    concept_id: str | None = None


class SubmitPracticeAnswerRequest(BaseModel):
    answer: str = Field(default="", max_length=3000)
    give_up: bool = False


def derive_mastery_status(elo_score: int, attempts_count: int) -> str:
    """Map Elo score and attempts count to mastery band status."""
    if attempts_count <= 0:
        return "not_started"
    if elo_score < 900:
        return "struggling"
    if elo_score <= 1150:
        return "developing"
    return "mastered"


def compute_elo_delta(is_correct: bool, hints_used: int) -> int:
    """Compute hint-weighted Elo change for a completed practice item."""
    if not is_correct:
        return -20
    if hints_used <= 0:
        return 32
    if hints_used == 1:
        return 20
    return 8


def _extract_solution_steps(concept_title: str, excerpt: str) -> list[str]:
    sentences = [s.strip() for s in re.split(r"[.!?]+", excerpt) if len(s.strip()) >= 12]
    steps = sentences[:3] if sentences else [f"Identify the core principle of {concept_title}."]
    return steps


def _build_common_misconceptions(excerpt: str) -> list[dict[str, str]]:
    lower = excerpt.lower()
    traps: list[dict[str, str]] = []
    for a, b in OPPOSITE_PAIRS:
        if a in lower:
            traps.append({"tag": f"inverted_{a}_vs_{b}", "trigger": b, "expected": a})
        elif b in lower:
            traps.append({"tag": f"inverted_{b}_vs_{a}", "trigger": a, "expected": b})
    if not traps:
        traps.append({"tag": "insufficient_evidence", "trigger": "", "expected": ""})
    return traps


def create_practice_item(
    conn: Any,
    db_path: Path | str,
    student_id: str,
    book_id: str | None = None,
    concept_id: str | None = None,
    assignment_id: str | None = None,
) -> dict[str, Any]:
    """Generate and persist a ZPD-calibrated practice problem for a student and concept."""
    if not concept_id:
        if book_id:
            row = conn.execute(
                "SELECT concept_id FROM concepts WHERE book_id=? ORDER BY order_index LIMIT 1",
                (book_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT concept_id FROM concepts ORDER BY order_index LIMIT 1"
            ).fetchone()
        if not row:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Upload a textbook before generating practice questions.",
            )
        concept_id = row["concept_id"]

    concept_row = conn.execute(
        "SELECT concept_id, book_id, title FROM concepts WHERE concept_id=?",
        (concept_id,),
    ).fetchone()
    if not concept_row:
        raise HTTPException(status_code=404, detail="Concept not found.")

    resolved_book_id = book_id or concept_row["book_id"]
    concept_title = concept_row["title"]

    mastery = conn.execute(
        "SELECT elo_score, attempts_count, status FROM student_concept_mastery "
        "WHERE student_id=? AND concept_id=?",
        (student_id, concept_id),
    ).fetchone()
    elo_score = mastery["elo_score"] if mastery else 1000
    attempts_count = mastery["attempts_count"] if mastery else 0
    mastery_status = mastery["status"] if mastery else "not_started"
    difficulty_score = elo_score + 50

    passages = retrieve_chunks(
        db_path,
        query=concept_title,
        book_id=resolved_book_id,
        concept_id=concept_id,
        mode="practice",
        limit=2,
    )
    excerpt = passages[0]["text"] if passages else f"Core principles of {concept_title}."

    llm_item = default_llm.generate_json(
        "Generate a Socratic practice problem calibrated to the target Elo difficulty. "
        "Return JSON with keys: question_text (str), hidden_solution_steps (list[str]), "
        "common_misconceptions (list[{tag, trigger, expected}]).",
        f"Concept: {concept_title}\nTarget difficulty: {difficulty_score}\nExcerpt: {excerpt[:800]}",
    )

    if llm_item and isinstance(llm_item.get("question_text"), str):
        question_text = llm_item["question_text"].strip()
        steps = [str(s) for s in llm_item.get("hidden_solution_steps", [])] or _extract_solution_steps(
            concept_title, excerpt
        )
        misconceptions = llm_item.get("common_misconceptions") or _build_common_misconceptions(excerpt)
    else:
        if difficulty_score < 1000:
            question_text = (
                f"In your own words, what is the primary relationship defined by {concept_title}, "
                f"and which textbook variables does it connect?"
            )
        elif difficulty_score <= 1200:
            question_text = (
                f"Explain how {concept_title} operates in a concrete scenario, citing the core rule "
                f"or evidence that justifies your reasoning."
            )
        else:
            question_text = (
                f"Construct a novel edge case applying {concept_title} and justify what happens "
                f"when one governing quantity doubles while others stay fixed."
            )
        steps = _extract_solution_steps(concept_title, excerpt)
        misconceptions = _build_common_misconceptions(excerpt)

    question_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO practice_items("
        "question_id, student_id, book_id, concept_id, assignment_id, difficulty_score, "
        "question_text, hidden_solution_steps, common_misconceptions, source_excerpt, "
        "hints_used, completed, created_at"
        ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            question_id,
            student_id,
            resolved_book_id,
            concept_id,
            assignment_id,
            difficulty_score,
            question_text,
            json.dumps(steps),
            json.dumps(misconceptions),
            excerpt[:1000],
            0,
            0,
            now,
        ),
    )

    return {
        "question_id": question_id,
        "book_id": resolved_book_id,
        "concept_id": concept_id,
        "concept_title": concept_title,
        "difficulty_score": difficulty_score,
        "question_text": question_text,
        "elo_score": elo_score,
        "attempts_count": attempts_count,
        "status": mastery_status,
        "hints_used": 0,
    }


@router.post("/generate", status_code=status.HTTP_201_CREATED)
def generate_practice(
    payload: GeneratePracticeRequest,
    request: Request,
    user: dict[str, str] = Depends(get_current_user),
) -> dict[str, Any]:
    """Generate a ZPD-calibrated practice problem without exposing hidden solution steps."""
    db_path: Path = request.app.state.db_path
    with db_session(db_path) as conn:
        return create_practice_item(
            conn=conn,
            db_path=db_path,
            student_id=user["user_id"],
            book_id=payload.book_id,
            concept_id=payload.concept_id,
        )


@router.post("/{question_id}/hint")
def request_practice_hint(
    question_id: str,
    request: Request,
    user: dict[str, str] = Depends(get_current_user),
) -> dict[str, Any]:
    """Provide a progressive Socratic hint and record hint usage on the practice item."""
    db_path: Path = request.app.state.db_path
    with db_session(db_path) as conn:
        item = conn.execute(
            "SELECT * FROM practice_items WHERE question_id=? AND student_id=?",
            (question_id, user["user_id"]),
        ).fetchone()
        if not item:
            raise HTTPException(status_code=404, detail="Practice item not found.")

        hints_used = item["hints_used"] + 1
        conn.execute(
            "UPDATE practice_items SET hints_used=? WHERE question_id=?",
            (hints_used, question_id),
        )
        steps: list[str] = json.loads(item["hidden_solution_steps"] or "[]")
        step_index = min(hints_used - 1, max(len(steps) - 1, 0))
        clue_tokens = [
            t for t in TOKEN_RE.findall(steps[step_index]) if len(t) >= 4 and t.lower() not in STOP_WORDS
        ][:3] if steps else []
        clue_phrase = ", ".join(clue_tokens) if clue_tokens else "the core textbook definition"

    return {
        "question_id": question_id,
        "hints_used": hints_used,
        "hint": (
            f"Hint {hints_used}: Focus on how {clue_phrase} connects to what the problem asks. "
            f"Which relationship links those pieces?"
        ),
    }


def _evaluate_answer(
    answer: str,
    give_up: bool,
    concept_title: str,
    hidden_steps: list[str],
    common_misconceptions: list[dict[str, str]],
    source_excerpt: str,
) -> tuple[bool, list[str], str]:
    if give_up or not answer.strip():
        return (
            False,
            ["gave_up"],
            "Let's revisit the foundational textbook rule first—which key quantities are involved?",
        )

    lower = answer.lower()
    detected_tags: list[str] = []
    for trap in common_misconceptions:
        trigger = str(trap.get("trigger", "")).lower().strip()
        expected = str(trap.get("expected", "")).lower().strip()
        if trigger and trigger in lower and (not expected or expected not in lower):
            detected_tags.append(str(trap["tag"]))

    if detected_tags:
        return (
            False,
            detected_tags,
            f"Check your reasoning on {concept_title}: one relationship looks inverted compared to the textbook. "
            f"What happens if you test the opposite direction?",
        )

    ref_text = f"{concept_title} {' '.join(hidden_steps)} {source_excerpt}"
    ref_tokens = {
        t.lower() for t in TOKEN_RE.findall(ref_text) if len(t) >= 4 and t.lower() not in STOP_WORDS
    }
    ans_tokens = {
        t.lower() for t in TOKEN_RE.findall(answer) if len(t) >= 4 and t.lower() not in STOP_WORDS
    }
    overlap = len(ans_tokens & ref_tokens)

    if len(answer.strip()) >= 20 and overlap >= 2:
        return (
            True,
            [],
            f"Strong reasoning on {concept_title}! Can you predict how your conclusion changes under an extreme boundary case?",
        )

    return (
        False,
        ["incomplete_reasoning"],
        f"Good start, but ground your explanation in specific concepts from {concept_title}. "
        f"Which definition or law justifies your claim?",
    )


@router.post("/{question_id}/answer")
def submit_practice_answer(
    question_id: str,
    payload: SubmitPracticeAnswerRequest,
    request: Request,
    user: dict[str, str] = Depends(get_current_user),
) -> dict[str, Any]:
    """Evaluate a student's practice answer, update Elo mastery, and log misconceptions."""
    db_path: Path = request.app.state.db_path
    now = datetime.now(timezone.utc).isoformat()

    with db_session(db_path) as conn:
        item = conn.execute(
            "SELECT p.*, c.title AS concept_title "
            "FROM practice_items p "
            "JOIN concepts c ON c.concept_id = p.concept_id "
            "WHERE p.question_id=? AND p.student_id=?",
            (question_id, user["user_id"]),
        ).fetchone()
        if not item:
            raise HTTPException(status_code=404, detail="Practice item not found.")

        hidden_steps: list[str] = json.loads(item["hidden_solution_steps"] or "[]")
        common_misconceptions: list[dict[str, str]] = json.loads(
            item["common_misconceptions"] or "[]"
        )

        is_correct, misconception_tags, feedback = _evaluate_answer(
            answer=payload.answer,
            give_up=payload.give_up,
            concept_title=item["concept_title"],
            hidden_steps=hidden_steps,
            common_misconceptions=common_misconceptions,
            source_excerpt=item["source_excerpt"],
        )

        conn.execute(
            "UPDATE practice_items SET completed=1, is_correct=?, misconception_tags=?, "
            "student_answer=?, feedback=?, completed_at=? WHERE question_id=?",
            (
                1 if is_correct else 0,
                json.dumps(misconception_tags),
                payload.answer.strip(),
                feedback,
                now,
                question_id,
            ),
        )
        if item["assignment_id"]:
            conn.execute(
                "UPDATE assignments SET status='completed' WHERE assignment_id=?",
                (item["assignment_id"],),
            )

        mastery = conn.execute(
            "SELECT * FROM student_concept_mastery WHERE student_id=? AND concept_id=?",
            (user["user_id"], item["concept_id"]),
        ).fetchone()
        prev_elo = mastery["elo_score"] if mastery else 1000
        prev_attempts = mastery["attempts_count"] if mastery else 0
        prev_hints_total = mastery["hints_used_total"] if mastery else 0
        prev_drops = mastery["consecutive_drops"] if mastery else 0

        delta = compute_elo_delta(is_correct=is_correct, hints_used=item["hints_used"])
        new_elo = max(600, min(2400, prev_elo + delta))
        attempts_count = prev_attempts + 1
        hints_used_total = prev_hints_total + item["hints_used"]
        hints_used_avg = round(hints_used_total / attempts_count, 2)
        consecutive_drops = 0 if delta >= 0 else prev_drops + 1
        new_status = derive_mastery_status(new_elo, attempts_count)

        conn.execute(
            "INSERT INTO student_concept_mastery("
            "student_id, concept_id, elo_score, attempts_count, hints_used_total, "
            "hints_used_avg, consecutive_drops, last_elo_delta, status, last_practiced"
            ") VALUES(?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(student_id, concept_id) DO UPDATE SET "
            "elo_score=?, attempts_count=?, hints_used_total=?, hints_used_avg=?, "
            "consecutive_drops=?, last_elo_delta=?, status=?, last_practiced=?",
            (
                user["user_id"],
                item["concept_id"],
                new_elo,
                attempts_count,
                hints_used_total,
                hints_used_avg,
                consecutive_drops,
                delta,
                new_status,
                now,
                new_elo,
                attempts_count,
                hints_used_total,
                hints_used_avg,
                consecutive_drops,
                delta,
                new_status,
                now,
            ),
        )

    return {
        "question_id": question_id,
        "correct": is_correct,
        "feedback": feedback,
        "misconception_tags": misconception_tags,
        "elo_score": new_elo,
        "elo_delta": delta,
        "attempts_count": attempts_count,
        "hints_used_avg": hints_used_avg,
        "status": new_status,
    }


@router.get("/progress")
def get_student_progress(
    request: Request,
    user: dict[str, str] = Depends(get_current_user),
) -> dict[str, Any]:
    """Return the authenticated student's per-concept Elo mastery and assigned practice."""
    with db_session(request.app.state.db_path) as conn:
        mastery_rows = conn.execute(
            "SELECT m.concept_id, c.title AS concept_title, c.chapter_number, "
            "t.book_id, t.title AS book_title, m.elo_score, m.attempts_count, "
            "m.hints_used_avg, m.consecutive_drops, m.status, m.last_practiced "
            "FROM student_concept_mastery m "
            "JOIN concepts c ON c.concept_id = m.concept_id "
            "JOIN textbooks t ON t.book_id = c.book_id "
            "WHERE m.student_id=? ORDER BY m.elo_score DESC, c.order_index",
            (user["user_id"],),
        ).fetchall()

        assignment_rows = conn.execute(
            "SELECT a.assignment_id, a.title, a.status, a.created_at, a.concept_id, "
            "c.title AS concept_title, p.question_id, p.question_text, p.difficulty_score, p.hints_used "
            "FROM assignments a "
            "JOIN concepts c ON c.concept_id = a.concept_id "
            "JOIN practice_items p ON p.question_id = a.question_id "
            "WHERE a.student_id=? ORDER BY a.created_at DESC",
            (user["user_id"],),
        ).fetchall()

    return {
        "mastery": [dict(r) for r in mastery_rows],
        "assignments": [dict(r) for r in assignment_rows],
    }
