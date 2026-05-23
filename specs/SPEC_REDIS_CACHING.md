# Spec: Redis Caching

## Objective

Introduce a Redis-backed cache-aside layer for two expensive or frequently-read endpoints: `GET /categories` and `GET /products/stats`. Categories are immutable via the API and can be cached indefinitely; stats are aggregation-heavy and can tolerate a short staleness window, but must be explicitly invalidated after any write operation.

---

## 1. Cache-Aside Pattern

The cache-aside pattern (also called lazy loading) works as follows:

```
Request arrives
    → Check cache (Redis)
        → HIT:  deserialize and return cached value (add "cached": true)
        → MISS: query MySQL/ES, serialize result, write to cache, return result
```

The application code is responsible for both reading from and writing to the cache. The cache is never written by a background job — it is populated on the first cache miss.

**Design decision:** Cache-aside is preferred over write-through here because:
- `GET /categories` is read-heavy and the data changes only via direct DB seeding (never via the API).
- `GET /products/stats` is aggregation-heavy and eventually consistent by nature. A brief staleness window is acceptable.

### Redis Failure is Non-Blocking

All Redis operations are wrapped in `try/except`. If Redis is unavailable (connection refused, timeout, etc.), the handler falls through to the MySQL/ES query as if the cache were a miss. The Redis error is logged at WARN level.

**Rationale:** Caching is a performance optimisation, not a correctness requirement. Making Redis a hard dependency of read endpoints would mean a Redis outage brings down product browsing — which is unacceptable. The service degrades gracefully (slower but correct) when Redis is down.

---

## 2. `GET /categories` Caching

### Cache Key

```
categories:all
```

### TTL

**3600 seconds (1 hour).**

In practice, the TTL never matters because categories are never modified via the API — once set, the cache entry is valid until Redis is restarted or the key is manually evicted. The TTL is set defensively to prevent a theoretical stale-forever scenario.

### Invalidation Policy

**Never invalidated programmatically.** There are no write endpoints for categories; they are populated only during the startup ingestion pipeline. If a future spec adds category management endpoints, this policy must be revisited.

### Implementation Sketch

```python
async def get_categories(redis, db):
    cached = await redis.get("categories:all")
    if cached:
        data = json.loads(cached)
        return {**data, "cached": True}

    categories = await db.fetch_all(select(Category))
    result = {
        "categories": [c.dict() for c in categories],
        "total": len(categories),
        "cached": False
    }
    await redis.set("categories:all", json.dumps(result), ex=3600)
    return result
```

### Response — Cache Miss

```json
{
  "categories": [ { "id": 1, "slug": "smartphones", "name": "Smartphones" } ],
  "total": 26,
  "cached": false
}
```

### Response — Cache Hit

```json
{
  "categories": [ { "id": 1, "slug": "smartphones", "name": "Smartphones" } ],
  "total": 26,
  "cached": true
}
```

---

## 3. `GET /products/stats` Caching

### Cache Key

```
products:stats
```

### TTL

**300 seconds (5 minutes).**

Stats reflect catalogue-wide aggregations. After a product is created, updated, or deleted, the stats can be up to 5 minutes stale before the cache refreshes naturally via TTL expiry. However, explicit invalidation on writes ensures the stats are fresh immediately after any mutation.

### Invalidation Policy

The `products:stats` cache key is **explicitly deleted** on every write that changes the catalogue:

| Write Operation          | Invalidation Action                    |
|--------------------------|----------------------------------------|
| `POST /products`         | `await redis.delete("products:stats")` |
| `PUT /products/{id}`     | `await redis.delete("products:stats")` |
| `DELETE /products/{id}`  | `await redis.delete("products:stats")` |
| `POST /products/bulk`    | `await redis.delete("products:stats")` |

Invalidation happens after the MySQL write succeeds, regardless of whether the ES write succeeded.

**Design decision:** Explicit invalidation (delete on write) rather than passive TTL expiry for stats. The TTL exists as a safety net, but stats should be accurate immediately after a write — a newly created product should be reflected in the total count without waiting up to 5 minutes. Deleting the key forces a fresh ES aggregation on the next request.

### Implementation Sketch

```python
async def get_stats(redis, es):
    cached = await redis.get("products:stats")
    if cached:
        data = json.loads(cached)
        return {**data, "cached": True}

    stats = await compute_stats_from_es(es)
    result = {**stats, "cached": False}
    await redis.set("products:stats", json.dumps(result), ex=300)
    return result
```

```python
# In POST /products, PUT /products/{id}, DELETE /products/{id}:
await redis.delete("products:stats")
```

### Response — Cache Hit

```json
{
  "total_products": 194,
  "avg_price": 423.17,
  "avg_rating": 4.22,
  "avg_discount": 12.68,
  "price_ranges": { "...": "..." },
  "top_categories": [ "..." ],
  "top_brands": [ "..." ],
  "cached": true
}
```

---

## 4. Docker Compose Redis Service

The Redis service is included in `docker-compose.yml` from the initial setup (SPEC_CORE_API.md). For completeness, the relevant snippet:

```yaml
redis:
  image: redis:7.2
  ports:
    - "6379:6379"
  networks:
    - ecommerce_net
  healthcheck:
    test: ["CMD", "redis-cli", "ping"]
    interval: 10s
    timeout: 5s
    retries: 5
```

The `app` service uses `condition: service_healthy` on `redis` in its `depends_on` block.

### Redis Client Configuration

```python
import redis.asyncio as aioredis

redis_client = aioredis.from_url(
    settings.REDIS_URL,
    encoding="utf-8",
    decode_responses=True,
    socket_connect_timeout=2,
    socket_timeout=2,
)
```

`socket_connect_timeout` and `socket_timeout` are set to 2 seconds so that a slow or unresponsive Redis does not block a request for longer than necessary before falling through to the direct DB/ES query.

---

## Acceptance Criteria

- [ ] `GET /categories` returns `"cached": false` on the first request after startup.
- [ ] `GET /categories` returns `"cached": true` on subsequent requests (within 3600s).
- [ ] `GET /products/stats` returns `"cached": false` on the first request after a cache miss or invalidation.
- [ ] `GET /products/stats` returns `"cached": true` on a second consecutive request (within 300s).
- [ ] After `POST /products`, the next `GET /products/stats` returns `"cached": false` (cache was invalidated).
- [ ] After `PUT /products/{id}`, the next `GET /products/stats` returns `"cached": false`.
- [ ] After `DELETE /products/{id}`, the next `GET /products/stats` returns `"cached": false`.
- [ ] When Redis is stopped, `GET /categories` still returns correct data (falls through to MySQL).
- [ ] When Redis is stopped, no 500 errors are returned — the response is correct but `"cached": false`.
- [ ] Redis health check passes in `GET /health` when Redis is running.
- [ ] Redis latency is reported in `GET /health` response.
