# Spec: CRUD Write and Delete Endpoints

## Objective

Add write endpoints to the API: create a new product (`POST /products`), partially update an existing product (`PUT /products/{id}`), and delete a product (`DELETE /products/{id}`). All mutations follow a dual-write pattern — MySQL is written first as the source of truth, then Elasticsearch is kept in sync. ES failures are logged but never surface as HTTP errors to the client.

---

## 1. Dual-Write Pattern

### Design Decision

MySQL is the authoritative source of truth for all product data. Elasticsearch is a derived projection used for search and aggregations. This means:

1. **MySQL first:** Every write operation commits to MySQL before touching ES.
2. **ES second:** After a successful MySQL write, the same data is written to (or deleted from) ES.
3. **ES failure is non-blocking:** If the ES write fails (network timeout, node down, etc.), the MySQL change stands and the HTTP response to the client reflects success. The ES failure is logged at ERROR level for monitoring/alerting.

**Rationale:** Treating ES as a replica rather than a co-equal store avoids distributed transaction complexity. If ES falls behind, it can be re-synced from MySQL via the `/admin/reindex` endpoint (see SPEC_IMPROVEMENTS.md). The alternative — failing the whole request if ES is down — would make every write dependent on ES availability, which is unacceptable when ES is a search enhancement rather than the primary store.

### Dual-Write Sequence

```
Client → POST /products
    → INSERT INTO products (MySQL)   ← commit
    → es.index(product document)     ← best-effort
    → return 201 Created
```

---

## 2. `POST /products` — Create Product

### Request Body

```json
{
  "title": "Wireless Keyboard",
  "description": "Compact wireless keyboard with backlight.",
  "price": 49.99,
  "discount_percentage": 5.0,
  "rating": 4.3,
  "stock": 150,
  "brand": "Logitech",
  "category": "laptops",
  "tags": ["wireless", "keyboard", "accessories"],
  "thumbnail": "https://example.com/thumb.jpg",
  "images": ["https://example.com/img1.jpg"]
}
```

**Required fields:** `title`, `price`, `category`.
**Optional fields:** `description`, `discount_percentage` (default 0), `rating`, `stock` (default 0), `brand`, `tags`, `thumbnail`, `images`.

### Behaviour

1. Validate that the `category` slug exists in MySQL. If not, return **422** with message `"Category '{slug}' not found"`.
2. Insert into `products` table (MySQL auto-assigns the ID for new products created via the API).
3. Insert tag rows into `product_tags` and image rows into `product_images`.
4. Index the new product document into Elasticsearch (best-effort).
5. Return HTTP **201 Created** with the full product object (including the newly assigned `id`).

### Response (201)

```json
{
  "id": 195,
  "title": "Wireless Keyboard",
  "description": "Compact wireless keyboard with backlight.",
  "price": 49.99,
  "discount_percentage": 5.0,
  "rating": 4.3,
  "stock": 150,
  "brand": "Logitech",
  "category": "laptops",
  "tags": ["wireless", "keyboard", "accessories"],
  "thumbnail": "https://example.com/thumb.jpg",
  "images": ["https://example.com/img1.jpg"]
}
```

---

## 3. `PUT /products/{id}` — Partial Update

### Design Decision — Partial Update Semantics

`PUT` here behaves as a partial update (semantically closer to `PATCH`) rather than a full replace. This means only the fields included in the request body are updated; omitted fields retain their current values.

**Rationale:** Requiring clients to send the complete product representation on every update (true REST `PUT`) creates unnecessary risk of accidentally overwriting fields the client did not intend to change (e.g. `stock`). Given that this is an internal API, partial update semantics on `PUT` are preferred for ergonomics. A strict REST purist would use `PATCH`, but `PUT` is acceptable here since we document the partial-update behaviour explicitly.

### Request Body

Any subset of the writable product fields:

```json
{
  "price": 44.99,
  "stock": 200
}
```

### Behaviour

1. Fetch the existing product from MySQL. If not found, return **404 Not Found**.
2. Merge the provided fields into the existing product record.
3. Execute `UPDATE products SET ... WHERE id = ?` for only the changed fields.
4. Update tag and image rows if `tags` or `images` are provided (delete existing rows, re-insert new ones).
5. Re-index the updated product in Elasticsearch (best-effort, full document replace).
6. Return HTTP **200 OK** with the complete updated product object.

### Response (200)

```json
{
  "id": 195,
  "title": "Wireless Keyboard",
  "price": 44.99,
  "stock": 200,
  "...all other fields..."
}
```

### 404 Response

```json
{
  "detail": "Product with id 999 not found"
}
```

---

## 4. `DELETE /products/{id}` — Delete Product

### Behaviour

1. Fetch the existing product from MySQL. If not found, return **404 Not Found**.
2. Delete the product row from MySQL (cascade deletes `product_tags` and `product_images`).
3. Delete the document from Elasticsearch (best-effort; log if ES delete fails).
4. Return HTTP **204 No Content** with an empty body.

### 404 Response

```json
{
  "detail": "Product with id 999 not found"
}
```

**Design decision:** 204 (No Content) is the standard response for a successful DELETE. Returning 200 with a confirmation message body is also acceptable but adds no information the client cannot infer from the 204 status itself.

---

## 5. Request and Response Schemas

### `ProductCreate` (request body for POST)

| Field                | Type         | Required | Default |
|----------------------|--------------|----------|---------|
| `title`              | string       | Yes      | —       |
| `description`        | string       | No       | null    |
| `price`              | float (> 0)  | Yes      | —       |
| `discount_percentage`| float (0–100)| No       | 0.0     |
| `rating`             | float (0–5)  | No       | null    |
| `stock`              | int (>= 0)   | No       | 0       |
| `brand`              | string       | No       | null    |
| `category`           | string       | Yes      | —       |
| `tags`               | list[string] | No       | []      |
| `thumbnail`          | string (URL) | No       | null    |
| `images`             | list[string] | No       | []      |

### `ProductUpdate` (request body for PUT)

Same fields as `ProductCreate` but all are optional. At least one field must be provided (return 422 if body is empty).

### `ProductResponse` (response body for POST and PUT)

All fields from `ProductCreate` plus:

| Field       | Type      |
|-------------|-----------|
| `id`        | int       |
| `created_at`| datetime  |

---

## Acceptance Criteria

- [ ] `POST /products` with valid body returns HTTP 201 and the created product including its assigned `id`.
- [ ] `POST /products` with a non-existent `category` slug returns HTTP 422.
- [ ] `POST /products` with missing required fields (`title`, `price`, `category`) returns HTTP 422.
- [ ] After `POST /products`, the product is retrievable via `GET /products/{id}` from MySQL.
- [ ] After `POST /products`, the product is findable via `GET /products?query=<title>` from Elasticsearch.
- [ ] `PUT /products/{id}` with a partial body updates only the specified fields; unspecified fields are unchanged.
- [ ] `PUT /products/{id}` returns HTTP 200 with the complete updated product.
- [ ] `PUT /products/99999` returns HTTP 404.
- [ ] `DELETE /products/{id}` returns HTTP 204 with no body.
- [ ] After `DELETE /products/{id}`, `GET /products/{id}` returns HTTP 404.
- [ ] After `DELETE /products/{id}`, the product no longer appears in `GET /products?query=<title>` search results.
- [ ] `DELETE /products/99999` returns HTTP 404.
- [ ] If Elasticsearch is unavailable, `POST /products` still returns 201 (MySQL write succeeds; ES failure is logged).
- [ ] Cascade delete removes `product_tags` and `product_images` rows when a product is deleted.
