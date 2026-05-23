# Spec: Top Rated, On Sale, and Global Search Endpoints

## Objective

Add three read-only endpoints that expose common e-commerce discovery patterns: browsing top-rated products, browsing products currently on sale, and a global cross-resource search that searches both products (via Elasticsearch) and categories (via MySQL) in a single request. Also document the critical FastAPI route ordering constraint that prevents static routes from being shadowed by a dynamic `/{product_id}` route.

---

## 1. `GET /products/top-rated`

### Purpose

Returns products whose rating meets or exceeds a caller-specified threshold. Intended for a "Top Rated" section in the UI.

### Query Parameters

| Parameter    | Type  | Default | Description                              |
|--------------|-------|---------|------------------------------------------|
| `min_rating` | float | 4.5     | Minimum rating threshold (inclusive)     |
| `limit`      | int   | 20      | Maximum number of products to return     |

### Implementation

MySQL query:

```sql
SELECT p.*, c.slug AS category
FROM products p
JOIN categories c ON p.category_id = c.id
WHERE p.rating >= :min_rating
ORDER BY p.rating DESC
LIMIT :limit
```

**Design decision:** MySQL is used here rather than Elasticsearch. This is a simple numeric range filter with no text search involved. MySQL handles it efficiently with an index on `rating`. Using ES for this would add a round-trip to a search engine for a query that a relational database handles natively.

### Validation

- `min_rating` must be between 0.0 and 5.0. Return HTTP 422 if outside this range.
- `limit` must be between 1 and 100. Return HTTP 422 if outside this range.

### Response

```json
{
  "products": [ { "...product fields..." } ],
  "total": 42,
  "min_rating": 4.5
}
```

---

## 2. `GET /products/on-sale`

### Purpose

Returns products with a discount percentage at or above a caller-specified threshold. Intended for a "On Sale" or "Deals" section in the UI.

### Query Parameters

| Parameter      | Type  | Default | Description                                    |
|----------------|-------|---------|------------------------------------------------|
| `min_discount` | float | 10      | Minimum discount percentage threshold (inclusive) |
| `limit`        | int   | 20      | Maximum number of products to return           |

### Implementation

MySQL query:

```sql
SELECT p.*, c.slug AS category
FROM products p
JOIN categories c ON p.category_id = c.id
WHERE p.discount_percentage >= :min_discount
ORDER BY p.discount_percentage DESC
LIMIT :limit
```

**Design decision:** Same rationale as top-rated — this is a numeric range filter on a single column. No text search; MySQL is the right tool.

### Validation

- `min_discount` must be between 0 and 100. Return HTTP 422 if outside this range.
- `limit` must be between 1 and 100. Return HTTP 422 if outside this range.

### Response

```json
{
  "products": [ { "...product fields..." } ],
  "total": 87,
  "min_discount": 10
}
```

---

## 3. `GET /search`

### Purpose

Global cross-resource search that returns results from multiple resource types (products and categories) grouped by type in a single response. Intended for a global search bar that surfaces both categories and products simultaneously.

### Query Parameters

| Parameter | Type   | Required | Description                        |
|-----------|--------|----------|------------------------------------|
| `query`   | string | Yes      | The search string                  |
| `limit`   | int    | 10       | Maximum results per resource type  |

If `query` is empty or missing, return HTTP 422.

### Implementation

The handler fires two lookups in parallel (`asyncio.gather`):

**Products — Elasticsearch `multi_match`:**

```json
{
  "query": {
    "multi_match": {
      "query": "phone",
      "fields": ["title^2", "description", "brand", "tags"]
    }
  },
  "size": 10
}
```

Including `brand` and `tags` fields (in addition to `title` and `description`) in the global search gives broader coverage — a user searching "Apple" should match brand-indexed products even if "Apple" does not appear in the product title.

**Categories — MySQL `LIKE`:**

```sql
SELECT id, slug, name
FROM categories
WHERE name LIKE :pattern OR slug LIKE :pattern
LIMIT :limit
```

Where `:pattern = f"%{query}%"`. Categories have only ~26 rows so a `LIKE` scan is negligible.

### Response

```json
{
  "query": "phone",
  "results": {
    "products": {
      "items": [
        {
          "id": 1,
          "title": "iPhone 9",
          "price": 549.99,
          "thumbnail": "https://...",
          "category": "smartphones",
          "type": "product"
        }
      ],
      "total": 12
    },
    "categories": {
      "items": [
        { "id": 2, "slug": "smartphones", "name": "Smartphones", "type": "category" }
      ],
      "total": 1
    }
  },
  "total": 13
}
```

**Design decision:** Results are grouped by type rather than interleaved. This makes it straightforward for the frontend to render separate sections ("Products" and "Categories") with individual counts. An interleaved response would require the client to sort by type itself and would obscure the per-type totals.

**`total` at the top level** is the sum of products and categories found. This gives a quick "X results" headline without the client summing the individual totals.

---

## 4. FastAPI Route Ordering — Critical Constraint

FastAPI (and the underlying Starlette router) evaluates routes in the order they are **registered**, not alphabetically or by specificity. A dynamic path parameter like `/{product_id}` will match any string, including literal words like `stats`, `top-rated`, `on-sale`, and `suggestions`.

### Rule

**All static routes must be registered before `/{product_id}`.**

### Routes Affected

The following routes under `/products` must be registered before `GET /products/{product_id}`:

| Route                          | Reason it would be shadowed           |
|--------------------------------|---------------------------------------|
| `GET /products/stats`          | `"stats"` captured as `product_id`    |
| `GET /products/top-rated`      | `"top-rated"` captured as `product_id`|
| `GET /products/on-sale`        | `"on-sale"` captured as `product_id`  |
| `GET /products/suggestions`    | `"suggestions"` captured as `product_id` |

### Recommended Registration Order

```python
router.add_api_route("/products/stats",       get_stats,       methods=["GET"])
router.add_api_route("/products/top-rated",   get_top_rated,   methods=["GET"])
router.add_api_route("/products/on-sale",     get_on_sale,     methods=["GET"])
router.add_api_route("/products/suggestions", get_suggestions, methods=["GET"])
router.add_api_route("/products/{product_id}", get_product,    methods=["GET"])
```

Or equivalently, using decorator order in a router file — decorators at the top of the file register first.

**This is not a FastAPI bug** — it is standard HTTP router behaviour. Document it here so it is not accidentally broken when new routes are added.

---

## Acceptance Criteria

- [ ] `GET /products/top-rated` returns products with rating >= 4.5 by default, ordered by rating descending.
- [ ] `GET /products/top-rated?min_rating=3.0` returns products with rating >= 3.0.
- [ ] `GET /products/top-rated?min_rating=6.0` returns HTTP 422 (out of 0–5 range).
- [ ] `GET /products/on-sale` returns products with discount >= 10% by default, ordered by discount descending.
- [ ] `GET /products/on-sale?min_discount=50` returns only products with discount >= 50%.
- [ ] `GET /products/on-sale?min_discount=150` returns HTTP 422 (out of 0–100 range).
- [ ] `GET /search?query=phone` returns a response with both `products` and `categories` groups.
- [ ] `GET /search?query=phone` products are sourced from Elasticsearch (text relevance scoring).
- [ ] `GET /search?query=smartphones` categories include the "Smartphones" category entry.
- [ ] `GET /search` (missing query) returns HTTP 422.
- [ ] `GET /products/stats` does not return a 404 (route ordering is correct).
- [ ] `GET /products/top-rated` does not return a 404 (route ordering is correct).
- [ ] `GET /products/on-sale` does not return a 404 (route ordering is correct).
- [ ] Products and categories search run concurrently (not sequentially).
