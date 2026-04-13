"""Audit dashboard — lightweight observability UI."""
import json
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy import desc
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import AuditLog

router = APIRouter(tags=["dashboard"])

DB = Annotated[Session, Depends(get_db)]


@router.get("/api/audit-logs")
def get_audit_logs(db: DB, workflow_id: str | None = None, limit: int = 100):
    """Return audit log entries, optionally filtered by workflow_id."""
    q = db.query(AuditLog).order_by(desc(AuditLog.timestamp))
    if workflow_id:
        q = q.filter(AuditLog.workflow_id == workflow_id)
    rows = q.limit(limit).all()
    return [
        {
            "id": r.id,
            "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            "workflow_id": r.workflow_id,
            "action": r.action,
            "entity_type": r.entity_type,
            "entity_id": r.entity_id,
            "detail": json.loads(r.detail) if r.detail else None,
        }
        for r in rows
    ]


@router.get("/api/workflows")
def get_workflows(db: DB):
    """Return distinct workflow_ids with their event counts."""
    from sqlalchemy import func
    rows = (
        db.query(
            AuditLog.workflow_id,
            func.count(AuditLog.id).label("events"),
            func.min(AuditLog.timestamp).label("started"),
            func.max(AuditLog.timestamp).label("last_activity"),
        )
        .filter(AuditLog.workflow_id.isnot(None))
        .group_by(AuditLog.workflow_id)
        .order_by(desc("last_activity"))
        .all()
    )
    return [
        {
            "workflow_id": r.workflow_id,
            "events": r.events,
            "started": r.started.isoformat() if r.started else None,
            "last_activity": r.last_activity.isoformat() if r.last_activity else None,
        }
        for r in rows
    ]


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    """Serve the audit dashboard UI."""
    return DASHBOARD_HTML


DASHBOARD_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>P2P Audit Dashboard</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
         background: #0f172a; color: #e2e8f0; padding: 24px; }
  h1 { font-size: 1.5rem; margin-bottom: 8px; color: #f8fafc; }
  .subtitle { color: #94a3b8; margin-bottom: 24px; font-size: 0.875rem; }
  .grid { display: grid; grid-template-columns: 340px 1fr; gap: 24px; height: calc(100vh - 120px); }
  .panel { background: #1e293b; border-radius: 12px; padding: 20px; overflow-y: auto; }
  .panel h2 { font-size: 0.875rem; text-transform: uppercase; letter-spacing: 0.05em;
               color: #64748b; margin-bottom: 12px; }

  /* Workflow list */
  .wf-card { padding: 12px; border-radius: 8px; cursor: pointer;
             border: 1px solid transparent; margin-bottom: 8px; transition: all 0.15s; }
  .wf-card:hover { background: #334155; }
  .wf-card.active { border-color: #3b82f6; background: #1e3a5f; }
  .wf-id { font-family: monospace; font-size: 0.8rem; color: #93c5fd; }
  .wf-meta { font-size: 0.75rem; color: #64748b; margin-top: 4px; }
  .wf-badge { display: inline-block; background: #3b82f6; color: #fff; border-radius: 10px;
              padding: 1px 8px; font-size: 0.7rem; margin-left: 6px; }

  /* Timeline */
  .timeline { position: relative; padding-left: 28px; }
  .timeline::before { content: ''; position: absolute; left: 10px; top: 0; bottom: 0;
                      width: 2px; background: #334155; }
  .event { position: relative; margin-bottom: 20px; }
  .event::before { content: ''; position: absolute; left: -22px; top: 6px; width: 10px; height: 10px;
                   border-radius: 50%; border: 2px solid #3b82f6; background: #1e293b; }
  .event.create_po::before { background: #22c55e; border-color: #22c55e; }
  .event.submit_po::before { background: #3b82f6; border-color: #3b82f6; }
  .event.receive_goods::before { background: #f59e0b; border-color: #f59e0b; }
  .event.create_invoice::before { background: #a855f7; border-color: #a855f7; }
  .event.match_invoice::before { background: #06b6d4; border-color: #06b6d4; }
  .event.approve_invoice::before { background: #10b981; border-color: #10b981; }
  .event-action { font-weight: 600; font-size: 0.9rem; }
  .event-time { font-size: 0.75rem; color: #64748b; margin-top: 2px; }
  .event-detail { font-size: 0.8rem; color: #94a3b8; margin-top: 6px;
                  background: #0f172a; padding: 8px 12px; border-radius: 6px;
                  font-family: monospace; white-space: pre-wrap; }

  .action-label { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem;
                  font-weight: 600; margin-right: 8px; }
  .action-label.create_po { background: #166534; color: #bbf7d0; }
  .action-label.submit_po { background: #1e40af; color: #bfdbfe; }
  .action-label.receive_goods { background: #92400e; color: #fde68a; }
  .action-label.create_invoice { background: #6b21a8; color: #e9d5ff; }
  .action-label.match_invoice { background: #155e75; color: #a5f3fc; }
  .action-label.approve_invoice { background: #065f46; color: #a7f3d0; }
  .action-label.error { background: #991b1b; color: #fecaca; }
  .event.error::before { background: #ef4444; border-color: #ef4444; }
  .event-detail.error-detail { border-left: 3px solid #ef4444; }

  .stats { display: flex; gap: 16px; margin-bottom: 20px; }
  .stat-card { background: #1e293b; border-radius: 10px; padding: 14px 20px; flex: 1; text-align: center; }
  .stat-val { font-size: 1.5rem; font-weight: 700; color: #f8fafc; }
  .stat-label { font-size: 0.75rem; color: #64748b; margin-top: 2px; }
  .stat-card.errors .stat-val { color: #ef4444; }
  .stat-card.errors { cursor: pointer; transition: background 0.15s; }
  .stat-card.errors:hover { background: #2a1215; }
  .stat-card.errors.active { border: 1px solid #ef4444; background: #2a1215; }

  .empty { text-align: center; color: #475569; padding: 40px; }
  .refresh-btn { background: #334155; border: none; color: #94a3b8; padding: 6px 14px;
                 border-radius: 6px; cursor: pointer; font-size: 0.8rem; }
  .refresh-btn:hover { background: #475569; color: #e2e8f0; }
  .header-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
  .auto-badge { font-size: 0.7rem; color: #22c55e; }
</style>
</head>
<body>

<h1>P2P Audit Dashboard</h1>
<p class="subtitle">Trace procurement workflows end-to-end via workflow_id</p>

<div class="stats">
  <div class="stat-card"><div class="stat-val" id="stat-workflows">-</div><div class="stat-label">Workflows</div></div>
  <div class="stat-card"><div class="stat-val" id="stat-events">-</div><div class="stat-label">Total Events</div></div>
  <div class="stat-card errors" id="stat-errors-card" onclick="showErrors()"><div class="stat-val" id="stat-errors">-</div><div class="stat-label">Errors (click to view)</div></div>
</div>

<div class="grid">
  <div class="panel" id="wf-panel">
    <div class="header-row">
      <h2>Workflows</h2>
      <div>
        <span class="auto-badge" id="auto-label"></span>
        <button class="refresh-btn" onclick="loadWorkflows()">Refresh</button>
      </div>
    </div>
    <div id="wf-list"><div class="empty">No workflows yet.<br>Make some API calls!</div></div>
  </div>

  <div class="panel" id="timeline-panel">
    <h2>Event Timeline</h2>
    <div id="timeline"><div class="empty">Select a workflow to see its events</div></div>
  </div>
</div>

<script>
let activeWf = null;
let viewMode = 'workflows'; // 'workflows' or 'errors'
let cachedErrors = [];

async function loadWorkflows() {
  const resp = await fetch('/api/workflows');
  const data = await resp.json();

  // Fetch all logs for error count
  const errResp = await fetch('/api/audit-logs?limit=500');
  const allLogs = await errResp.json();
  cachedErrors = allLogs.filter(e => e.action.startsWith('error:'));

  // Update stats
  document.getElementById('stat-workflows').textContent = data.length;
  document.getElementById('stat-events').textContent = allLogs.length;
  document.getElementById('stat-errors').textContent = cachedErrors.length;

  // Highlight errors card if in error view
  document.getElementById('stat-errors-card').classList.toggle('active', viewMode === 'errors');

  const el = document.getElementById('wf-list');
  if (!data.length) { el.innerHTML = '<div class="empty">No workflows yet.<br>Make some API calls!</div>'; return; }
  el.innerHTML = data.map(w => {
    const wfErrors = cachedErrors.filter(e => e.workflow_id === w.workflow_id).length;
    const errorBadge = wfErrors > 0 ? '<span class="wf-badge" style="background:#ef4444">' + wfErrors + ' errors</span>' : '';
    return `
    <div class="wf-card ${activeWf === w.workflow_id && viewMode === 'workflows' ? 'active' : ''}"
         onclick="selectWorkflow('${w.workflow_id}')">
      <div class="wf-id">${w.workflow_id}<span class="wf-badge">${w.events} events</span>${errorBadge}</div>
      <div class="wf-meta">${timeAgo(w.last_activity)}</div>
    </div>`;
  }).join('');
}

function showErrors() {
  viewMode = 'errors';
  activeWf = null;
  loadWorkflows();

  const el = document.getElementById('timeline');
  const h2 = document.querySelector('#timeline-panel h2');
  h2.textContent = 'All Errors';

  if (!cachedErrors.length) { el.innerHTML = '<div class="empty">No errors recorded</div>'; return; }

  el.innerHTML = '<div class="timeline">' + cachedErrors.map(e => {
    const errorCode = e.action.replace('error:', '');
    const wfLink = e.workflow_id
      ? '<span style="cursor:pointer;color:#93c5fd;text-decoration:underline" onclick="selectWorkflow(\\'' + e.workflow_id + '\\')">' + e.workflow_id.slice(0,8) + '...</span>'
      : '<span style="color:#475569">no workflow</span>';
    return `
    <div class="event error">
      <div class="event-action">
        <span class="action-label error">${errorCode}</span>
        ${wfLink}
      </div>
      <div class="event-time">${new Date(e.timestamp).toLocaleString()}</div>
      ${e.detail ? '<div class="event-detail error-detail">' + JSON.stringify(e.detail, null, 2) + '</div>' : ''}
    </div>`;
  }).join('') + '</div>';
}

async function selectWorkflow(wfId) {
  viewMode = 'workflows';
  activeWf = wfId;
  loadWorkflows();

  const h2 = document.querySelector('#timeline-panel h2');
  h2.textContent = 'Event Timeline';

  const resp = await fetch('/api/audit-logs?workflow_id=' + wfId);
  const data = await resp.json();
  data.reverse();
  const el = document.getElementById('timeline');
  if (!data.length) { el.innerHTML = '<div class="empty">No events</div>'; return; }
  el.innerHTML = '<div class="timeline">' + data.map(e => {
    const isError = e.action.startsWith('error:');
    const cssClass = isError ? 'error' : e.action;
    const label = isError ? e.action.replace('error:', '') : formatAction(e.action);
    const entityLabel = isError ? '' : e.entity_type + ' #' + e.entity_id;
    const detailClass = isError ? 'event-detail error-detail' : 'event-detail';
    return `
    <div class="event ${cssClass}">
      <div class="event-action">
        <span class="action-label ${cssClass}">${label}</span>
        ${entityLabel}
      </div>
      <div class="event-time">${new Date(e.timestamp).toLocaleString()}</div>
      ${e.detail ? '<div class="' + detailClass + '">' + JSON.stringify(e.detail, null, 2) + '</div>' : ''}
    </div>`;
  }).join('') + '</div>';
}

function formatAction(a) {
  return a.split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
}

function timeAgo(iso) {
  const s = Math.floor((Date.now() - new Date(iso)) / 1000);
  if (s < 60) return s + 's ago';
  if (s < 3600) return Math.floor(s/60) + 'm ago';
  if (s < 86400) return Math.floor(s/3600) + 'h ago';
  return Math.floor(s/86400) + 'd ago';
}

loadWorkflows();
setInterval(() => {
  loadWorkflows();
  if (viewMode === 'workflows' && activeWf) selectWorkflow(activeWf);
  if (viewMode === 'errors') showErrors();
}, 3000);
document.getElementById('auto-label').textContent = 'auto-refresh 3s';
</script>
</body>
</html>
"""
