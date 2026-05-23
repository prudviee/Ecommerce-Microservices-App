# Spec: Sorting, Price Filtering, and Health Endpoint

## Objective

Extend `GET /products` with server-side sorting and price/category filtering so clients can retrieve products in meaningful order without doing it themselves. Add a `GET /health` endpoint that actively checks connectivity and latency to all three backing stores (MySQL, Elasticsearch, Redis) and returns a structured status report.

---

## 1. Extending `GET /products`

### New Query Parameters

| Parameter  | Type   | Default | Description                                                       |
|------------|--------|---------|-------------------------------------------------------------------|
| `sort`     | string | none    | One of: `price_asc`, `price_desc`, `rating_asc`, `rating_desc`, `discount_desc` |
| `min_price`| float  | none    | Inclusive lower bound on `price`                                  |
| `max_price`| float  | none    | Inclusive upper bound on `price`                                  |
| `category` | string | none    | Filter by category slug (exact match)                             |

Existing parameters `page` and `limit` continue to work as before.

### Sort Mapping

| `sort` value    | SQL equivalent                        |
|-----------------|---------------------------------------|
| `price_asc`     | `ORDER BY price ASC`                  |
| `price_desc`    | `ORDER BY price DESC`                 |
| `rating_asc`    | `ORDER BY rating ASC`                 |
| `rating_desc`   | `ORDER BY rating DESC`                |
| `discount_desc` | `ORDER BY discount_percentage DESC`   |

**Design decision:** Sorting is implemented in MySQL via `ORDER BY`. Elasticsearch is not involved in this endpoint — it is reserved for text search (later specs). Keeping sort logic in MySQL avoids synchronisation complexity and leverages indexed columns.

### Price Range Filtering

Applied as `WHERE price >= min_price AND price <= max_price`. Each bound is independently optional — passing only `min_price` without `max_price` is valid.

### Category Filtering

Resolves the slug to a `category_id` via a JOIN on the `categories` table, then filters `WHERE products.category_id = ?`.

### Validation

**Invalid `sort` value → 422**

FastAPI's `Query` parameter with `Literal` type annotation raises a `422 Unprocessable Entity` automatically if the value is not one of the five allowed strings. The response body follows FastAPI's default validation error format.

```json
{
  "detail": [
    {
      "loc": ["query", "sort"],
      "msg": "value is not a valid enumeration member",
      "type": "type_error.enum"
    }
  ]
}
```

**`min_price` > `max_price` → 422**

This cross-field validation is not expressible with Pydantic field constraints alone. It is handled with a manual check in the route handler:

```python
if min_price is not None and max_price is not None and min_price > max_price:
    raise HTTPException(
        status_code=422,
        detail="min_price must be less than or equal to max_price"
    )
```

**Design decision:** Returning 422 (Unprocessable Entity) rather than 400 (Bad Request) is intentional — 422 is the HTTP standard for semantically invalid input when the syntax is correct, and it is what FastAPI uses natively for validation errors. Using 422 consistently across manual and automatic validation keeps the API behaviour uniform.

### Updated Response Shape

The response shape from `GET /products` does not change — the same `products`, `total`, `page`, `limit`, `pages` envelope is returned. Sorting and filtering affect the content and `total` count.

---

## 2. `GET /health`

### Purpose

Provides an active liveness + readiness check for all backing stores. Used by Docker, load balancers, and monitoring systems. Also useful during development to quickly verify connectivity.

### Implementation

The health handler performs a lightweight probe against each store concurrently:

| Store         | Probe                                                   |
|---------------|---------------------------------------------------------|
| MySQL         | `SELECT 1` via the connection pool                      |
| Elasticsearch | `es.cluster.health()` or `es.ping()`                    |
| Redis         | `redis.ping()`                                          |

Each probe is wrapped in a `try/except` and timed. Latency is recorded in milliseconds.

### Response — All Healthy (200)

```json
{
  "status": "healthy",
  "checks": {
    "mysql": {
      "status": "ok",
      "latency_ms": 2.1
    },
    "elasticsearch": {
      "status": "ok",
      "latency_ms": 5.4
    },
    "redis": {
      "status": "ok",
      "latency_ms": 0.8
    }
  }
}
```

### Response — One or More Failing (503)

If any store probe fails, the top-level `status` becomes `"degraded"` and the HTTP status code is **503 Service Unavailable**. Individual checks that failed include an `"error"` field with the exception message.

```json
{
  "status": "degraded",
  "checks": {
    "mysql": {
      "status": "ok",
      "latency_ms": 1.9
    },
    "elasticsearch": {
      "status": "error",
      "error": "Connection refused"
    },
    "redis": {
      "status": "ok",
      "latency_ms": 0.7
    }
  }
}
```

**Design decision:** Returning 503 (not 200 with an error body) when degraded ensures that health checks used by load balancers and orchestrators correctly remove the instance from rotation. Monitoring systems that only inspect status codes work correctly without parsing the body.

**Design decision:** Probes run concurrently (via `asyncio.gather`) rather than sequentially so that a slow store does not inflate the latency measurements of healthy stores.

---

## Acceptance Criteria

- [ ] `GET /products?sort=price_asc` returns products ordered cheapest first.
- [ ] `GET /products?sort=price_desc` returns products ordered most expensive first.
- [ ] `GET /products?sort=rating_desc` returns products ordered by highest rating first.
- [ ] `GET /products?sort=discount_desc` returns products ordered by highest discount first.
- [ ] `GET /products?sort=invalid` returns HTTP 422.
- [ ] `GET /products?min_price=50&max_price=200` returns only products with price in [50, 200].
- [ ] `GET /products?min_price=500&max_price=100` returns HTTP 422.
- [ ] `GET /products?category=smartphones` returns only products in that category.
- [ ] Sorting and filtering can be combined (e.g. `?category=smartphones&sort=price_asc&min_price=100`).
- [ ] `GET /health` returns HTTP 200 and `"status": "healthy"` when all stores are running.
- [ ] `GET /health` returns HTTP 503 and `"status": "degraded"` when a store is unreachable.
- [ ] Health response includes `latency_ms` for each passing store.
- [ ] Health probes run concurrently (not sequentially).
