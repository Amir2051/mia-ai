from contextlib import asynccontextmanager
from logging import StreamHandler, getLogger
import os
import sys
import time
import uuid

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.shopify.config import settings

MAX_REQUEST_BYTES = 5 * 1024 * 1024


def _configure_logging() -> None:
    logger = getLogger("mia_ai")
    if not logger.handlers:
        handler = StreamHandler(sys.stdout)
        handler.setFormatter(__import__("logging").Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(__import__("logging").INFO)


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        request_id = getattr(request.state, "request_id", "n/a")
        getLogger("mia_ai").exception("request_id=%s path=%s", request_id, request.url.path, exc_info=exc)
        return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"detail": "Internal server error"})


@asynccontextmanager
async def lifespan(application: FastAPI):
    yield


def create_app() -> FastAPI:
    _configure_logging()
    application = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

    @application.middleware("http")
    async def request_security_and_logging(request: Request, call_next):
        request.state.request_id = str(uuid.uuid4())
        start = time.perf_counter()

        content_length = request.headers.get("content-length")
        try:
            if content_length and int(content_length) > MAX_REQUEST_BYTES:
                return JSONResponse(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, content={"detail": "Request body too large"})
        except ValueError:
            return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"detail": "Invalid Content-Length header"})

        try:
            response = await call_next(request)
        except Exception:
            getLogger("mia_ai").exception("request_id=%s path=%s", request.state.request_id, request.url.path)
            return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"detail": "Internal server error"})

        duration_ms = (time.perf_counter() - start) * 1000
        logger = getLogger("mia_ai")
        logger.info("request_id=%s method=%s path=%s status=%s duration_ms=%s", request.state.request_id, request.method, request.url.path, response.status_code, round(duration_ms, 2))

        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        # Shopify loads embedded apps inside an admin iframe. X-Frame-Options: DENY
        # would block that iframe, so use CSP frame-ancestors for the intended hosts.
        response.headers["Content-Security-Policy"] = "frame-ancestors 'self' https://admin.shopify.com https://*.myshopify.com"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if settings.is_production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.effective_cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Shopify-Hmac-SHA256", "X-Shopify-Topic", "X-Shopify-Shop-Domain", "X-Shopify-Event-Id"],
    )
    _register_exception_handlers(application)

    from app.routers import analytics, auth, customers, imports, marketing, orders, products, settings as settings_router, webhooks
    application.include_router(auth.router, prefix="/api/auth", tags=["auth"])
    application.include_router(analytics.router, prefix="/api/analytics", tags=["analytics"])
    application.include_router(customers.router, prefix="/api/customers", tags=["customers"])
    application.include_router(imports.router, prefix="/api/imports", tags=["imports"])
    application.include_router(marketing.router, prefix="/api/marketing", tags=["marketing"])
    application.include_router(orders.router, prefix="/api/orders", tags=["orders"])
    application.include_router(products.router, prefix="/api/products", tags=["products"])
    application.include_router(settings_router.router, prefix="/api/settings", tags=["settings"])
    application.include_router(webhooks.router, prefix="/api", tags=["webhooks"])

    @application.get("/health", tags=["health"])
    async def health():
        return {"status": "ok", "app": settings.app_name}

    frontend_dist = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "dist")
    if os.path.isdir(frontend_dist):
        application.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")

        @application.get("/{full_path:path}")
        async def spa_fallback(request: Request, full_path: str):
            index_path = os.path.join(frontend_dist, "index.html")
            if os.path.isfile(index_path):
                return FileResponse(index_path)
            return JSONResponse({"detail": "not_found"}, status_code=404)

    return application


app = create_app()
