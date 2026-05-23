# Spec: Core API Bootstrap

## Objective

Stand up a fully containerized e-commerce API with FastAPI, MySQL 8.0, Elasticsearch 8.13, and Redis 7.2 using Docker Compose. On startup, ingest product and category data from dummyjson.com into both MySQL and Elasticsearch so the service is immediately queryable without manual seeding. Expose two read endpoints: `GET /categories` and `GET /products` (paginated).

---

## 1. Docker Compose Setup

### Services

| Service         | Image                    | Port | Purpose                         |
|-----------------|--------------------------|------|---------------------------------|
| `db`            | mysql:8.0                | 3306 | Normalized relational storage   |
| `elasticsearch` | elasticsearch:8.13.0     | 9200 | Full-text search + aggregations |
| `redis`         | redis:7.2                | 6379 | Caching layer                   |
| `app`           | built from Dockerfile    | 8000 | FastAPI application             |

### Design Decisions

- MySQL uses `MYSQL_ROOT_PASSWORD` / `MYSQL_DATABASE` env vars; no anonymous access.
- Elasticsearch runs in single-node mode with `xpack.security.enabled=false` for local dev simplicity.
- Redis runs with default config; no persistence required for caching.
- `app` uses `depends_on: condition: service_healthy` for all three stores — prevents the app from booting before stores are ready.
- A named volume `mysql_data` persists the database across `docker compose down` restarts (without `--volumes`).
- All services share a single bridge network `ecommerce_net` for DNS-based service discovery.

### Environment Variables (app service)

```
DATABASE_URL=mysql+aiomysql://root:password@db:3306/ecommerce
ELASTICSEARCH_URL=http://elasticsearch:9200
REDIS_URL=redis://redis:6379
```

---

## 2. Startup Ingestion Pipeline

### Source

`https://dummyjson.com/products?limit=194&skip=0` — returns all ~194 products with embedded category strings.

### Trigger

Runs inside a FastAPI `lifespan` async context manager (replaces deprecated `@app.on_event("startup")`). The lifespan function owns connection pool setup and teardown in addition to ingestion.

### Idempotency

Before inserting any data, the pipeline checks whether rows already exist:

```python
# Pseudo-code
existing = await db.execute(select(func.count()).select_from(Product))
if existing.scalar() > 0:
    logger.info("Data already seeded, skipping ingestion")
    return
```

This makes the pipeline safe to run on every container restart without duplicating data.

### Elasticsearch Index Bootstrap

Before inserting documents, the pipeline calls `es.indices.exists(index="products")`. If the index does not exist it creates it with the full mapping defined in Section 3. This is also idempotent.

### Pipeline Steps (in order)

1. Fetch all products from dummyjson.com (single HTTP GET).
2. Extract unique category strings; upsert into `categories` table.
3. For each product: insert into `products`, then insert associated rows into `product_tags` and `product_images`.
4. Build an ES document for each product and bulk-index into the `products` index.
5. Log summary: `Seeded {n} products across {k} categories`.

---

## 3. MySQL Normalized Schema

### `categories`

```sql
CREATE TABLE categories (
    id   INT AUTO_INCREMENT PRIMARY KEY,
    slug VARCHAR(100) NOT NULL UNIQUE,
    name VARCHAR(100) NOT NULL
);
```

`slug` is the raw string from dummyjson (e.g. `"smartphones"`). `name` is a title-cased display label.

### `products`

```sql
CREATE TABLE products (
    id                  INT PRIMARY KEY,
    title               VARCHAR(255) NOT NULL,
    description         TEXT,
    price               DECIMAL(10,2) NOT NULL,
    discount_percentage DECIMAL(5,2)  DEFAULT 0,
    rating              DECIMAL(3,2),
    stock               INT          DEFAULT 0,
    brand               VARCHAR(100),
    sku                 VARCHAR(100),
    weight              DECIMAL(6,2),
    thumbnail           VARCHAR(500),
    category_id         INT NOT NULL,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (category_id) REFERENCES categories(id)
);
```

Design notes:
- `id` uses the dummyjson product ID directly (not auto-increment). This makes re-ingestion idempotent via `INSERT IGNORE` or `ON DUPLICATE KEY UPDATE`.
- `price` is `DECIMAL` not `FLOAT` to avoid floating-point rounding errors in financial contexts.
- `thumbnail` is stored on the product row (single value); multiple images live in `product_images`.

### `product_tags`

```sql
CREATE TABLE product_tags (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    product_id INT NOT NULL,
    tag        VARCHAR(100) NOT NULL,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
);
```

### `product_images`

```sql
CREATE TABLE product_images (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    product_id INT NOT NULL,
    url        VARCHAR(500) NOT NULL,
    sort_order INT DEFAULT 0,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
);
```

`sort_order` preserves the original image array ordering from the dummyjson response.

**Schema design rationale:** Tags and images are in separate tables rather than JSON columns. This allows indexing on `tag`, enforces referential integrity via foreign keys, and keeps queries clean without JSON path functions.

---

## 4. Elasticsearch Index Mapping

Index name: `products`

```json
{
  "mappings": {
    "properties": {
      "id":                  { "type": "integer" },
      "title":               { "type": "text" },
      "description":         { "type": "text" },
      "brand":               { "type": "keyword" },
      "category":            { "type": "keyword" },
      "tags":                { "type": "keyword" },
      "price":               { "type": "float" },
      "rating":              { "type": "float" },
      "discount_percentage": { "type": "float" },
      "stock":               { "type": "integer" },
      "thumbnail":           { "type": "keyword", "index": false },
      "images":              { "type": "keyword", "index": false }
    }
  }
}
```

### Mapping Design Decisions

- `title` and `description` are `text` — they pass through the standard analyzer (tokenization, lowercasing, stemming) for full-text search.
- `brand`, `category`, `tags` are `keyword` — matched exactly and used in aggregations and facets. Mixed-case brand names like "Apple" must be queried as-is.
- `price`, `rating`, `discount_percentage` are `float` — supports range queries and numeric aggregations.
- `thumbnail` and `images` are `keyword` with `"index": false` — stored in `_source` for retrieval but never queried or aggregated, saving index space and avoiding URL strings polluting the keyword vocabulary.

---

## 5. API Endpoints

### `GET /categories`

Returns the full list of categories from MySQL. No pagination — the dataset has ~26 categories and is effectively static.

**Response:**

```json
{
  "categories": [
    { "id": 1, "slug": "smartphones", "name": "Smartphones" }
  ],
  "total": 26
}
```

### `GET /products`

Returns a paginated product list from MySQL.

**Query Parameters:**

| Parameter | Type | Default | Description              |
|-----------|------|---------|--------------------------|
| `page`    | int  | 1       | 1-based page number      |
| `limit`   | int  | 20      | Items per page (max 100) |

**Response:**

```json
{
  "products": [ { "...product fields..." } ],
  "total": 194,
  "page": 1,
  "limit": 20,
  "pages": 10
}
```

Each product object includes: `id`, `title`, `description`, `price`, `discount_percentage`, `rating`, `stock`, `brand`, `thumbnail`, `category` (slug string), `tags` (array), `images` (array).

**Design Decision — MySQL for base listing:** The default products list is served from MySQL rather than Elasticsearch. MySQL is the source of truth; ES is reserved for search-specific features introduced in later specs. Keeping the default list on MySQL avoids building ES as a primary read path prematurely.

---

## Acceptance Criteria

- [ ] `docker compose up --build` starts all four services without errors and without manual steps.
- [ ] MySQL, Elasticsearch, and Redis pass their health checks before the app container starts.
- [ ] On first boot, the ingestion pipeline fetches from dummyjson.com and populates all four MySQL tables.
- [ ] On second boot (data already present), ingestion is skipped and a log line confirms it.
- [ ] `GET /categories` returns all categories with correct `slug` and `name` fields.
- [ ] `GET /products` returns paginated results with correct `total`, `page`, `limit`, and `pages` fields.
- [ ] `GET /products?page=2&limit=10` returns a different slice of products than page 1.
- [ ] The Elasticsearch `products` index exists with the correct mapping after startup.
- [ ] `thumbnail` and `images` fields appear in ES `_source` but are not indexed (verifiable via the mapping API).
- [ ] No duplicate products exist in MySQL or ES after restarting the container multiple times.
