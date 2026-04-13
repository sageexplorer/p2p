# P2P Procurement API

A **Purchase-to-Pay REST API** built with FastAPI, designed for **AI agent consumption**. Every endpoint is atomic and machine-friendly so an agent can compose the full procurement workflow: create POs, receive goods, match invoices, approve payments, and post to the general ledger.

Built as a modernization layer for a legacy ERP system.

## Tech Stack

- **Python 3.10+** / **FastAPI** / **SQLAlchemy 2.0** / **SQLite**
- **Pydantic v2** for request/response validation
- **Decimal** for all money fields (never float)
- **LangGraph + Claude** for the AI agent
- **MCP (Model Context Protocol)** for Claude desktop/CLI integration

## Quick Start

### 1. Clone and install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

For the agent and MCP server, also install:

```bash
pip install langchain-anthropic langgraph httpx python-dotenv mcp
```

### 2. Set up environment variables

Create a `.env` file in the project root:

```
ANTHROPIC_API_KEY=sk-ant-...
```

### 3. Start the API

```bash
uvicorn app.main:app --reload
```

The app automatically creates tables and seeds demo data (3 vendors, 8 products) on startup.

- **API:** http://localhost:8000
- **Swagger UI:** http://localhost:8000/docs
- **OpenAPI JSON:** http://localhost:8000/openapi.json

### 4. Verify it works

```bash
curl http://localhost:8000/health
# {"status": "ok"}
```

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check |
| `GET` | `/vendors` | List all vendors |
| `GET` | `/vendors/{id}` | Get vendor detail |
| `GET` | `/vendors/{id}/exposure` | AP exposure & credit remaining |
| `POST` | `/purchase-orders` | Create a draft PO |
| `GET` | `/purchase-orders/{id}` | Get PO with line items |
| `POST` | `/purchase-orders/{id}/submit` | Submit PO (DRAFT -> SUBMITTED) |
| `POST` | `/purchase-orders/{id}/receive` | Record goods receipt |
| `POST` | `/invoices` | Create an invoice against a PO |
| `GET` | `/invoices/{id}` | Get invoice detail |
| `POST` | `/invoices/{id}/match` | Run 3-way match |
| `POST` | `/invoices/{id}/approve` | Approve invoice & post GL entries |

All state transitions are **idempotent** -- calling submit, match, or approve twice returns `200` with the current state.

---

## P2P Workflow

```
Create PO (DRAFT)
    |
    v
Submit PO (SUBMITTED)
    |
    v
Receive Goods (RECEIVED)     <-- supports partial receipts
    |
    v
Create Invoice (PENDING)
    |
    v
3-Way Match (MATCHED)        <-- invoice amount <= received value
    |
    v
Approve (APPROVED)           <-- posts GL entries (debit AP, credit expense)
```

---

## AI Agents

The API is purpose-built for agent consumption. Three integration paths are provided:

### Option A: Hand-Written Tools Agent (LangGraph)

Uses 9 manually defined tools with rich docstrings. The agent runs a ReAct loop, deciding which endpoint to call based on the current state.

```bash
# Requires the API to be running on localhost:8000
python -m agent.graph
```

This starts an interactive CLI where you can give natural-language procurement instructions like:

> "Create a PO for 100 pine studs from ACME, receive them, invoice for the full amount, and approve it."

### Option B: OpenAPI Auto-Generated Agent

Instead of hand-written tools, this agent fetches `/openapi.json` at startup and builds tools automatically from the spec. The OpenAPI descriptions become the LLM's tool docstrings.

```bash
python -m agent.openapi_agent
```

### Option C: MCP Server (Claude Desktop / Claude Code)

Exposes every API endpoint as an MCP tool. Claude can call the P2P API directly through the Model Context Protocol.

```bash
python -m mcp_server.server
```

Or configure it in `.mcp.json` for automatic use with Claude Code:

```json
{
  "mcpServers": {
    "p2p-api": {
      "command": ".venv/bin/python",
      "args": ["-m", "mcp_server.server"]
    }
  }
}
```

> All agents require `ANTHROPIC_API_KEY` in your `.env` file and the API running on `localhost:8000`.

---

## Running Tests

Tests use **pytest** with an isolated test database (no interference with dev data).

```bash
# Run all tests
pytest tests/ -v

# Run specific test files
pytest tests/test_purchase_orders.py -v
pytest tests/test_invoices.py -v
pytest tests/test_e2e_01.py -v          # Comprehensive end-to-end suite
```

### What the tests cover

| File | Coverage |
|------|----------|
| `test_health.py` | Health check endpoint |
| `test_purchase_orders.py` | PO creation, submission, receiving, validation errors |
| `test_invoices.py` | Invoice creation, 3-way matching, GL posting |
| `test_e2e.py` | Quick smoke test of the full workflow |
| `test_e2e_01.py` | Comprehensive E2E with edge cases (341 assertions) |

### Manual testing

See [`TESTING.md`](TESTING.md) for a full curl-based testing playbook covering every endpoint and error case.

---

## Structured Error Handling

Every error returns a machine-readable envelope:

```json
{
  "error_code": "OVER_RECEIPT",
  "message": "Receiving 200 units for PO line 3 would exceed ordered qty.",
  "context": {
    "po_line_item_id": 3,
    "qty_ordered": 200,
    "qty_already_received": 100,
    "max_receivable": 100
  },
  "next_actions": ["reduce_qty", "check_po_line"]
}
```

- `error_code` -- stable, machine-readable identifier (SCREAMING_SNAKE_CASE)
- `context` -- values the agent needs to decide what to do next
- `next_actions` -- suggested recovery steps

---

## Project Structure

```
app/
├── main.py              # FastAPI app, lifespan, middleware
├── database.py          # Engine, Base, SessionLocal, get_db
├── models.py            # SQLAlchemy ORM models
├── schemas.py           # Pydantic request/response schemas
├── enums.py             # POStatus, InvoiceStatus, PaymentTerms
├── errors.py            # P2PError exception + handler
├── seed.py              # Idempotent demo data
├── audit.py             # Audit logging
├── middleware.py         # AuditLogMiddleware
└── routers/
    ├── purchase_orders.py
    ├── invoices.py
    ├── vendors.py
    └── dashboard.py
agent/
├── graph.py             # LangGraph ReAct agent (hand-written tools)
├── tools.py             # 9 API tool wrappers
├── openapi_agent.py     # Agent with auto-generated tools
└── openapi_tools.py     # OpenAPI -> LangChain tool builder
mcp_server/
└── server.py            # FastMCP server for Claude integration
tests/
├── conftest.py          # Test fixtures (isolated DB)
├── test_health.py
├── test_purchase_orders.py
├── test_invoices.py
├── test_e2e.py
└── test_e2e_01.py
```

---

## Domain Rules

| Rule | Error Code | HTTP Status |
|------|-----------|-------------|
| Inactive vendor cannot create PO | `INACTIVE_VENDOR` | 400 |
| Cannot receive more than ordered | `OVER_RECEIPT` | 422 |
| Invoice amount > received value | `AMOUNT_EXCEEDS_RECEIVED` | 422 |
| Approve only from MATCHED status | `INVOICE_NOT_MATCHED` | 409 |
| Status never goes backwards | `INVALID_STATUS_TRANSITION` | 409 |
| GL debits must equal credits | Asserted in code | -- |
