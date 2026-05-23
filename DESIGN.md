# Backend Design Document — E-Commerce API

## Overview

A production-ready REST API for an e-commerce product catalog. Built with FastAPI, MySQL, Elasticsearch, and Redis — all containerised with Docker Compose. Covers full CRUD, advanced search, caching, rate limiting, observability, and stock management.

---

## Table of Contents

1. [System Architecture](#1-system-architecture)
2. [Technology Choices](#2-technology-choices)
3. [Database Design](#3-database-design)
4. [Elasticsearch Design](#4-elasticsearch-design)
5. [Dual-Write Pattern](#5-dual-write-pattern)
6. [Search Architecture](#6-search-architecture)
7. [Caching Strategy](#7-caching-strategy)
8. [Stock Management & Atomicity](#8-stock-management--atomicity)
9. [Rate Limiting](#9-rate-limiting)
10. [Observability](#10-observability)
11. [API Design Decisions](#11-api-design-decisions)
12. [Trade-offs & Known Limitations](#12-trade-offs--known-limitations)

---

## 1. System Architecture

```
                        ┌─────────────────────────────────────┐
                        │           Docker Compose             │
                        │                                      │
  Client ──────────────▶│  FastAPI App  :8000                  │
                        │       │                              │
                        │       ├──────────▶ MySQL 8.0         │
                        │       │           (source of truth)  │
                        │       │                              │
                        │       ├──────────▶ Elasticsearch 8.13│
                        │       │           (search + facets)  │
                        │       │                              │
                        │       └──────────▶ Redis 7.2         │
                        │                   (cache + limits)   │
                        └─────────────────────────────────────┘
```

**Request flow:**
- All requests enter through FastAPI
- `RequestLoggingMiddleware` fires first — stamps a `X-Request-ID`, starts a timer
- Rate limiter checks Redis before the route handler runs (on write endpoints)
- Route handler calls service layer (business logic)
- Service layer talks to MySQL and/or Elasticsearch depending on the operation
- Redis is checked for cache before hitting MySQL/ES on cacheable endpoints
- Response goes back with `X-Request-ID` and `X-Response-Time` headers attached

**Startup flow:**
- Docker health checks ensure MySQL and Elasticsearch are ready before the app container starts
- FastAPI `lifespan` event runs the ingestion pipeline on startup
- Ingestion is idempotent — checks if data exists before fetching from dummyjson.com

---

## 2. Technology Choices

### FastAPI over Flask/Django
- Native async support and type annotations via Pydantic — less boilerplate for request validation
- Auto-generates OpenAPI/Swagger docs from route definitions — no extra work
- `lifespan` context manager for clean startup/shutdown hooks
- `Depends()` injection system makes it easy to share DB sessions, ES clients, Redis clients across routes without globals

### MySQL over PostgreSQL
- Assignment specified MySQL
- Normalised relational schema is a natural fit for product catalog data
- SQLAlchemy 2.0 provides a clean ORM layer — easy to swap DB if needed

### Elasticsearch alongside MySQL
- Full-text search with relevance scoring is not MySQL's strength — `LIKE '%query%'` doesn't rank results, doesn't handle typos, and can't do faceted aggregations efficiently
- ES handles: fuzzy search, autocomplete, `more_like_this`, aggregations (stats, facets) — all things that would require complex, slow SQL
- MySQL stays as the write source of truth; ES is the read-optimised search index

### Redis for caching and rate limiting
- Single Redis instance serves two purposes: cache store for categories/stats, and rate limit counter store for slowapi
- This avoids running two separate in-memory services
- Redis `SETEX` (set with expiry) handles TTL-based eviction natively

### Sync SQLAlchemy over async
- Async SQLAlchemy with `asyncpg`/`aiomysql` adds complexity — connection pool management, async context managers everywhere
- For a single-node demo API, the throughput difference is not meaningful
- Kept sync to keep the code readable and debuggable

---

## 3. Database Design

### Schema

```
categories          products                product_tags        product_images
──────────          ────────                ────────────        ──────────────
id (PK)             id (PK)                 id (PK)             id (PK)
name                title                   product_id (FK)     product_id (FK)
                    description             tag                 url
                    price
                    discount_percentage
                    rating
                    stock
                    brand
                    sku
                    weight
                    thumbnail
                    category_id (FK)
```

### Why normalised instead of JSON columns?

The alternative would be storing tags as `tags JSON` and images as `images JSON` directly on the products table. That's simpler but has real costs:

- You can't do `WHERE tags CONTAINS 'apple'` without a JSON function scan — no index
- You can't enforce referential integrity on JSON values
- Querying is messier and DB-specific

Separate tables for tags and images allow clean joins, potential indexing, and make the schema easier to extend (e.g. add `tag_type`, `image_order`).

### Why keep product IDs from dummyjson?

The ingested products have IDs 1–194 from the source API. Preserving these IDs means the same ID references the same document in both MySQL and Elasticsearch — no mapping table needed, no sync complexity.

---

## 4. Elasticsearch Design

### Index mapping decisions

```json
{
  "title":       { "type": "text" },        ← full-text, scored
  "description": { "type": "text" },        ← full-text, scored
  "brand":       { "type": "keyword" },     ← exact match, aggregations
  "category":    { "type": "keyword" },     ← exact match, aggregations
  "tags":        { "type": "keyword" },     ← exact match, aggregations
  "price":       { "type": "float" },       ← range queries, sort
  "rating":      { "type": "float" },       ← range queries, sort
  "thumbnail":   { "type": "keyword", "index": false },  ← display only
  "images":      { "type": "keyword", "index": false }   ← display only
}
```

**`text` vs `keyword`:**
- `text` fields are analysed — tokenised, lowercased, stemmed. Good for "find documents containing this word"
- `keyword` fields are stored as-is. Good for exact filtering (`category = "smartphones"`), sorting, and aggregations (count by brand)
- `thumbnail` and `images` are `"index": false` because we never search on them — storing them in ES avoids a MySQL round-trip when building search results

### Query types and why each was chosen

**Full-text search — `multi_match` with `fuzziness: AUTO`**
```json
{
  "multi_match": {
    "query": "iphon",
    "fields": ["title^2", "description"],
    "fuzziness": "AUTO"
  }
}
```
- Searches both `title` and `description` in one query
- `title^2` boosts title matches — a product named "iPhone" should rank above one that merely mentions it in description
- `fuzziness: AUTO` tolerates typos: "iphon" finds "iPhone", "laptpo" finds "laptop". AUTO scales fuzz tolerance with word length

**Autocomplete suggestions — `match_phrase_prefix`**
```json
{ "match_phrase_prefix": { "title": "mac" } }
```
- Matches documents where `title` starts with the prefix "mac" as a phrase
- Fast and lightweight — no custom completion suggester needed
- Returns title, category, thumbnail — just enough for a dropdown

**Similar products — `more_like_this`**
```json
{
  "more_like_this": {
    "fields": ["title", "description"],
    "like": [{ "_index": "products", "_id": "1" }],
    "min_term_freq": 1,
    "max_query_terms": 25,
    "minimum_should_match": "20%"
  }
}
```
- Finds products with similar vocabulary in title and description
- `minimum_should_match: "20%"` is intentionally relaxed — product descriptions are short, strict matching would return nothing
- Only `title` and `description` (both `text`) are used — `keyword` fields like `tags` can't be used for MLT

**Aggregations for stats and facets**

Stats and facets are computed as ES aggregations that run alongside the search query — no extra round trip:
```json
{
  "aggs": {
    "by_category": { "terms": { "field": "category" } },
    "avg_price":   { "avg":   { "field": "price"    } }
  }
}
```
Running aggregations with `size: 0` (stats endpoint) means ES scores/ranks nothing and just computes aggregates — efficient.

---

## 5. Dual-Write Pattern

Every write operation (create, update, delete) touches both MySQL and Elasticsearch:

```
POST /products
      │
      ▼
  MySQL INSERT          ← if this fails, stop. Nothing written.
      │
      ▼
  ES index              ← if this fails, log error. Request still succeeds.
      │
      ▼
  Redis DELETE          ← invalidate stats cache
      │
      ▼
  201 response
```

**Why MySQL first?**
MySQL is the source of truth. If the MySQL write fails, we have nothing — don't touch ES. If ES fails after a successful MySQL write, the product exists in MySQL (safe) but won't appear in search results until reindexed. This is an acceptable temporary inconsistency.

**Why not roll back MySQL if ES fails?**
Rollback adds complexity and doesn't meaningfully improve consistency — ES failures are typically transient (network blip, ES restart). The product is safe in MySQL. The `POST /admin/reindex` endpoint exists to force-sync MySQL → ES after any failure.

**Why not ES first?**
ES has no transactions. If MySQL fails after an ES write, you'd have a product in the search index that doesn't exist in the database. Stale search results are worse than missing search results.

---

## 6. Search Architecture

The decision of which system handles which query type:

```
?query=          → Elasticsearch   full-text, fuzzy, relevance scoring
?category=       → MySQL           exact filter, indexed FK lookup
?sort=           → both            ORDER BY in MySQL / sort clause in ES
?min/max_price=  → both            WHERE clause in MySQL / range filter in ES
/stats           → Elasticsearch   aggregations (avg, terms) — efficient with aggs API
/suggestions     → Elasticsearch   match_phrase_prefix
/{id}/similar    → Elasticsearch   more_like_this
/search          → Elasticsearch   global cross-resource search
```

**Why not route everything through ES?**

For exact categorical filtering (`?category=smartphones`), MySQL is faster — it's a simple indexed FK lookup. ES adds network overhead and scoring complexity for something that doesn't need relevance ranking.

For writes, MySQL is the only option — ES doesn't support transactions.

---

## 7. Caching Strategy

### What is cached and why

| Endpoint | TTL | Reason |
|----------|-----|--------|
| `GET /categories` | 1 hour | 24 categories loaded on every page load. Never changes after ingestion. No invalidation needed. |
| `GET /products/stats` | 5 minutes | ES aggregation query on every page load. Slightly stale stats are acceptable. Short TTL to stay reasonably fresh. |

### Pattern: cache-aside

```
Request arrives
    │
    ▼
Check Redis ──── HIT ──▶ return cached response (+ "cached": true)
    │
   MISS
    │
    ▼
Query MySQL / ES
    │
    ▼
Write to Redis with TTL
    │
    ▼
Return response
```

### Cache invalidation on writes

The stats cache is explicitly deleted (`REDIS DEL products:stats`) on every successful POST, PUT, or DELETE. This is event-driven invalidation — more precise than relying purely on TTL.

The categories cache is never explicitly invalidated because categories cannot be created or deleted via the API. The 1-hour TTL is a safety net.

### Redis failure is non-blocking

If Redis is down, the cache read fails silently — the request falls through to MySQL/ES. If the cache write fails, it's logged and ignored. Redis is an optimisation, not a dependency. This is the same resilience philosophy as the ES dual-write.

---

## 8. Stock Management & Atomicity

### The problem: race conditions

Without careful handling, two concurrent buy requests can both observe the same stock count and both decrement it — resulting in negative stock ("overselling"):

```
Thread A: SELECT stock = 1   ← both see stock = 1
Thread B: SELECT stock = 1
Thread A: UPDATE stock = 0   ← Thread A succeeds
Thread B: UPDATE stock = 0   ← Thread B also succeeds (wrong — should have failed)
```

### The solution: atomic UPDATE with WHERE

```python
rows_updated = db.query(Product).filter(
    Product.id == product_id,
    Product.stock >= quantity        ← condition checked atomically
).update({"stock": Product.stock - quantity})
```

This translates to:
```sql
UPDATE products
SET stock = stock - :quantity
WHERE id = :id AND stock >= :quantity
```

The database executes this as a single atomic operation. If two requests race:
- One will update 1 row and succeed
- The other will update 0 rows (condition no longer true) and get a 409

No SELECT → check → UPDATE sequence. No window for a race condition. No application-level locking needed.

### Stock adjustment (PATCH /stock)

Same atomicity principle applies:
```sql
UPDATE products
SET stock = stock + :delta
WHERE id = :id AND (stock + :delta) >= 0
```

Negative delta (write-off) is safe — the WHERE clause prevents going below zero.

---

## 9. Rate Limiting

### Why rate limit only write endpoints?

Read endpoints (`GET /products`, `GET /categories`, etc.) are either cached (categories, stats) or fast indexed lookups. Applying rate limits to reads would hurt the demo UI. Write endpoints are the only ones that can cause state changes or expensive ES indexing operations.

### Implementation: slowapi + Redis

`slowapi` is a FastAPI-compatible wrapper around the `limits` library. It uses Redis as the counter store — each IP's request count is stored as a Redis key with a 60-second TTL. This means rate limit state survives app restarts (counters live in Redis, not in-process).

```
Request to POST /products
    │
    ▼
slowapi checks Redis: "ip:1.2.3.4:POST:/products" = 29
    │
   < 30 ──▶ increment counter, allow request
    │
   ≥ 30 ──▶ return 429 Too Many Requests
```

---

## 10. Observability

### Structured JSON logging

All log output is JSON:
```json
{
  "message": "http_request",
  "method": "GET",
  "path": "/products",
  "query": "query=apple",
  "status": 200,
  "duration_ms": 34,
  "request_id": "abc-123",
  "level": "INFO",
  "logger": "access",
  "timestamp": "2026-04-26 13:36:59"
}
```

Plaintext logs require regex parsing by log aggregation tools (Datadog, Loki, CloudWatch). JSON logs are directly queryable — filter by `status >= 500`, group by `path`, alert on `duration_ms > 1000`.

### X-Request-ID

Every request gets a UUID either generated fresh or forwarded from a client/gateway header. This ID is:
- Attached to the response header
- Included in every log line for that request

This means a single request can be traced end-to-end across any number of log lines, services, or load balancers.

### X-Response-Time

Server-side processing time attached to every response. Load balancers and API gateways use this for latency monitoring and health scoring without needing to parse logs.

### Prometheus metrics

`prometheus-fastapi-instrumentator` automatically exposes:
- `http_requests_total` — count by method, path, status code
- `http_request_duration_seconds` — latency histogram per endpoint

These can be scraped by Prometheus and visualised in Grafana. Latency histograms are particularly valuable — they show p50/p95/p99 latency, not just averages.

---

## 11. API Design Decisions

### Route ordering in FastAPI

FastAPI matches routes in registration order. Static routes must come before parameterised routes:

```python
@router.get("/stats")       ← registered first
@router.get("/top-rated")   ← registered first
@router.get("/{product_id}") ← registered last
```

If `/{product_id}` came first, `/stats` would be matched as `product_id = "stats"` and return a 404.

### 409 Conflict for stock errors, not 400

HTTP 400 means the request is malformed. A buy request with `{"quantity": 5}` is perfectly valid — the problem is the server's current state (insufficient stock). 409 Conflict is the correct status for "request is valid but conflicts with current state".

### Bulk create is all-or-nothing

When creating multiple products, a partial success (some created, some failed) leaves the caller in an ambiguous state — they don't know which ones succeeded and would need to query to find out. All-or-nothing (validate all first, commit all or nothing) is simpler and more predictable from the caller's perspective.

### `cached: true` in response body

Returning a `cached` field in the response body makes cache behaviour visible and testable. It's useful during development and in integration tests. In production you'd typically strip it or put it in a response header (`X-Cache: HIT`).

---

## 12. Trade-offs & Known Limitations

| Trade-off | Decision | Reasoning |
|-----------|----------|-----------|
| Sync SQLAlchemy | Kept sync | Async adds complexity without meaningful gain for a single-node demo |
| ES failure on write is non-blocking | Accepted | MySQL is source of truth; `POST /admin/reindex` recovers ES |
| No authentication | Skipped | Out of scope for a product catalog assignment |
| Single-node Elasticsearch | Accepted | Production would require replication; adequate for development |
| No cache stampede protection | Accepted | Low-traffic demo; production would add probabilistic early expiry or a distributed lock |
| No order history | Skipped | The `POST /{id}/buy` endpoint covers the core transactional concern without requiring a full orders schema |
| No real payment integration | Skipped | Mock payments add no engineering signal; real gateway integration is out of scope |
