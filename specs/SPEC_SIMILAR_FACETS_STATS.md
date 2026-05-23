# Spec: Similar Products, Search Facets, and Product Stats

## Objective

Add three Elasticsearch-powered features that go beyond basic CRUD: (1) a "more like this" endpoint to find products similar to a given product, (2) aggregation-backed facets returned alongside text search results so clients can offer drill-down filtering, and (3) a statistics endpoint that aggregates pricing, rating, discount, and categorical distribution across the entire catalogue.

---

## 1. `GET /products/{id}/similar`

### Purpose

Returns products that are semantically similar to the specified product, based on shared vocabulary in their title and description fields. Useful for "customers also viewed" or "related products" UI sections.

### Elasticsearch Query

Uses the [`more_like_this`](https://www.elastic.co/guide/en/elasticsearch/reference/current/query-dsl-mlt-query.html) (MLT) query, which analyses the target document's field values, extracts the most significant terms, and constructs a query to find documents sharing those terms.

```json
{
  "query": {
    "more_like_this": {
      "fields": ["title", "description"],
      "like": [
        {
          "_index": "products",
          "_id": "{id}"
        }
      ],
      "min_term_freq": 1,
      "min_doc_freq": 1,
      "minimum_should_match": "20%"
    }
  },
  "size": 5
}
```

### Parameter Decisions

- **`fields: ["title", "description"]`** — Title carries the strongest signal (brand, product type, model). Description adds supporting vocabulary. Including other fields like `brand` or `tags` would risk over-matching on very common terms.
- **`minimum_should_match: "20%"`** — A percentage-based threshold works proportionally regardless of how many significant terms are extracted. A hard number (e.g. `2`) would be too strict for short titles and too loose for long descriptions.
- **`min_term_freq: 1` and `min_doc_freq: 1`** — Lowered from ES defaults (2 and 5) because the catalogue is small (~194 products). Default values would discard too many terms from a small corpus.
- **`size: 5`** — Returns 5 similar products; enough for a "related products" carousel without overwhelming the response.

### Behaviour

- The target product itself is automatically excluded from MLT results by Elasticsearch.
- If the product ID does not exist in MySQL, return HTTP 404 before calling ES.
- If ES returns 0 results (no similar products found), return an empty array — not an error.

### Response

```json
{
  "product_id": 1,
  "similar": [
    { "id": 12, "title": "...", "price": 49.99, "thumbnail": "...", "category": "smartphones" }
  ],
  "total": 5
}
```

---

## 2. Search Facets on `GET /products?query=`

### Purpose

When a client performs a text search via `?query=`, return aggregation buckets alongside the product results so the UI can render "Filter by category" and "Filter by brand" drill-down panels without a second request.

### Trigger

Facets are only computed and returned when the `query` parameter is present. They are omitted from non-search (browse) responses to avoid unnecessary aggregation overhead on every paginated list request.

### Elasticsearch Query with Aggregations

The search query is a `multi_match` against `title` and `description`. Aggregations are appended to the same request body:

```json
{
  "query": {
    "multi_match": {
      "query": "laptop",
      "fields": ["title^2", "description"]
    }
  },
  "aggs": {
    "by_category": {
      "terms": { "field": "category", "size": 20 }
    },
    "by_brand": {
      "terms": { "field": "brand", "size": 20 }
    }
  },
  "from": 0,
  "size": 20
}
```

**`title^2`** — Boosts matches in `title` over `description`. A product matching "laptop" in its title is more relevant than one where "laptop" only appears in the description body.

### Facet Response Shape

Facets are included in the top-level `GET /products` response when `query` is present:

```json
{
  "products": [ { "...product..." } ],
  "total": 18,
  "page": 1,
  "limit": 20,
  "pages": 1,
  "facets": {
    "categories": [
      { "slug": "laptops", "count": 9 },
      { "slug": "smartphones", "count": 4 }
    ],
    "brands": [
      { "name": "Apple", "count": 6 },
      { "name": "Samsung", "count": 3 }
    ]
  }
}
```

**Design decision:** Facets are embedded in the products response rather than a separate endpoint. This avoids a second round-trip from the client and reflects the fact that facets are tightly coupled to the current query — they are meaningless without it.

**Design decision:** When `query` is absent, `facets` is omitted from the response entirely (not returned as `null` or `{}`). This keeps the browse-mode response shape clean and avoids clients needing to handle a dummy facet structure.

---

## 3. `GET /products/stats`

### Purpose

Returns catalogue-wide statistics computed via Elasticsearch aggregations. Intended for a stats bar displayed at the top of the frontend UI.

### Elasticsearch Aggregations

A single query with no filter (match_all) and multiple aggregations:

```json
{
  "query": { "match_all": {} },
  "size": 0,
  "aggs": {
    "total_products":  { "value_count": { "field": "id" } },
    "avg_price":       { "avg": { "field": "price" } },
    "avg_rating":      { "avg": { "field": "rating" } },
    "avg_discount":    { "avg": { "field": "discount_percentage" } },
    "price_ranges": {
      "range": {
        "field": "price",
        "ranges": [
          { "key": "under_50",      "to": 50 },
          { "key": "50_to_200",     "from": 50,  "to": 200 },
          { "key": "200_to_1000",   "from": 200, "to": 1000 },
          { "key": "above_1000",    "from": 1000 }
        ]
      }
    },
    "top_categories": {
      "terms": { "field": "category", "size": 5 }
    },
    "top_brands": {
      "terms": { "field": "brand", "size": 5 }
    }
  }
}
```

**`size: 0`** — No documents are returned in hits; we only need the aggregation results. This is the standard pattern for aggregation-only queries.

### Response

```json
{
  "total_products": 194,
  "avg_price": 423.17,
  "avg_rating": 4.22,
  "avg_discount": 12.68,
  "price_ranges": {
    "under_50": 18,
    "50_to_200": 62,
    "200_to_1000": 89,
    "above_1000": 25
  },
  "top_categories": [
    { "slug": "smartphones",    "count": 24 },
    { "slug": "laptops",        "count": 18 },
    { "slug": "fragrances",     "count": 11 },
    { "slug": "skincare",       "count": 11 },
    { "slug": "groceries",      "count": 10 }
  ],
  "top_brands": [
    { "name": "Apple",   "count": 12 },
    { "name": "Samsung", "count": 8 }
  ]
}
```

**Design decision:** Stats are served from Elasticsearch rather than MySQL because all the required aggregations (avg, range, terms) are native ES operations that return in a single round-trip. Replicating these in MySQL would require multiple queries and more complex SQL with GROUP BY and CASE expressions.

**Note on route ordering:** `GET /products/stats` must be registered in FastAPI **before** `GET /products/{product_id}`. FastAPI matches routes in registration order; if `/{product_id}` is registered first, the literal string `"stats"` will be captured as a product ID and result in a 404 or incorrect lookup. See SPEC_TOPRATED_ONSALE_GLOBALSEARCH.md for the full list of static routes that must precede `/{product_id}`.

---

## Acceptance Criteria

- [ ] `GET /products/1/similar` returns up to 5 products similar to product 1.
- [ ] Product 1 itself is not included in the similar results.
- [ ] `GET /products/99999/similar` returns HTTP 404 when the product does not exist.
- [ ] `GET /products/1/similar` returns an empty `similar` array (not an error) if no similar products are found.
- [ ] `GET /products?query=laptop` returns products with a `facets` key in the response.
- [ ] Facets include `categories` and `brands` arrays with correct `count` values.
- [ ] `GET /products?page=1&limit=20` (no query) does not include a `facets` key.
- [ ] `GET /products/stats` returns `total_products`, `avg_price`, `avg_rating`, `avg_discount`.
- [ ] `GET /products/stats` returns `price_ranges` with all four range buckets: `under_50`, `50_to_200`, `200_to_1000`, `above_1000`.
- [ ] `GET /products/stats` returns `top_categories` and `top_brands` with at most 5 entries each.
- [ ] Stats values are numeric (not strings); monetary values are rounded to 2 decimal places.
- [ ] The route `/products/stats` does not conflict with `/products/{product_id}`.
