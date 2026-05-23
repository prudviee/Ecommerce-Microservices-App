# Spec: Production-Grade Improvements

## Objective

Harden the API for production use with seven cross-cutting improvements: an admin reindex endpoint, rate limiting on write operations, per-request tracing via `X-Request-ID`, Prometheus metrics exposure, a bulk product creation endpoint, structured JSON logging, and an `X-Response-Time` header on every response.

---

## 1. `POST /admin/reindex` — Force MySQL → ES Sync

### Purpose

Re-syncs the entire product catalogue from MySQL into Elasticsearch. Used when ES falls behind due to failed dual-writes, index corruption, or ES downtime. Useful during development to reset the ES state without restarting the whole stack.

### Rate Limiting

Limited to **5 requests per minute** per IP. Reindexing is a bulk operation that puts significant load on both MySQL and ES; unbounded calls could destabilise both stores.

### Implementation

1. Fetch all products from MySQL (with tags and images via joins).
2. Build ES documents for each product.
3. Call `es.bulk()` with `index` actions for all products.
4. Return a summary.

### Response (200)

```json
{
  "indexed": 194,
  "failed": 0,
  "duration_ms": 1423
}
```

If any ES bulk actions fail, `failed` reflects the count and the response still returns 200 (partial success). Individual failures are logged.

### Security Note

The `/admin/` prefix signals that this endpoint should be protected by an auth layer in a real deployment (e.g. API key middleware). For this project, no auth is implemented, but the prefix is reserved for admin-only operations.

---

## 2. Rate Limiting via slowapi + Redis

### Library

[`slowapi`](https://github.com/laurentS/slowapi) — a rate limiting library for FastAPI/Starlette built on `limits`. Uses Redis as the backend counter store.

### Rate Limits

| Endpoint                 | Limit      | Rationale                                              |
|--------------------------|------------|--------------------------------------------------------|
| `POST /products`         | 30/minute  | Prevents automated bulk creation spam                  |
| `PUT /products/{id}`     | 30/minute  | Prevents rapid-fire update hammering                   |
| `DELETE /products/{id}`  | 30/minute  | Prevents automated mass deletion                       |
| `POST /products/bulk`    | 10/minute  | Bulk creates up to 50 products each; tighter limit     |
| `POST /admin/reindex`    | 5/minute   | Heavy operation; very tight limit                      |

Rate limits are per IP address (`get_remote_address` key function from slowapi).

### 429 Response

When a limit is exceeded, slowapi returns HTTP **429 Too Many Requests** with a `Retry-After` header indicating when the window resets.

```json
{
  "error": "Rate limit exceeded: 30 per 1 minute"
}
```

### Configuration

```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address, storage_uri=settings.REDIS_URL)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
```

**Design decision:** Redis is reused as the rate limit counter backend (same Redis instance as caching). This avoids adding another infrastructure dependency. The rate limit counters use a separate key namespace from the cache keys.

---

## 3. `X-Request-ID` Header

### Purpose

Assigns a unique identifier to every request. The ID is either taken from an incoming `X-Request-ID` header (if the client or upstream proxy provides one) or generated as a new UUID. The ID is:

- Attached to the response as an `X-Request-ID` header.
- Injected into every log line for the duration of the request (via a context variable).

This makes it possible to correlate all log entries for a single request, and for clients to reference a specific request in a support context.

### Implementation — Middleware

```python
import uuid
from starlette.middleware.base import BaseHTTPMiddleware

class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
```

### Log Correlation

Each log record includes `request_id` as a structured field. Using a `contextvars.ContextVar` allows the request ID to be available anywhere in the call stack without threading concerns:

```python
request_id_var: ContextVar[str] = ContextVar("request_id", default="unknown")
```

The middleware sets `request_id_var.set(request_id)` at the start of each request.

---

## 4. Prometheus Metrics via `prometheus-fastapi-instrumentator`

### Library

[`prometheus-fastapi-instrumentator`](https://github.com/trallnag/prometheus-fastapi-instrumentator) — auto-instruments FastAPI with standard HTTP metrics.

### Metrics Exposed

The library exposes these metrics automatically at `GET /metrics`:

| Metric                                    | Type      | Description                         |
|-------------------------------------------|-----------|-------------------------------------|
| `http_requests_total`                     | Counter   | Total requests by method/path/status |
| `http_request_duration_seconds`           | Histogram | Request latency distribution        |
| `http_requests_in_progress`              | Gauge     | Currently in-flight requests        |

### Setup

```python
from prometheus_fastapi_instrumentator import Instrumentator

Instrumentator().instrument(app).expose(app)
```

This single call instruments all routes and registers `GET /metrics` in Prometheus text format.

**Design decision:** `/metrics` is unauthenticated in this project. In production, this endpoint should be protected (e.g. restricted to the internal network or behind Prometheus scrape authentication) to avoid exposing request patterns and error rates publicly.

---

## 5. `POST /products/bulk`

### Purpose

Creates up to 50 products in a single request. Useful for seeding test data or migrating products in batches. All-or-nothing: if any product fails validation, the entire batch is rejected with no DB writes.

### Request Body

```json
{
  "products": [
    { "title": "Product A", "price": 29.99, "category": "laptops" },
    { "title": "Product B", "price": 59.99, "category": "smartphones" }
  ]
}
```

### Validation

1. Array length must be 1–50. Return HTTP 422 if 0 or > 50.
2. Each product is validated against the same `ProductCreate` schema used by `POST /products`.
3. All category slugs must exist. If any slug is missing, return HTTP 422 with the list of invalid slugs before any DB write.

**Validation is all-or-nothing:** The entire batch is validated before any MySQL insert is executed. This prevents partial writes where some products were created and others were not.

### Implementation

```python
async with db.begin():  # single transaction
    for product_data in products:
        await insert_product(db, product_data)

await es_bulk_index(products)         # best-effort
await redis.delete("products:stats")  # invalidate stats cache
```

### Response (201)

```json
{
  "created": 2,
  "products": [
    { "id": 195, "title": "Product A", "...": "..." },
    { "id": 196, "title": "Product B", "...": "..." }
  ]
}
```

### Rate Limit

10 requests per minute (see Section 2).

---

## 6. Structured JSON Logging

### Library

[`python-json-logger`](https://github.com/madzak/python-json-logger) — formats log output as JSON lines instead of plain text.

### Why Structured Logging

Plain text logs are human-readable but machine-unfriendly. Log aggregation systems (Datadog, ELK, CloudWatch Logs Insights) can parse and query structured fields directly. Searching for all errors related to a specific `request_id` requires a text regex on unstructured logs but a simple field query on structured logs.

### Configuration

```python
import logging
from pythonjsonlogger import jsonlogger

logger = logging.getLogger()
handler = logging.StreamHandler()
formatter = jsonlogger.JsonFormatter(
    fmt="%(asctime)s %(name)s %(levelname)s %(message)s"
)
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)
```

`basicConfig` is replaced entirely by this setup.

### Log Record Shape

```json
{
  "asctime": "2026-04-26T10:00:00.000Z",
  "name": "app.products",
  "levelname": "INFO",
  "message": "Product created",
  "request_id": "b3e2f8a1-...",
  "product_id": 195,
  "duration_ms": 14.2
}
```

All log lines include `request_id` via the context variable set by `RequestIDMiddleware`.

---

## 7. `X-Response-Time` Header

### Purpose

Reports server-side processing time in milliseconds on every response. Useful for performance monitoring, identifying slow endpoints, and client-side SLA tracking without needing to set up Prometheus.

### Implementation — Middleware

```python
import time
from starlette.middleware.base import BaseHTTPMiddleware

class ResponseTimeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000
        response.headers["X-Response-Time"] = f"{elapsed_ms:.2f}ms"
        return response
```

**`time.perf_counter()`** is used rather than `time.time()` for sub-millisecond accuracy (perf_counter is monotonic and high-resolution; time.time() has platform-dependent resolution and can jump backwards due to NTP).

---

## Acceptance Criteria

- [ ] `POST /admin/reindex` re-indexes all products from MySQL into ES and returns `indexed`, `failed`, `duration_ms`.
- [ ] `POST /admin/reindex` called 6 times in under 60 seconds returns HTTP 429 on the 6th call.
- [ ] `POST /products` called 31 times in under 60 seconds returns HTTP 429 on the 31st call.
- [ ] Every response includes an `X-Request-ID` header.
- [ ] When the request includes `X-Request-ID: my-trace-id`, the same value appears in the response header.
- [ ] When no `X-Request-ID` is sent, the response contains a UUID-format `X-Request-ID`.
- [ ] Log lines for a single request all share the same `request_id` value.
- [ ] `GET /metrics` returns Prometheus text format with `http_requests_total` and `http_request_duration_seconds`.
- [ ] `POST /products/bulk` with 2 valid products returns 201 with both products created.
- [ ] `POST /products/bulk` with 51 products returns HTTP 422 (exceeds limit).
- [ ] `POST /products/bulk` where one product has an invalid category slug returns HTTP 422 and creates no products.
- [ ] `POST /products/bulk` is a single DB transaction (all-or-nothing on DB error).
- [ ] Log output is valid JSON (one object per line, not plain text).
- [ ] Every response includes an `X-Response-Time` header with a value in milliseconds (e.g. `"14.32ms"`).
