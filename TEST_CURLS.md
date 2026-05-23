# API Test Curls & Responses

All responses below are real — captured from a running instance with 194 ingested products.  
Requires `docker compose up -d` to be running.

---

## Health

```bash
curl http://localhost:8000/health
```
```json
{
  "status": "ok",
  "dependencies": {
    "mysql":         { "status": "ok", "latency_ms": 1 },
    "elasticsearch": { "status": "ok", "latency_ms": 4 },
    "redis":         { "status": "ok", "latency_ms": 1 }
  }
}
```

---

## Categories

### List all categories (cache miss)
```bash
curl http://localhost:8000/categories
```
```json
{
  "data": [
    { "id": 1, "name": "beauty" },
    { "id": 2, "name": "fragrances" }
    // ... 22 more
  ],
  "total": 24,
  "cached": false
}
```

### Second call (cache hit)
```bash
curl http://localhost:8000/categories
```
```json
{
  "data": [ ... ],
  "total": 24,
  "cached": true
}
```

---

## Products — Read

### List with pagination
```bash
curl "http://localhost:8000/products?page=1&limit=2"
```
```json
{
  "data": [
    {
      "id": 1,
      "title": "Essence Mascara Lash Princess",
      "description": "The Essence Mascara Lash Princess is a popular mascara...",
      "price": 9.99,
      "discount_percentage": 10.48,
      "rating": 2.56,
      "stock": 99,
      "brand": "Essence",
      "sku": "BEA-ESS-ESS-001",
      "category": "beauty",
      "tags": ["beauty", "mascara"],
      "thumbnail": "https://cdn.dummyjson.com/product-images/beauty/essence-mascara-lash-princess/thumbnail.webp",
      "images": ["https://cdn.dummyjson.com/product-images/beauty/essence-mascara-lash-princess/1.webp"]
    }
  ],
  "total": 194,
  "page": 1,
  "limit": 2,
  "pages": 97
}
```

### Full-text search (Elasticsearch)
```bash
curl "http://localhost:8000/products?query=apple&limit=2"
```
```json
{
  "data": [
    {
      "id": 16,
      "title": "Apple",
      "price": 1.99,
      "category": "groceries",
      "tags": ["fruits"]
    }
  ],
  "total": 11,
  "page": 1,
  "limit": 2,
  "pages": 6,
  "facets": {
    "categories": [
      { "name": "mobile-accessories", "count": 7 },
      { "name": "groceries",          "count": 1 },
      { "name": "laptops",            "count": 1 },
      { "name": "tablets",            "count": 1 },
      { "name": "womens-bags",        "count": 1 }
    ],
    "brands": [
      { "name": "Apple",      "count": 9 },
      { "name": "Urban Chic", "count": 1 }
    ]
  }
}
```

### Category filter + price range + sort (composable)
```bash
curl "http://localhost:8000/products?category=smartphones&min_price=500&sort=price_asc&limit=2"
```
```json
{
  "data": [
    {
      "id": 133,
      "title": "Samsung Galaxy S10",
      "price": 699.99,
      "discount_percentage": 5.59,
      "rating": 3.06,
      "stock": 19,
      "brand": "Samsung",
      "category": "smartphones",
      "tags": ["smartphones", "samsung galaxy"]
    }
  ],
  "total": 3,
  "page": 1,
  "limit": 2,
  "pages": 2
}
```

### Single product by ID
```bash
curl http://localhost:8000/products/1
```
```json
{
  "id": 1,
  "title": "Essence Mascara Lash Princess",
  "description": "The Essence Mascara Lash Princess is a popular mascara...",
  "price": 9.99,
  "discount_percentage": 10.48,
  "rating": 2.56,
  "stock": 99,
  "brand": "Essence",
  "sku": "BEA-ESS-ESS-001",
  "category": "beauty",
  "tags": ["beauty", "mascara"],
  "thumbnail": "https://cdn.dummyjson.com/product-images/beauty/essence-mascara-lash-princess/thumbnail.webp",
  "images": ["https://cdn.dummyjson.com/product-images/beauty/essence-mascara-lash-princess/1.webp"]
}
```

### Similar products (Elasticsearch MLT)
```bash
curl http://localhost:8000/products/1/similar
```
```json
{
  "product_id": 1,
  "similar": [
    {
      "id": 4,
      "title": "Red Lipstick",
      "price": 12.99,
      "rating": 4.36,
      "category": "beauty",
      "thumbnail": "https://cdn.dummyjson.com/product-images/beauty/red-lipstick/thumbnail.webp"
    }
  ]
}
```

### Stats (Elasticsearch aggregations)
```bash
curl http://localhost:8000/products/stats
```
```json
{
  "total_products": 194,
  "avg_price": 1562.1,
  "avg_rating": 3.8,
  "avg_discount": 10.56,
  "price_ranges": {
    "under_50":    118,
    "50_to_200":    27,
    "200_to_1000":  24,
    "above_1000":   26
  },
  "top_categories": [
    { "name": "kitchen-accessories", "count": 30 },
    { "name": "groceries",           "count": 27 },
    { "name": "smartphones",         "count": 17 },
    { "name": "sports-accessories",  "count": 17 },
    { "name": "mobile-accessories",  "count": 14 }
  ],
  "top_brands": [
    { "name": "Apple",          "count": 14 },
    { "name": "Rolex",          "count":  6 },
    { "name": "Samsung",        "count":  5 },
    { "name": "Fashion Shades", "count":  4 },
    { "name": "Dodge",          "count":  3 }
  ]
}
```

### Stats — cache hit
```bash
curl http://localhost:8000/products/stats   # second call
```
```json
{
  "total_products": 194,
  "avg_price": 1562.1,
  "avg_rating": 3.8,
  "cached": true,
  ...
}
```

### Top rated products
```bash
curl "http://localhost:8000/products/top-rated?min_rating=4.8&limit=2"
```
```json
{
  "data": [
    {
      "id": 99,
      "title": "Amazon Echo Plus",
      "price": 99.99,
      "discount_percentage": 12.07,
      "rating": 4.99,
      "stock": 61,
      "brand": "Amazon",
      "category": "mobile-accessories",
      "tags": ["electronics", "smart speakers"]
    }
  ],
  "total": 19,
  "page": 1,
  "limit": 2,
  "pages": 10,
  "min_rating": 4.8
}
```

### On sale products
```bash
curl "http://localhost:8000/products/on-sale?min_discount=10&limit=2"
```
```json
{
  "data": [ ... ],
  "total": 99,
  "page": 1,
  "limit": 2,
  "pages": 50,
  "min_discount": 10.0
}
```

### Autocomplete suggestions (Elasticsearch prefix)
```bash
curl "http://localhost:8000/products/suggestions?query=mac"
```
```json
{
  "suggestions": [
    {
      "id": 78,
      "title": "Apple MacBook Pro 14 Inch Space Grey",
      "category": "laptops",
      "thumbnail": "https://cdn.dummyjson.com/product-images/laptops/apple-macbook-pro-14-inch-space-grey/thumbnail.webp"
    }
  ]
}
```

---

## Global Search

```bash
curl "http://localhost:8000/search?query=phone&limit=2"
```
```json
{
  "query": "phone",
  "products": [
    {
      "id": 121,
      "title": "iPhone 5s",
      "price": 199.99,
      "rating": 2.83,
      "category": "smartphones",
      "thumbnail": "https://cdn.dummyjson.com/product-images/smartphones/iphone-5s/thumbnail.webp"
    }
  ],
  "categories": [
    { "id": 14, "name": "smartphones" }
  ],
  "totals": {
    "products": 8,
    "categories": 1
  }
}
```

---

## Products — Write

### Create product
```bash
curl -X POST http://localhost:8000/products \
  -H "Content-Type: application/json" \
  -d '{
    "title": "iPhone 15 Pro",
    "description": "Latest Apple flagship.",
    "price": 999.99,
    "discount_percentage": 5.0,
    "stock": 50,
    "brand": "Apple",
    "sku": "APL-IP15P-001",
    "category": "smartphones",
    "tags": ["apple", "flagship"]
  }'
```
```json
{
  "id": 197,
  "title": "iPhone 15 Pro",
  "description": "Latest Apple flagship.",
  "price": 999.99,
  "discount_percentage": 5.0,
  "rating": null,
  "stock": 50,
  "brand": "Apple",
  "sku": "APL-IP15P-001",
  "category": "smartphones",
  "tags": ["apple", "flagship"],
  "thumbnail": null,
  "images": []
}
```
HTTP 201 Created. Written to MySQL + Elasticsearch. Stats cache invalidated.

### Update product (partial)
```bash
curl -X PUT http://localhost:8000/products/197 \
  -H "Content-Type: application/json" \
  -d '{"price": 899.99, "stock": 45}'
```
```json
{
  "id": 197,
  "title": "iPhone 15 Pro",
  "price": 899.99,
  "stock": 45,
  "brand": "Apple",
  "category": "smartphones",
  "tags": ["apple", "flagship"],
  ...
}
```
HTTP 200. Synced to MySQL + Elasticsearch. Stats cache invalidated.

### Delete product
```bash
curl -X DELETE http://localhost:8000/products/197
```
```json
{
  "message": "Product 197 deleted successfully"
}
```
HTTP 200. Removed from MySQL + Elasticsearch. Stats cache invalidated.

---

## Bulk Create

### Success
```bash
curl -X POST http://localhost:8000/products/bulk \
  -H "Content-Type: application/json" \
  -d '{"products": [
    {"title": "Bulk A", "price": 9.99, "category": "beauty"},
    {"title": "Bulk B", "price": 19.99, "category": "laptops"}
  ]}'
```
```json
{
  "created": 2,
  "ids": [198, 199],
  "duration_ms": 19
}
```
HTTP 201. Single DB transaction. ES bulk indexed. Stats cache invalidated.

### Invalid category in one item (entire request rejected)
```bash
curl -X POST http://localhost:8000/products/bulk \
  -H "Content-Type: application/json" \
  -d '{"products": [
    {"title": "Good", "price": 9.99, "category": "beauty"},
    {"title": "Bad",  "price": 9.99, "category": "doesnotexist"}
  ]}'
```
```json
{
  "detail": {
    "code": 422,
    "message": "Validation failed for 1 product(s)",
    "errors": [{ "index": 1, "error": "Category 'doesnotexist' not found" }]
  }
}
```

---

## Stock Management

### Restock (positive delta)
```bash
curl -X PATCH http://localhost:8000/products/1/stock \
  -H "Content-Type: application/json" \
  -d '{"delta": 20, "reason": "restocked from supplier"}'
```
```json
{
  "product_id": 1,
  "previous_stock": 99,
  "delta": 20,
  "current_stock": 119,
  "reason": "restocked from supplier"
}
```

### Write-off (negative delta)
```bash
curl -X PATCH http://localhost:8000/products/1/stock \
  -H "Content-Type: application/json" \
  -d '{"delta": -5, "reason": "damaged in warehouse"}'
```
```json
{
  "product_id": 1,
  "previous_stock": 119,
  "delta": -5,
  "current_stock": 114,
  "reason": "damaged in warehouse"
}
```

### Would go negative (409)
```bash
curl -X PATCH http://localhost:8000/products/1/stock \
  -H "Content-Type: application/json" \
  -d '{"delta": -99999}'
```
```json
{ "detail": { "code": 409, "message": "Insufficient stock. Available: 114, requested reduction: 99999" } }
```

---

## Buy

### Successful purchase
```bash
curl -X POST http://localhost:8000/products/1/buy \
  -H "Content-Type: application/json" \
  -d '{"quantity": 2}'
```
```json
{
  "product_id": 1,
  "product_title": "Essence Mascara Lash Princess",
  "quantity_purchased": 2,
  "stock_remaining": 112
}
```
Atomic — uses `UPDATE WHERE stock >= quantity`. No overselling possible.

### Insufficient stock (409)
```bash
curl -X POST http://localhost:8000/products/1/buy \
  -H "Content-Type: application/json" \
  -d '{"quantity": 500}'
```
```json
{ "detail": { "code": 409, "message": "Insufficient stock. Available: 112, requested: 500" } }
```

---

## Admin — Re-index

```bash
curl -X POST http://localhost:8000/admin/reindex
```
```json
{
  "indexed": 194,
  "failed": 0,
  "duration_ms": 152,
  "status": "ok"
}
```
Force-syncs all MySQL products into Elasticsearch. Use after any ES failure.

---

## Prometheus Metrics

```bash
curl http://localhost:8000/metrics
```
```
# HELP http_requests_total Total number of requests by method, status and handler.
# TYPE http_requests_total counter
http_requests_total{handler="/products/{product_id}",method="GET",status="2xx"} 1.0
http_requests_total{handler="/health",method="GET",status="2xx"} 4.0
...
```

---

## Response Headers

Every response includes:

```bash
curl -si http://localhost:8000/health | grep -E "x-request-id|x-response-time"
```
```
x-request-id: 637961cb-d032-4c42-ac9b-fd49d957f527
x-response-time: 3ms
```

### Supply your own request ID (echoed back)
```bash
curl -si -H "X-Request-ID: my-trace-123" http://localhost:8000/health | grep x-request-id
```
```
x-request-id: my-trace-123
```

---

## Rate Limiting

Write endpoints return `429` after exceeding per-IP limits. Example after 30 POSTs/min:
```json
{ "error": "Rate limit exceeded: 30 per 1 minute" }
```

---

## Redis Cache Invalidation

Stats cache is deleted on every write. Example — delete a product, then hit stats:

```bash
curl -X DELETE http://localhost:8000/products/197
curl http://localhost:8000/products/stats
```
```json
{
  "total_products": 194,   // count dropped by 1
  "avg_price": 1570.1,     // recalculated fresh
  "avg_rating": 3.8,
  "cached": false          // cache was busted by the delete
}
```

---

## Error Responses

### Product not found (404)
```bash
curl http://localhost:8000/products/9999
```
```json
{ "code": 404, "message": "Not found" }
```

### Invalid sort value (422)
```bash
curl "http://localhost:8000/products?sort=invalid"
```
```json
{
  "detail": {
    "code": 422,
    "message": "Invalid sort value. Allowed: price_asc, price_desc, rating_asc, rating_desc, discount_desc"
  }
}
```

### min_price > max_price (422)
```bash
curl "http://localhost:8000/products?min_price=500&max_price=100"
```
```json
{
  "detail": {
    "code": 422,
    "message": "min_price cannot be greater than max_price"
  }
}
```

### Unknown category (empty result, not error)
```bash
curl "http://localhost:8000/products?category=doesnotexist"
```
```json
{
  "data": [],
  "total": 0,
  "page": 1,
  "limit": 20,
  "pages": 0
}
```
