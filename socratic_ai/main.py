"""FastAPI application entrypoint for Socratic AI."""

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from socratic_ai.analytics import router as analytics_router
from socratic_ai.auth import router as auth_router
from socratic_ai.db import DEFAULT_DB_PATH, init_db
from socratic_ai.practice import router as practice_router
from socratic_ai.rag import router as rag_router
from socratic_ai.tutor import router as tutor_router

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_UPLOAD_DIR = BASE_DIR / "uploads"
STATIC_DIR = BASE_DIR / "static"


def create_app(
    db_path: Path | str = DEFAULT_DB_PATH,
    upload_dir: Path | str = DEFAULT_UPLOAD_DIR,
) -> FastAPI:
    """Create and configure the FastAPI application instance."""
    resolved_db = Path(db_path)
    resolved_uploads = Path(upload_dir)
    resolved_uploads.mkdir(parents=True, exist_ok=True)
    init_db(resolved_db)

    application = FastAPI(title="Socratic AI Tutor", version="1.0.0")
    application.state.db_path = resolved_db
    application.state.upload_dir = resolved_uploads

    secret_key = os.environ.get("SOCRATIC_SECRET_KEY", "socratic-dev-secret-key-change-me")
    application.add_middleware(
        SessionMiddleware,
        secret_key=secret_key,
        session_cookie="socratic_session",
        same_site="lax",
        https_only=False,
    )

    application.include_router(auth_router)
    application.include_router(rag_router)
    application.include_router(tutor_router)
    application.include_router(practice_router)
    application.include_router(analytics_router)

    @application.get("/api/health")
    def health() -> dict[str, str]:
        """Return service health status."""
        return {"status": "ok"}

    if STATIC_DIR.exists():
        application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

        @application.get("/", include_in_schema=False)
        def serve_index() -> FileResponse:
            """Serve the single-page web UI."""
            return FileResponse(STATIC_DIR / "index.html")
    else:
        @application.get("/", include_in_schema=False)
        def serve_placeholder() -> JSONResponse:
            """Fallback root response before static UI assets are built."""
            return JSONResponse({"service": "Socratic AI Tutor", "status": "ok"})

    return application


app = create_app()
