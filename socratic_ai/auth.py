"""Authentication, password hashing, and role-based access control."""

from datetime import datetime, timezone
import hashlib
import hmac
import secrets
from typing import Any, Literal
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from socratic_ai.db import db_session

router = APIRouter(prefix="/api/auth", tags=["auth"])


def hash_password(password: str) -> str:
    """Create a salted PBKDF2-SHA256 password hash."""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000
    ).hex()
    return f"pbkdf2_sha256${salt}${digest}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify a plaintext password against a stored PBKDF2-SHA256 hash."""
    try:
        scheme, salt, expected = stored_hash.split("$", 2)
    except ValueError:
        return False
    if scheme != "pbkdf2_sha256":
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000
    ).hex()
    return hmac.compare_digest(actual, expected)


class RegisterRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    password: str = Field(..., min_length=6, max_length=200)
    role: Literal["student", "teacher"] = "student"
    class_name: str = Field(default="Physics 101", max_length=100)


class LoginRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=1, max_length=200)


def _session_user_dict(row: Any) -> dict[str, str]:
    return {
        "user_id": row["user_id"],
        "name": row["name"],
        "role": row["role"],
        "class_name": row["class_name"],
    }


def get_current_user(request: Request) -> dict[str, str]:
    """Dependency returning the authenticated user or raising 401."""
    session_user = request.session.get("user")
    if not session_user or not session_user.get("user_id"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )
    db_path = request.app.state.db_path
    with db_session(db_path) as conn:
        row = conn.execute(
            "SELECT user_id, name, role, class_name FROM users WHERE user_id=?",
            (session_user["user_id"],),
        ).fetchone()
    if not row:
        request.session.clear()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )
    return _session_user_dict(row)


def require_teacher(
    user: dict[str, str] = Depends(get_current_user),
) -> dict[str, str]:
    """Dependency restricting access to authenticated teachers."""
    if user["role"] != "teacher":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Teacher role required.",
        )
    return user


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, request: Request) -> dict[str, Any]:
    """Register a new student or teacher account and start a session."""
    name = payload.name.strip()
    if len(name) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Name must be at least 2 characters.",
        )
    class_name = payload.class_name.strip() or "Physics 101"
    user_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    password_hash = hash_password(payload.password)

    db_path = request.app.state.db_path
    with db_session(db_path) as conn:
        existing = conn.execute(
            "SELECT user_id FROM users WHERE lower(name)=lower(?)", (name,)
        ).fetchone()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An account with this name already exists.",
            )
        conn.execute(
            "INSERT INTO users(user_id, name, role, class_name, password_hash, created_at)"
            " VALUES(?,?,?,?,?,?)",
            (user_id, name, payload.role, class_name, password_hash, created_at),
        )

    user = {
        "user_id": user_id,
        "name": name,
        "role": payload.role,
        "class_name": class_name,
    }
    request.session["user"] = user
    return {"user": user}


@router.post("/login")
def login(payload: LoginRequest, request: Request) -> dict[str, Any]:
    """Authenticate an existing user and set the session cookie."""
    name = payload.name.strip()
    db_path = request.app.state.db_path
    with db_session(db_path) as conn:
        row = conn.execute(
            "SELECT user_id, name, role, class_name, password_hash "
            "FROM users WHERE lower(name)=lower(?)",
            (name,),
        ).fetchone()

    if not row or not verify_password(payload.password, row["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Name or password is incorrect.",
        )

    user = _session_user_dict(row)
    request.session["user"] = user
    return {"user": user}


@router.get("/me")
def me(request: Request) -> dict[str, Any]:
    """Return the current authenticated session state."""
    session_user = request.session.get("user")
    if not session_user or not session_user.get("user_id"):
        return {"authenticated": False}
    db_path = request.app.state.db_path
    with db_session(db_path) as conn:
        row = conn.execute(
            "SELECT user_id, name, role, class_name FROM users WHERE user_id=?",
            (session_user["user_id"],),
        ).fetchone()
    if not row:
        request.session.clear()
        return {"authenticated": False}
    return {"authenticated": True, "user": _session_user_dict(row)}


@router.post("/logout")
def logout(request: Request) -> dict[str, bool]:
    """Clear the active user session."""
    request.session.clear()
    return {"authenticated": False}


@router.get("/class-students")
def list_class_students(
    request: Request,
    teacher: dict[str, str] = Depends(require_teacher),
) -> dict[str, Any]:
    """List students enrolled in the authenticated teacher's class."""
    db_path = request.app.state.db_path
    with db_session(db_path) as conn:
        rows = conn.execute(
            "SELECT user_id, name, class_name FROM users "
            "WHERE role='student' AND class_name=? ORDER BY name",
            (teacher["class_name"],),
        ).fetchall()
    return {
        "class_name": teacher["class_name"],
        "students": [dict(row) for row in rows],
    }
