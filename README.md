# E-Commerce API

A production-ready REST API for e-commerce products built with FastAPI, MySQL, Elasticsearch, and Redis.
Includes a demo UI, full CRUD, advanced search, bulk operations, rate limiting, Prometheus metrics, and structured logging.

---

## Development Process

This project was built following a **Spec-Driven Development (SDD)** approach. Before writing any code, a detailed specification document was authored for each feature covering:

- The objective and scope of the feature
- Data model and schema decisions with rationale
- API contract (endpoints, request/response shapes, status codes)
- Design decisions and trade-offs considered
- Acceptance criteria checklist

Each spec was reviewed and agreed on before implementation began. This ensured:
- No ambiguity about what was being built or why
- Design decisions were deliberate, not accidental
- Implementation could be verified against a clear acceptance checklist

The spec documents are included in the repository:

| Spec | Feature |
|------|---------|
| `specs/SPEC_CORE_API.md` | Docker Compose setup, ingestion pipeline, MySQL schema, ES mapping, base endpoints |
| `specs/SPEC_SORTING_PRICE_HEALTH.md` | Sort, price range filters, category filter, health endpoint |
| `specs/SPEC_SIMILAR_FACETS_STATS.md` | Similar products (MLT), search facets, stats aggregations |
| `specs/SPEC_CRUD_WRITE_DELETE.md` | Create, update, delete endpoints with dual-write pattern |
| `specs/SPEC_TOPRATED_ONSALE_GLOBALSEARCH.md` | Top-rated, on-sale, global cross-resource search |
| `specs/SPEC_REDIS_CACHING.md` | Cache-aside pattern, TTL strategy, invalidation logic |
| `specs/SPEC_IMPROVEMENTS.md` | Rate limiting, Prometheus, JSON logging, X-Request-ID, bulk create, admin reindex |
| `specs/SPEC_STOCK_BUY.md` | Atomic stock management and purchase endpoint |
| `specs/FRONTEND_SPEC.md` | Demo UI feature set |

---

## How to Run

### Prerequisites
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and running

### Start the app

```bash
docker compose up --build
```

Docker will:
1. Start MySQL, Elasticsearch, and Redis containers
2. Build and start the FastAPI app
3. Automatically fetch 194 products from dummyjson.com and ingest them into MySQL + Elasticsearch

First run takes ~2-3 minutes (Docker downloads images, data is ingested). Subsequent runs start in seconds.

### Access

| URL | Description |
|-----|-------------|
| http://localhost:8000 | Demo UI — visual frontend for all endpoints |
| http://localhost:8000/docs | Swagger UI — interactive API docs |
| http://localhost:8000/redoc | ReDoc API documentation |
| http://localhost:8000/health | Dependency health check (MySQL + ES + Redis) |
| http://localhost:8000/metrics | Prometheus metrics |

---

## API Endpoints

### Health

```
GET /health           # Pings MySQL + Elasticsearch + Redis, returns latency for each
```

### Categories

```
GET /categories       # List all categories (Redis-cached, 1hr TTL)
```

### Products — Read

```
GET /products                                      # List all products
GET /products?page=2&limit=10                      # Pagination
GET /products?category=smartphones                 # Filter by category (MySQL)
GET /products?query=apple                          # Full-text search (Elasticsearch)
GET /products?query=laptop&min_price=500           # Search + price filter
GET /products?sort=price_asc                       # Sort: price_asc, price_desc,
                                                   #       rating_asc, rating_desc,
                                                   #       discount_desc
GET /products?min_price=100&max_price=500          # Price range filter
GET /products/stats                                # Aggregations: avg price, top brands, etc. (Redis-cached, 5min TTL)
GET /products/top-rated                            # Products rated >= 4.5
GET /products/top-rated?min_rating=4.8             # Custom rating threshold
GET /products/on-sale                              # Products with >= 10% discount
GET /products/on-sale?min_discount=20              # Custom discount threshold
GET /products/suggestions?query=mac                # Autocomplete suggestions (Elasticsearch)
GET /products/{id}                                 # Single product by ID
GET /products/{id}/similar                         # Similar products (Elasticsearch MLT)
```

All read params are composable:
```
GET /products?category=laptops&min_price=500&sort=rating_desc&page=2
GET /products?query=apple&max_price=200&sort=discount_desc
```

### Products — Write

```
POST   /products          # Create a product (MySQL + Elasticsearch, rate limited: 30/min)
POST   /products/bulk     # Create up to 50 products in one call (single DB transaction, rate limited: 10/min)
PUT    /products/{id}     # Update a product (partial update, syncs MySQL + Elasticsearch, rate limited: 30/min)
DELETE /products/{id}     # Delete a product (removes from MySQL + Elasticsearch, rate limited: 30/min)
```

**Create / Update request body:**
```json
{
  "title": "iPhone 15 Pro",
  "description": "Latest Apple flagship.",
  "price": 999.99,
  "discount_percentage": 5.0,
  "stock": 50,
  "brand": "Apple",
  "sku": "APL-IP15P-001",
  "category": "smartphones",
  "tags": ["apple", "flagship"],
  "thumbnail": "https://..."
}
```
All fields except `title`, `price`, and `category` are optional. `PUT` accepts any subset of fields.

**Bulk create request body:**
```json
{
  "products": [
    { "title": "Product A", "price": 9.99, "category": "beauty" },
    { "title": "Product B", "price": 19.99, "category": "laptops" }
  ]
}
```
Max 50 per call. All-or-nothing: if any category is invalid, the entire request is rejected with per-item error details.

### Global Search

```
GET /search?query=phone        # Search across products AND categories in one call
GET /search?query=phone&limit=5
```

Response groups results by type:
```json
{
  "query": "phone",
  "products": [...],
  "categories": [{ "id": 1, "name": "smartphones" }],
  "totals": { "products": 12, "categories": 1 }
}
```

### Stock & Purchase

```
PATCH /products/{id}/stock   # Adjust stock by delta (positive = restock, negative = write-off)
POST  /products/{id}/buy     # Purchase N units — atomically decrements stock, 409 if insufficient
```

**Stock adjustment body:**
```json
{ "delta": 20, "reason": "restocked from supplier" }
{ "delta": -3, "reason": "damaged in warehouse" }
```

**Stock adjustment response:**
```json
{ "product_id": 1, "previous_stock": 99, "delta": 20, "current_stock": 119, "reason": "restocked from supplier" }
```

**Buy body:**
```json
{ "quantity": 2 }
```

**Buy response:**
```json
{ "product_id": 1, "product_title": "iPhone 15 Pro", "quantity_purchased": 2, "stock_remaining": 48 }
```

Both endpoints return `409 Conflict` when stock is insufficient. The buy endpoint uses a single atomic `UPDATE WHERE stock >= quantity` — no SELECT → check → UPDATE race condition possible.

### Admin

```
POST /admin/reindex    # Force-sync all products from MySQL → Elasticsearch (rate limited: 5/min)
```

Response:
```json
{ "indexed": 194, "failed": 0, "duration_ms": 152, "status": "ok" }
```

---

## Design Choices

### MySQL Schema
- **Normalized schema** — 4 tables: `categories`, `products`, `product_tags`, `product_images`
- Tags and images in separate tables (not JSON columns) — allows clean querying and relational integrity
- Product IDs preserved from dummyjson — stable references across MySQL and Elasticsearch

### Elasticsearch
- `title` and `description` as `text` — full-text search with relevance scoring
- `brand`, `category`, `tags` as `keyword` — exact-match filtering and aggregations
- `thumbnail` and `images` stored but not indexed (`"index": false`) — display fields only
- `multi_match` with `fuzziness: AUTO` — typo-tolerant full search
- `match_phrase_prefix` for suggestions — prefix-aware, fast, lightweight
- `more_like_this` for similar products — finds related products by title + description
- `aggs` for stats and facets — ES aggregations run alongside queries without extra round trips

### Dual-Write Pattern (Create / Update / Delete)
Every write operation updates both MySQL and Elasticsearch in sequence:
- **MySQL first** (source of truth) — if this fails, nothing is written
- **Elasticsearch second** — if ES fails, the error is logged but the request succeeds (data is safe in MySQL and can be re-indexed via `POST /admin/reindex`)

### Search Architecture
```
?query=    → Elasticsearch  (full-text, fuzzy, facets, MLT, suggestions)
?category= → MySQL          (exact filter, fast indexed lookup)
?sort=     → both           (ORDER BY in MySQL, sort clause in ES)
?min/max_price= → both      (WHERE in MySQL, range filter in ES)
```

### Redis Caching
Two endpoints are cached in Redis to reduce MySQL and Elasticsearch load:

| Endpoint | Cache Key | TTL | Invalidation |
|----------|-----------|-----|--------------|
| `GET /categories` | `categories:all` | 1 hour | None (categories are immutable via API) |
| `GET /products/stats` | `products:stats` | 5 minutes | Deleted on `POST`, `PUT`, `DELETE` |

**Pattern**: cache-aside — try Redis first, on miss hit the source, write result to Redis.  
**Resilience**: Redis failure is non-blocking; requests fall through to MySQL/ES transparently.  
Cached responses include `"cached": true` in the response body.

### Rate Limiting
Write endpoints are protected with per-IP rate limits backed by Redis:

| Endpoint | Limit |
|----------|-------|
| `POST /products` | 30/minute |
| `PUT /products/{id}` | 30/minute |
| `DELETE /products/{id}` | 30/minute |
| `POST /products/bulk` | 10/minute |
| `POST /admin/reindex` | 5/minute |

Exceeding the limit returns `HTTP 429 Too Many Requests`.

### Observability
- **Prometheus metrics** — `GET /metrics` exposes request counts, latency histograms, and error rates per endpoint via `prometheus-fastapi-instrumentator`
- **Structured JSON logging** — every log line is JSON: `{"message": "http_request", "method": "GET", "path": "/products", "status": 200, "duration_ms": 6, "request_id": "..."}`
- **`X-Request-ID` header** — UUID generated per request (or forwarded from client), attached to response headers and every log line for end-to-end tracing
- **`X-Response-Time` header** — server-side processing time on every response (e.g. `3ms`)

### Request Logging Middleware
Implemented as a FastAPI `BaseHTTPMiddleware`. Handles `X-Request-ID` generation, `X-Response-Time` attachment, and structured JSON access logging — zero impact on route handlers.

### Ingestion on Startup
- Runs automatically via FastAPI `lifespan` event
- Idempotent — skips if data already exists, safe to restart
- Waits for MySQL and Elasticsearch health with retry + backoff before starting

### Architecture
```
app/
├── routers/           ← HTTP layer (request parsing, response shaping)
│   ├── products.py    ← CRUD + bulk + stats + search endpoints
│   ├── categories.py  ← Categories with Redis cache
│   ├── search.py      ← Global search
│   └── admin.py       ← Reindex endpoint
├── services/          ← Business logic (MySQL queries, ES queries)
├── models/            ← SQLAlchemy ORM models
├── schemas/           ← Pydantic request/response schemas
├── ingest/            ← Data ingestion pipeline
├── database.py        ← MySQL engine + session
├── elastic.py         ← Elasticsearch client
├── cache.py           ← Redis client + get_redis() dependency
├── limiter.py         ← slowapi rate limiter (Redis-backed)
└── logging_config.py  ← JSON log formatter setup
```

---

## Demo UI

A frontend at `http://localhost:8000` visually demonstrates every API endpoint.

| UI Feature | API |
|------------|-----|
| Stats bar (total, avg price, avg rating) | `GET /products/stats` |
| Category filter bar | `GET /categories` + `GET /products?category=` |
| ⭐ Top Rated button | `GET /products/top-rated` |
| 🏷️ On Sale button | `GET /products/on-sale` |
| Sort buttons (Price ↑↓, Rating, Discount) | `GET /products?sort=` |
| Price range inputs | `GET /products?min_price=&max_price=` |
| Product grid with pagination | `GET /products` |
| Search bar | `GET /products?query=` via Elasticsearch |
| Suggestions dropdown | `GET /products/suggestions?query=` |
| "Search all" in dropdown | `GET /search?query=` — shows products + categories |
| Product detail modal | `GET /products/{id}` |
| Similar products in modal | `GET /products/{id}/similar` |
| Facet pills on search | Aggregations from ES response |
| API badge | Shows exact endpoint + status code in real time |

Built as a single `static/index.html` — no framework, no build step.

---

## Trade-offs & Known Limitations

- **No authentication** — out of scope for this assignment
- **Sync SQLAlchemy** — async would improve throughput under load, kept sync for simplicity
- **Brief ES/MySQL inconsistency on write** — if ES indexing fails after a MySQL write, the product exists in MySQL but not in ES. Use `POST /admin/reindex` to recover
- **Single-node Elasticsearch** — adequate for development; production requires replication
- **No cache stampede protection** — low-traffic demo; production would add probabilistic early expiry or a distributed lock

---

## Tech Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.11 |
| Framework | FastAPI |
| ORM | SQLAlchemy 2.0 |
| Database | MySQL 8.0 |
| Search | Elasticsearch 8.13 |
| Cache / Rate Limiting | Redis 7.2 |
| Metrics | Prometheus (`prometheus-fastapi-instrumentator`) |
| Logging | `python-json-logger` |
| Containerization | Docker + Docker Compose |
| Frontend | Vanilla HTML/CSS/JS (single file, no build step) |
