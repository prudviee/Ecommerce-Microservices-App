# Spec: Stock Management and Buy Endpoints

## Objective

Add two atomic stock endpoints: one for adjusting stock by a signed delta (for restocking or manual corrections), and one for purchasing a quantity of a product (decrementing stock). Both endpoints eliminate race conditions by using a single atomic SQL `UPDATE ... WHERE` statement rather than a read-check-write sequence.

---

## 1. Race Condition Background

### The Problem with Naive Read-Check-Write

A common but incorrect implementation of stock decrement looks like this:

```python
# NAIVE — HAS A RACE CONDITION
product = await db.get(Product, product_id)
if product.stock < quantity:
    raise HTTPException(409, "Insufficient stock")
product.stock -= quantity
await db.commit()
```

**The race window:** Between the `SELECT` and the `UPDATE`, another concurrent request can read the same `stock` value, pass the same check, and commit its decrement. Both requests succeed but together they decrement more stock than was available. This is a classic TOCTOU (time-of-check-time-of-use) bug.

With 10 concurrent requests each wanting to buy the last unit, all 10 read `stock=1`, all 10 pass the `>= 1` check, all 10 commit, and `stock` ends up at `-9`.

### The Atomic Solution

Move the check into the `WHERE` clause of the `UPDATE` statement:

```sql
UPDATE products
SET stock = stock + :delta
WHERE id = :product_id
  AND stock + :delta >= 0
```

MySQL evaluates the `WHERE` clause atomically at the row level using a row lock. Only one concurrent request can hold the lock and execute this update at a time. If the condition `stock + delta >= 0` is false, the UPDATE matches 0 rows — which the application interprets as a 409 conflict. No separate SELECT is needed; no race window exists.

---

## 2. `PATCH /products/{id}/stock`

### Purpose

Adjusts the stock of a product by a signed integer delta. Positive delta = restock. Negative delta = manual decrement (for corrections, reservations, etc.).

### Request Body

```json
{
  "delta": -5,
  "reason": "Damaged goods removed"
}
```

| Field    | Type   | Required | Description                                    |
|----------|--------|----------|------------------------------------------------|
| `delta`  | int    | Yes      | Signed integer. Non-zero. Positive or negative.|
| `reason` | string | No       | Human-readable note for audit logging.         |

Validation: `delta` must not be zero (return 422).

### Atomic SQL

```sql
UPDATE products
SET stock = stock + :delta
WHERE id = :product_id
  AND stock + :delta >= 0
```

If `rowcount == 0`:
- Check if the product exists at all (a second SELECT). If not → 404. If yes → 409 (delta would make stock negative).

If `rowcount == 1`: success.

**Design decision:** Use `PATCH` (not `PUT`) because this endpoint applies a partial, incremental change to a single field (`stock`). The client does not need to know the current stock value to use this endpoint — they provide a delta, not an absolute value.

### Behaviour

1. Execute the atomic UPDATE.
2. If `rowcount == 0`: run a `SELECT id FROM products WHERE id = ?` to distinguish 404 from 409.
3. If the product does not exist: return **404 Not Found**.
4. If the product exists but delta would make stock negative: return **409 Conflict**.
5. If `rowcount == 1`: fetch the updated row to return `previous_stock` and `current_stock`.

### Fetching `previous_stock`

Since MySQL does not return old values natively in an `UPDATE`, `previous_stock` is computed as `current_stock - delta` from the returned row.

### Response (200)

```json
{
  "product_id": 1,
  "previous_stock": 50,
  "delta": -5,
  "current_stock": 45,
  "reason": "Damaged goods removed"
}
```

### 409 Response

```json
{
  "detail": "Stock adjustment would result in negative stock. Current stock: 3, delta: -5"
}
```

### 404 Response

```json
{
  "detail": "Product with id 999 not found"
}
```

---

## 3. `POST /products/{id}/buy`

### Purpose

Purchases a specified quantity of a product. Decrements stock atomically. Returns purchase confirmation including remaining stock.

### Request Body

```json
{
  "quantity": 2
}
```

| Field      | Type | Required | Description                           |
|------------|------|----------|---------------------------------------|
| `quantity` | int  | Yes      | Number of units to purchase. Must be >= 1. |

Validation: `quantity` must be >= 1 (return 422 if 0 or negative).

### Atomic SQL

```sql
UPDATE products
SET stock = stock - :quantity
WHERE id = :product_id
  AND stock >= :quantity
```

Equivalent to calling `PATCH /stock` with `delta = -quantity`, but the buy endpoint has different semantics (purchase confirmation response) and enforces `quantity >= 1`.

If `rowcount == 0`:
- Same disambiguation as the stock endpoint: 404 if product missing, 409 if stock insufficient.

### Behaviour

1. Validate `quantity >= 1`.
2. Execute the atomic UPDATE.
3. If `rowcount == 0`: run existence check, return 404 or 409 accordingly.
4. If `rowcount == 1`: return purchase confirmation.

### Response (200)

```json
{
  "product_id": 1,
  "product_title": "iPhone 9",
  "quantity_purchased": 2,
  "stock_remaining": 89
}
```

### 409 Response — Insufficient Stock

```json
{
  "detail": "Insufficient stock. Requested: 5, available: 3"
}
```

### 404 Response

```json
{
  "detail": "Product with id 999 not found"
}
```

---

## 4. Why These Two Endpoints Are Separate

`PATCH /stock` and `POST /buy` are deliberately separate despite both decrementing stock:

| Concern                | `PATCH /stock`                        | `POST /buy`                          |
|------------------------|---------------------------------------|--------------------------------------|
| Delta direction        | Any (positive or negative)            | Always negative (purchase only)      |
| Minimum quantity       | Not enforced (any non-zero int)       | Must be >= 1                         |
| Reason field           | Supported (audit notes)               | Not needed                           |
| Response               | Stock adjustment details              | Purchase confirmation                |
| Caller                 | Admin / inventory system              | Customer / checkout flow             |

Merging them would require conditional logic in both the request schema and response shape, adding complexity without benefit.

---

## Acceptance Criteria

- [ ] `PATCH /products/1/stock` with `{"delta": 10}` increases stock by 10 and returns `previous_stock`, `delta`, `current_stock`.
- [ ] `PATCH /products/1/stock` with `{"delta": -5}` when stock >= 5 decreases stock by 5.
- [ ] `PATCH /products/1/stock` with `{"delta": -999}` when stock is 3 returns HTTP 409 with a message indicating the shortfall.
- [ ] `PATCH /products/99999/stock` returns HTTP 404.
- [ ] `PATCH /products/1/stock` with `{"delta": 0}` returns HTTP 422.
- [ ] `POST /products/1/buy` with `{"quantity": 2}` decreases stock by 2 and returns `product_title`, `quantity_purchased`, `stock_remaining`.
- [ ] `POST /products/1/buy` when stock is 1 and `quantity` is 5 returns HTTP 409 with available stock in the message.
- [ ] `POST /products/99999/buy` returns HTTP 404.
- [ ] `POST /products/1/buy` with `{"quantity": 0}` returns HTTP 422.
- [ ] `POST /products/1/buy` with `{"quantity": -1}` returns HTTP 422.
- [ ] Concurrent requests: 10 simultaneous `POST /products/1/buy` with `quantity=1` when `stock=1` result in exactly 1 success (200) and 9 failures (409). No negative stock.
- [ ] Concurrent requests: 5 simultaneous `PATCH /products/1/stock` with `delta=-3` when `stock=10` result in 3 successes and 2 failures (409). Stock ends at 1, not negative.
