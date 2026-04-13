# Project Context for Claude

## What this is
A **Purchase-to-Pay (P2P) REST API** for a 20-year-old ERP modernization. The API will be consumed by an **AI agent**, not a human UI. Design every endpoint as a tool call: atomic, predictable, machine-friendly.

## Consumer = agent (this shapes everything)
- **Atomic endpoints**, not high-level "verbs." The agent composes the workflow.
- **Rich OpenAPI descriptions** — these become the LLM tool docstrings.
- **Structured errors with `next_actions`** so the agent can recover without guessing.
- **Idempotent state transitions** — agents retry. `submit`, `match`, `approve` return 200 with current state if called twice.
- **Enums, not free strings**, for every status field.

## Tech stack (non-negotiable for this demo)
- Python 3.10+, FastAPI, SQLAlchemy 2.0 (Mapped/`mapped_column` style), SQLite
- Pydantic v2 for schemas (`model_config = {"from_attributes": True}`)
- **Money is `Decimal` end-to-end.** Column: `Numeric(12, 2)`. Never float.
- **Datetimes are timezone-aware UTC.** Use `datetime.now(timezone.utc)`, never `datetime.utcnow()` (deprecated).
- No Alembic — `Base.metadata.create_all` in the lifespan hook.
- No auth, no pagination, no async DB, no Docker.

## File layout
```
app/
├── __init__.py
├── database.py          # engine, Base, SessionLocal, get_db
├── enums.py             # PaymentTerms, POStatus, InvoiceStatus
├── models.py            # SQLAlchemy ORM models
├── schemas.py           # Pydantic request/response models
├── errors.py            # P2PError + structured error envelope + handler
├── seed.py              # Idempotent seed data
├── main.py              # FastAPI app + lifespan + exception handler registration
└── routers/
    ├── __init__.py
    ├── purchase_orders.py
    ├── invoices.py
    └── vendors.py
```
Routers are mounted in `main.py` with `app.include_router(...)`.

## Error envelope (use this shape everywhere)
Every error response — 4xx and 5xx — returns:
```json
{
  "error_code": "PARTIAL_RECEIPT_PENDING",
  "message": "Human-readable summary",
  "context": { "invoice_amount": "1200.00", "received_value": "800.00", "gap": "400.00" },
  "next_actions": ["wait_for_receipt", "create_partial_invoice"]
}
```
- `error_code` is SCREAMING_SNAKE_CASE and machine-stable.
- `context` contains the values the agent needs to decide what to do next.
- `next_actions` is a list of suggested recoveries. Empty list is OK.

Implementation: a `P2PError` exception class in `app/errors.py` plus a FastAPI exception handler registered in `main.py`. Raise `P2PError(code, message, context, next_actions)` from anywhere.

## Domain rules (hard invariants — never violate)
1. **Inactive vendor cannot have a new PO.** → `INACTIVE_VENDOR` (400).
2. **`qty_received ≤ qty_ordered`** on every PO line. → `OVER_RECEIPT` (422).
3. **3-way match:** `invoice.amount ≤ received_value`. → `AMOUNT_EXCEEDS_RECEIVED` (422).
4. **Partial receipt flag:** if not all ordered qty received, the match surfaces `PARTIAL_RECEIPT_PENDING` in `next_actions` but the match itself can still succeed if amounts tie out.
5. **Approve only from MATCHED.** → `INVOICE_NOT_MATCHED` (409).
6. **GL must balance:** sum of debits == sum of credits per invoice approval. Assert in code.
7. **Status transitions are one-way:** DRAFT→SUBMITTED→RECEIVED→CLOSED. No skipping, no rewinding.

## GL posting rules
When an invoice is approved:
- Debit: **AP Control** account — hardcoded `2000`.
- Credit: **Expense** account from `vendor.expense_account_code`.
- Amount: `invoice.amount`.
- Two `GLEntry` rows per approval. Debits total == credits total.

## Design defaults (when ambiguous, pick the simpler one)
- **3-way match granularity:** aggregate (sum of invoice ≤ sum of received value). Line-level only if the interviewer asks for it.
- **Tolerance band:** strict equality. No ±% fuzziness.
- **Multiple receipts per PO:** yes (use `GoodsReceipt` + `GoodsReceiptLineItem`).
- **Multiple invoices per PO:** yes. Track remaining matchable value.
- **PO mutation after SUBMITTED:** locked. No edits.
- **Cancellation paths:** skip for demo. Happy path + validation failures only.

## Coding conventions
- Import order: stdlib, third-party, local (`from .xxx import yyy`).
- Prefer **explicit returns over implicit None.**
- One router per resource; keep them thin — business logic in helpers if they get long, but inline is fine for the demo.
- Every endpoint has `tags=[...]`, a docstring, and `response_model=...`.
- Use `typing.Annotated[Session, Depends(get_db)]` for DB dependency (cleaner than bare `Depends`).
- Keep functions short; if an endpoint exceeds ~25 lines, factor a helper.

## Observability hook (do this cheaply)
- Every `PurchaseOrder` gets a `workflow_id` UUID at creation.
- Threaded through `Invoice` and `GLEntry` via the PO relationship.
- This enables cross-entity tracing later without re-plumbing anything.

## Things NOT to do
- ❌ Auth, users, roles
- ❌ Alembic migrations
- ❌ Docker, docker-compose
- ❌ Unit tests (smoke script `smoke.py` instead)
- ❌ Async SQLAlchemy
- ❌ Float for money, ever
- ❌ Free-string status fields
- ❌ Generic `HTTPException` — use `P2PError` so the envelope stays consistent
- ❌ Scope creep: if it's not in the README or this file, ask before building

## Seed data (already in `app/seed.py`)
- 3 vendors: ACME (NET30, active), Ironclad (NET60, active), Legacy Stone (inactive — kept on purpose for validation demos)
- 8 products (SKUs): lumber, drywall, concrete, plumbing
- Seed is idempotent — safe to re-run on every app start.

## How to decide something not covered here
1. Does it violate a hard invariant above? → No.
2. Is it the **simplest** thing that satisfies the README? → Prefer this.
3. Does it make the API **harder** for an agent to consume? → If yes, reconsider.
4. Will it take more than ~5 minutes? → Stop and surface the decision.
