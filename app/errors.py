"""Structured error envelope for agent-friendly error responses.

Every error — 4xx or 5xx — returns the same JSON shape so the consuming
agent can parse errors with a single code path.
"""
import logging
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from .database import SessionLocal
from .models import AuditLog

logger = logging.getLogger("p2p.audit")


class P2PError(Exception):
    """Raise from anywhere to produce a structured error response."""

    def __init__(
        self,
        status_code: int,
        error_code: str,
        message: str,
        context: dict[str, Any] | None = None,
        next_actions: list[str] | None = None,
        workflow_id: str | None = None,
    ):
        self.status_code = status_code
        self.error_code = error_code
        self.message = message
        self.context = context or {}
        self.next_actions = next_actions or []
        self.workflow_id = workflow_id


async def p2p_error_handler(_request: Request, exc: P2PError) -> JSONResponse:
    # Persist error to audit log
    db = SessionLocal()
    try:
        entry = AuditLog(
            action=f"error:{exc.error_code}",
            entity_type="error",
            entity_id=None,
            workflow_id=exc.workflow_id,
            detail=f'{{"status_code": {exc.status_code}, "error_code": "{exc.error_code}", '
                   f'"message": "{exc.message}", "context": {_safe_json(exc.context)}}}',
        )
        db.add(entry)
        db.commit()
    finally:
        db.close()

    logger.warning("error_code=%s status=%d message=%s",
                    exc.error_code, exc.status_code, exc.message)

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error_code": exc.error_code,
            "message": exc.message,
            "context": exc.context,
            "next_actions": exc.next_actions,
        },
    )


def _safe_json(obj: Any) -> str:
    import json
    try:
        return json.dumps(obj)
    except TypeError:
        return "{}"
