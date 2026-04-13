"""Audit trail helper — writes structured events to the audit_logs table."""
import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from .models import AuditLog

logger = logging.getLogger("p2p.audit")


def log_event(
    db: Session,
    *,
    action: str,
    entity_type: str,
    entity_id: int | None = None,
    workflow_id: str | None = None,
    request_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Persist an audit event and log it."""
    detail_str = json.dumps(detail) if detail else None
    entry = AuditLog(
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        workflow_id=workflow_id,
        request_id=request_id,
        detail=detail_str,
    )
    db.add(entry)
    # Don't commit — caller's transaction will commit
    logger.info(
        "workflow=%s action=%s %s_id=%s %s",
        workflow_id, action, entity_type, entity_id,
        detail_str or "",
    )
