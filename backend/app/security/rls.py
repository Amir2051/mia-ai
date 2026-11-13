import hashlib
import hmac

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.shopify.config import settings


SHOP_DOMAIN_SETTING = "app.shop_domain"
SHOP_ID_SETTING = "app.shop_id"
SHOP_CONTEXT_SIGNATURE_SETTING = "app.shop_context_signature"
SCHEDULER_FLAG_SETTING = "app.internal_scheduler"
SCHEDULER_SIGNATURE_SETTING = "app.internal_scheduler_signature"
SHOP_CONTEXT_MESSAGE = "mia:shop:v1"
SCHEDULER_CONTEXT_MESSAGE = "mia:internal-scheduler:v1"


def _context_signature(message: str) -> str:
    secret = settings.rls_context_secret
    if len(secret.encode("utf-8")) < 32:
        raise RuntimeError("RLS_CONTEXT_SECRET must contain at least 32 bytes for PostgreSQL tenant isolation")
    return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


async def _set_local(db: AsyncSession, setting: str, value: str) -> None:
    await db.execute(
        text("select set_config(:setting, :value, true)"),
        {"setting": setting, "value": value},
    )


async def set_shop_context(db: AsyncSession, shop_domain: str, shop_id: int | None = None) -> None:
    """Bind the authenticated merchant to this PostgreSQL transaction with an HMAC proof.

    The shop ID GUC is retained for diagnostics/backwards compatibility only.
    RLS policies must derive the effective shop from the signed domain context.
    """
    if not db.bind or db.bind.dialect.name != "postgresql":
        return

    domain = (shop_domain or "").strip().lower()
    if not domain:
        raise ValueError("shop_domain is required for database security context")

    # Bind the tenant proof to this PostgreSQL transaction so a captured
    # signature cannot be replayed in a later transaction.
    txid = await db.scalar(text("SELECT pg_catalog.txid_current()"))
    if txid is None:
        raise RuntimeError("Could not bind shop context to the current PostgreSQL transaction")
    signature = _context_signature(f"{SHOP_CONTEXT_MESSAGE}:{txid}:{domain}")
    await _set_local(db, SHOP_DOMAIN_SETTING, domain)
    await _set_local(db, SHOP_CONTEXT_SIGNATURE_SETTING, signature)
    await _set_local(db, SHOP_ID_SETTING, str(shop_id) if shop_id is not None else "")


async def set_internal_scheduler_context(db: AsyncSession) -> None:
    """Grant the trusted scheduler cross-shop read/claim context for this transaction.

    A plain app.internal_scheduler=true GUC is deliberately insufficient. The
    database validates this HMAC using key material inaccessible to the runtime role.
    """
    if not db.bind or db.bind.dialect.name != "postgresql":
        return

    # Bind the proof to this PostgreSQL transaction. A signature observed in
    # one scheduler transaction must not be replayable in a later transaction.
    txid = await db.scalar(text("SELECT pg_catalog.txid_current()"))
    if txid is None:
        raise RuntimeError("Could not bind scheduler context to the current PostgreSQL transaction")
    signature = _context_signature(f"{SCHEDULER_CONTEXT_MESSAGE}:{txid}")
    await _set_local(db, SCHEDULER_FLAG_SETTING, "true")
    await _set_local(db, SCHEDULER_SIGNATURE_SETTING, signature)
