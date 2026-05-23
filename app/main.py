import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from prometheus_fastapi_instrumentator import Instrumentator
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware

from app.cache import redis_client
from app.database import engine
from app.elastic import es_client
from app.ingest.ingest import run_ingestion
from app.limiter import limiter
from app.logging_config import setup_logging
from app.routers import categories, products, search
from app.routers import admin

setup_logging()

access_logger = logging.getLogger("access")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        start = time.monotonic()
        response = await call_next(request)
        duration_ms = round((time.monotonic() - start) * 1000)

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time"] = f"{duration_ms}ms"

        access_logger.info(
            "http_request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "query": request.url.query or None,
                "status": response.status_code,
                "duration_ms": duration_ms,
                "request_id": request_id,
            },
        )
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    run_ingestion()
    yield


app = FastAPI(
    title="E-Commerce API",
    description="REST API for e-commerce products powered by MySQL and Elasticsearch",
    version="1.0.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(RequestLoggingMiddleware)

Instrumentator().instrument(app).expose(app)

app.include_router(products.router)
app.include_router(categories.router)
app.include_router(search.router)
app.include_router(admin.router)


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return JSONResponse(status_code=404, content={"code": 404, "message": "Not found"})


@app.get("/health", tags=["Health"])
def health():
    result = {"status": "ok", "dependencies": {}}

    # MySQL check
    try:
        from sqlalchemy import text
        start = time.monotonic()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        result["dependencies"]["mysql"] = {
            "status": "ok",
            "latency_ms": round((time.monotonic() - start) * 1000),
        }
    except Exception as e:
        result["status"] = "degraded"
        result["dependencies"]["mysql"] = {"status": "error", "latency_ms": None, "error": str(e)}

    # Elasticsearch check
    try:
        start = time.monotonic()
        ok = es_client.ping()
        latency = round((time.monotonic() - start) * 1000)
        if ok:
            result["dependencies"]["elasticsearch"] = {"status": "ok", "latency_ms": latency}
        else:
            raise ConnectionError("ping returned False")
    except Exception as e:
        result["status"] = "degraded"
        result["dependencies"]["elasticsearch"] = {"status": "error", "latency_ms": None, "error": str(e)}

    # Redis check
    try:
        start = time.monotonic()
        redis_client.ping()
        result["dependencies"]["redis"] = {
            "status": "ok",
            "latency_ms": round((time.monotonic() - start) * 1000),
        }
    except Exception as e:
        result["status"] = "degraded"
        result["dependencies"]["redis"] = {"status": "error", "latency_ms": None, "error": str(e)}

    status_code = 200 if result["status"] == "ok" else 503
    return JSONResponse(content=result, status_code=status_code)


app.mount("/", StaticFiles(directory="static", html=True), name="static")
