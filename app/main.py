"""FastAPI application entrypoint.

Run with:
    uvicorn app.main:app --reload
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session

from .database import Base, SessionLocal, engine, get_db
from .errors import P2PError, p2p_error_handler
from .middleware import AuditLogMiddleware
from .models import Product
from .routers import dashboard, invoices, purchase_orders, vendors
from .seed import seed_database

# Configure audit logger — shows in uvicorn output
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logging.getLogger("p2p.audit").setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables on startup. No Alembic for the demo — `create_all`
    # is fine because the schema only changes when we restart.
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_database(db)
    finally:
        db.close()
    yield


app = FastAPI(
    title="P2P API",
    description=(
        "Purchase-to-Pay API designed for AI agent consumption. "
        "Endpoints are intentionally atomic so an agent can compose "
        "the procurement workflow itself."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(AuditLogMiddleware)
app.add_exception_handler(P2PError, p2p_error_handler)
app.include_router(purchase_orders.router)
app.include_router(invoices.router)
app.include_router(vendors.router)
app.include_router(dashboard.router)


@app.get("/health", tags=["meta"])
def health():
    """Liveness probe — returns OK if the app booted and the DB is reachable."""
    return {"status": "ok"}


@app.get("/products", tags=["meta"])
def products(db: Session = Depends(get_db)):
    """ get all the products"""
    products = db.query(Product).all()
    return products


