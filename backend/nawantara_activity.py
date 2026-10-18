"""Admin UI, authenticated worker gateway and durable request metadata feed."""

from __future__ import annotations

import hmac
import json
import os
import sqlite3
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import Response
from open_webui.internal.db import get_async_db_context
from open_webui.models.files import File
from open_webui.models.users import User, UserModel
from open_webui.utils.auth import get_admin_user
from pydantic import BaseModel, Field
from sqlalchemy import select

router = APIRouter()
DATA = Path(os.environ.get("DATA_DIR", "/app/backend/data"))
Admin = Annotated[UserModel, Depends(get_admin_user)]


def connection() -> sqlite3.Connection:
    db = sqlite3.connect(DATA / "document-request-events.sqlite3", timeout=10)
    db.execute(
        "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, record TEXT NOT NULL)"
    )
    return db


def token() -> str:
    return (
        Path(
            os.environ.get(
                "COMPANY_INGEST_TOKEN_FILE", "/run/secrets/company-ingest-token"
            )
        )
        .read_text()
        .strip()
    )


async def gateway(payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            response = await client.post(
                os.environ.get("COMPANY_INGEST_URL", "http://rms-sync:8090") + "/admin",
                json=payload,
                headers={"Authorization": "Bearer " + token()},
            )
        except (httpx.HTTPError, OSError):
            raise HTTPException(
                503, "Document worker unavailable. Try again shortly."
            ) from None
    if response.status_code == 400:
        raise HTTPException(409, response.json().get("error", "Action unavailable"))
    if response.status_code != 200:
        raise HTTPException(503, "Document worker unavailable. Try again shortly.")
    return response.json()


@router.get("/api/v1/document-activity/{kind}")
async def listing(kind: str, request: Request, user: Admin) -> dict:
    if kind not in {"jobs", "events"}:
        raise HTTPException(404, "Not found")
    allowed = {
        "status",
        "actor_id",
        "action",
        "origin",
        "scope",
        "document_id",
        "from",
        "to",
        "page",
    }
    filters = {k: v[:200] for k, v in request.query_params.items() if k in allowed}
    return await gateway({"operation": kind, "filters": filters})


@router.get("/api/v1/document-activity/jobs/{job_id}")
async def detail(job_id: str, user: Admin) -> dict:
    return await gateway({"operation": "detail", "job_id": job_id})


class Control(BaseModel):
    operation: str = Field(pattern="^(cancel|retry)$")


@router.post("/api/v1/document-activity/jobs/{job_id}")
async def control(job_id: str, body: Control, user: Admin, request: Request) -> dict:
    # Require a Bearer header for mutations, preventing ambient cookie CSRF.
    if not request.headers.get("authorization", "").startswith("Bearer "):
        raise HTTPException(403, "An authenticated session header is required")
    return await gateway(
        {
            "operation": body.operation,
            "job_id": job_id,
            "actor": {
                "actor_id": user.id,
                "actor_name": user.name,
                "actor_role": user.role,
            },
        }
    )


@router.get("/api/internal/document-activity", include_in_schema=False)
async def feed(request: Request, after: str = "0", kind: str = "events") -> dict:
    if not hmac.compare_digest(
        request.headers.get("authorization", "").encode(),
        ("Bearer " + token()).encode(),
    ):
        raise HTTPException(401, "Authentication required")
    if kind == "files":
        return await file_jobs(after if after != "0" else "")
    db = connection()
    try:
        rows = db.execute(
            "SELECT id, record FROM events WHERE id > ? ORDER BY id LIMIT 200", (after,)
        ).fetchall()
        return {
            "events": [{"sequence": key, **json.loads(record)} for key, record in rows],
            "cursor": rows[-1][0] if rows else after,
        }
    finally:
        db.close()


async def file_jobs(after: str) -> dict:
    # Project operational fields only; never load filenames, extracted text or chat data.
    statement = (
        select(
            File.id,
            File.user_id,
            File.created_at,
            File.updated_at,
            File.data["status"].as_string().label("status"),
            User.name,
            User.role,
        )
        .outerjoin(User, File.user_id == User.id)
        .where(File.id > after)
        .order_by(File.id)
        .limit(200)
    )
    async with get_async_db_context() as db:
        rows = (await db.execute(statement)).mappings().all()
    jobs = []
    for row in rows:
        if row["status"] not in {"pending", "processing", "completed", "failed"}:
            continue
        jobs.append(
            {
                "job_id": "file-" + row["id"],
                "resource_id": row["id"],
                "actor_id": row["user_id"],
                "actor_name": row["name"] or "Unknown (deleted account)",
                "actor_role": row["role"],
                "attribution": "File owner reported by Nawantara",
                "action": "file_indexing",
                "stage": "file_processing",
                "origin": "nawantara",
                "scope": "restricted",
                "controllable": False,
                "source_reported": True,
                "status": "queued" if row["status"] == "pending" else row["status"],
                "created_at": datetime.fromtimestamp(row["created_at"], UTC).isoformat()
                if row["created_at"]
                else None,
                "updated_at": datetime.fromtimestamp(row["updated_at"], UTC).isoformat()
                if row["updated_at"]
                else None,
                "started_at": None,
                "finished_at": None,
                "error": "File processing failed. The file owner can inspect or reprocess the upload."
                if row["status"] == "failed"
                else None,
            }
        )
    return {
        "jobs": jobs,
        "cursor": rows[-1]["id"] if rows else after,
        "more": len(rows) == 200,
    }


def record_request(request: Request, status: int) -> None:
    user = request.scope.get("state", {}).get("user")
    if user is None:
        return
    internal = hmac.compare_digest(
        request.headers.get("X-Nawantara-Worker", "").encode(), token().encode()
    )
    if internal and request.method in {"GET", "HEAD"}:
        return
    parts = request.url.path.strip("/").split("/")
    resource = parts[2]
    identity = parts[3] if len(parts) > 3 else None
    record = {
        "occurred_at": datetime.now(UTC).isoformat(),
        "origin": "nawantara",
        "actor_id": user.id,
        "actor_name": user.name,
        "actor_role": user.role,
        "action": request.method.lower() + "_" + resource,
        "resource_id": identity,
        "scope": "restricted" if resource == "files" else "knowledge",
        "status": "accepted"
        if status == 202
        else ("completed" if status < 400 else "failed"),
        "http_status": status,
    }
    if internal:
        record.update(
            actor_id="system",
            actor_name="System",
            actor_role="system",
            executor_account_id=user.id,
            job_id=request.headers.get("X-Nawantara-Job") or None,
        )
    db = connection()
    try:
        with db:
            db.execute("INSERT INTO events(record) VALUES (?)", (json.dumps(record),))
    finally:
        db.close()


def install(app: FastAPI) -> None:
    app.include_router(router)

    @app.middleware("http")
    async def document_requests(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        tracked = request.url.path.startswith(("/api/v1/files/", "/api/v1/knowledge/"))
        response = await call_next(request)
        if tracked and not request.url.path.endswith("/process/status"):
            record_request(request, response.status_code)
        if request.url.path.startswith("/api/v1/document-activity/"):
            response.headers["Cache-Control"] = "no-store"
        return response
