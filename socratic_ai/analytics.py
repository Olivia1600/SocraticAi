"""Teacher class-level analytics: mastery heatmap, misconception clusters, alerts, and assignments."""

from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from socratic_ai.auth import require_teacher
from socratic_ai.db import db_session
from socratic_ai.practice import create_practice_item

router = APIRouter(prefix="/api/teacher", tags=["teacher"])


class CreateAssignmentRequest(BaseModel):
    concept_id: str = Field(..., min_length=1)
    student_ids: list[str] = Field(default_factory=list)
    title: str | None = Field(default=None, max_length=200)


def _heatmap_band(elo_score: int, attempts_count: int) -> str:
    if attempts_count <= 0:
        return "not_started"
    if elo_score < 900:
        return "struggling"
    if elo_score <= 1150:
        return "developing"
    return "mastered"


@router.get("/dashboard")
def teacher_dashboard(
    request: Request,
    book_id: str | None = Query(default=None),
    teacher: dict[str, str] = Depends(require_teacher),
) -> dict[str, Any]:
    """Return class mastery heatmap, misconception clusters, and intervention alerts."""
    class_name = teacher["class_name"]
    db_path: Path = request.app.state.db_path

    with db_session(db_path) as conn:
        students = conn.execute(
            "SELECT user_id, name FROM users WHERE role='student' AND class_name=? ORDER BY name",
            (class_name,),
        ).fetchall()

        concept_sql = (
            "SELECT concept_id, book_id, title, chapter_number, section_label "
            "FROM concepts "
        )
        params: list[Any] = []
        if book_id:
            concept_sql += "WHERE book_id=? "
            params.append(book_id)
        concept_sql += "ORDER BY chapter_number, order_index"
        concepts = conn.execute(concept_sql, tuple(params)).fetchall()

        student_ids = [s["user_id"] for s in students]
        mastery_map: dict[tuple[str, str], dict[str, Any]] = {}
        if student_ids:
            placeholders = ",".join("?" for _ in student_ids)
            m_rows = conn.execute(
                f"SELECT * FROM student_concept_mastery WHERE student_id IN ({placeholders})",
                tuple(student_ids),
            ).fetchall()
            for r in m_rows:
                mastery_map[(r["student_id"], r["concept_id"])] = dict(r)

        heatmap_rows: list[dict[str, Any]] = []
        alerts: list[dict[str, Any]] = []
        concept_lookup = {c["concept_id"]: dict(c) for c in concepts}

        for student in students:
            sid = student["user_id"]
            cells: list[dict[str, Any]] = []
            attempted_elos: list[int] = []
            for concept in concepts:
                cid = concept["concept_id"]
                m = mastery_map.get((sid, cid))
                elo = m["elo_score"] if m else 1000
                attempts = m["attempts_count"] if m else 0
                hints_avg = m["hints_used_avg"] if m else 0.0
                drops = m["consecutive_drops"] if m else 0
                cell_status = m["status"] if m else "not_started"
                band = _heatmap_band(elo, attempts)
                if attempts > 0:
                    attempted_elos.append(elo)
                cells.append(
                    {
                        "concept_id": cid,
                        "concept_title": concept["title"],
                        "elo_score": elo,
                        "attempts_count": attempts,
                        "hints_used_avg": hints_avg,
                        "status": cell_status,
                        "band": band,
                    }
                )

                reasons: list[str] = []
                if hints_avg > 2.5:
                    reasons.append("high_hint_usage")
                if drops >= 2:
                    reasons.append("consecutive_elo_drops")
                if reasons:
                    alerts.append(
                        {
                            "student_id": sid,
                            "student_name": student["name"],
                            "concept_id": cid,
                            "concept_title": concept["title"],
                            "elo_score": elo,
                            "hints_used_avg": hints_avg,
                            "consecutive_drops": drops,
                            "reasons": reasons,
                        }
                    )

            avg_elo = round(sum(attempted_elos) / len(attempted_elos)) if attempted_elos else 1000
            heatmap_rows.append(
                {
                    "student_id": sid,
                    "student_name": student["name"],
                    "average_elo": avg_elo,
                    "cells": cells,
                }
            )

        # Aggregate misconception clusters across tutoring sessions and practice items
        cluster_counts: dict[tuple[str, str], set[str]] = defaultdict(set)
        cluster_occurrences: dict[tuple[str, str], int] = defaultdict(int)

        if student_ids:
            placeholders = ",".join("?" for _ in student_ids)
            tut_rows = conn.execute(
                f"SELECT student_id, concept_id, misconception_tags FROM tutoring_sessions "
                f"WHERE student_id IN ({placeholders}) AND concept_id IS NOT NULL",
                tuple(student_ids),
            ).fetchall()
            prac_rows = conn.execute(
                f"SELECT student_id, concept_id, misconception_tags FROM practice_items "
                f"WHERE student_id IN ({placeholders}) AND completed=1",
                tuple(student_ids),
            ).fetchall()

            for row in list(tut_rows) + list(prac_rows):
                cid = row["concept_id"]
                if book_id and cid not in concept_lookup:
                    continue
                tags = json.loads(row["misconception_tags"] or "[]")
                for tag in tags:
                    key = (cid, str(tag))
                    cluster_counts[key].add(row["student_id"])
                    cluster_occurrences[key] += 1

        total_students = max(len(students), 1)
        misconception_clusters: list[dict[str, Any]] = []
        for (cid, tag), occ in cluster_occurrences.items():
            affected = len(cluster_counts[(cid, tag)])
            c_info = concept_lookup.get(cid)
            title = c_info["title"] if c_info else "Concept"
            misconception_clusters.append(
                {
                    "concept_id": cid,
                    "concept_title": title,
                    "tag": tag,
                    "occurrences": occ,
                    "affected_students": affected,
                    "affected_pct": round((affected / total_students) * 100, 1),
                }
            )
        misconception_clusters.sort(
            key=lambda item: (item["affected_students"], item["occurrences"]),
            reverse=True,
        )

    return {
        "class_name": class_name,
        "concepts": [dict(c) for c in concepts],
        "heatmap": heatmap_rows,
        "misconception_clusters": misconception_clusters[:10],
        "intervention_alerts": alerts,
    }


@router.post("/assignments", status_code=status.HTTP_201_CREATED)
def assign_practice(
    payload: CreateAssignmentRequest,
    request: Request,
    teacher: dict[str, str] = Depends(require_teacher),
) -> dict[str, Any]:
    """Push curated ZPD practice assignments for a weak concept to selected or all class students."""
    db_path: Path = request.app.state.db_path
    class_name = teacher["class_name"]
    now = datetime.now(timezone.utc).isoformat()

    with db_session(db_path) as conn:
        concept = conn.execute(
            "SELECT concept_id, book_id, title FROM concepts WHERE concept_id=?",
            (payload.concept_id,),
        ).fetchone()
        if not concept:
            raise HTTPException(status_code=404, detail="Concept not found.")

        if payload.student_ids:
            placeholders = ",".join("?" for _ in payload.student_ids)
            students = conn.execute(
                f"SELECT user_id, name FROM users "
                f"WHERE role='student' AND class_name=? AND user_id IN ({placeholders})",
                (class_name, *payload.student_ids),
            ).fetchall()
        else:
            students = conn.execute(
                "SELECT user_id, name FROM users WHERE role='student' AND class_name=?",
                (class_name,),
            ).fetchall()

        if not students:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No matching students found in your class.",
            )

        assignment_title = (
            payload.title.strip()
            if payload.title and payload.title.strip()
            else f"Targeted Practice: {concept['title']}"
        )
        created: list[dict[str, Any]] = []
        for student in students:
            assignment_id = str(uuid.uuid4())
            item = create_practice_item(
                conn=conn,
                db_path=db_path,
                student_id=student["user_id"],
                book_id=concept["book_id"],
                concept_id=concept["concept_id"],
                assignment_id=assignment_id,
            )
            conn.execute(
                "INSERT INTO assignments("
                "assignment_id, teacher_id, class_name, concept_id, student_id, "
                "question_id, title, status, created_at"
                ") VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    assignment_id,
                    teacher["user_id"],
                    class_name,
                    concept["concept_id"],
                    student["user_id"],
                    item["question_id"],
                    assignment_title,
                    "pending",
                    now,
                ),
            )
            created.append(
                {
                    "assignment_id": assignment_id,
                    "student_id": student["user_id"],
                    "student_name": student["name"],
                    "question_id": item["question_id"],
                    "title": assignment_title,
                    "status": "pending",
                }
            )

    return {"assigned_count": len(created), "assignments": created}
