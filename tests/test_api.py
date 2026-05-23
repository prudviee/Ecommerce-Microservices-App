"""
Integration tests for the E-Commerce API.

Requires the full stack to be running:
    docker compose up -d

Run with:
    pip install pytest httpx
    pytest tests/test_api.py -v
"""

import time

import httpx
import pytest

BASE_URL = "http://localhost:8000"

# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=15.0) as c:
        yield c


@pytest.fixture(scope="session")
def created_ids():
    """Tracks product IDs created during tests so they can be deleted afterwards."""
    ids = []
    yield ids
    # Session teardown — clean up all products created during tests
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        for pid in ids:
            c.delete(f"/products/{pid}")


@pytest.fixture
def new_product(client, created_ids):
    """Creates a single product and registers it for cleanup."""
    resp = client.post("/products", json={
        "title": "Test Product",
        "price": 49.99,
        "category": "beauty",
        "brand": "TestBrand",
        "stock": 10,
        "tags": ["test"],
    })
    assert resp.status_code == 201
    product = resp.json()
    created_ids.append(product["id"])
    return product


# ─── Health ───────────────────────────────────────────────────────────────────

class TestHealth:
    def test_health_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"

    def test_health_has_all_dependencies(self, client):
        deps = client.get("/health").json()["dependencies"]
        assert "mysql" in deps
        assert "elasticsearch" in deps
        assert "redis" in deps

    def test_health_all_deps_ok(self, client):
        deps = client.get("/health").json()["dependencies"]
        for name, info in deps.items():
            assert info["status"] == "ok", f"{name} reported unhealthy"
            assert info["latency_ms"] is not None

    def test_health_response_headers(self, client):
        resp = client.get("/health")
        assert "x-request-id" in resp.headers
        assert "x-response-time" in resp.headers

    def test_x_request_id_echoed(self, client):
        resp = client.get("/health", headers={"X-Request-ID": "test-trace-abc"})
        assert resp.headers["x-request-id"] == "test-trace-abc"


# ─── Categories ───────────────────────────────────────────────────────────────

class TestCategories:
    def test_returns_categories(self, client):
        resp = client.get("/categories")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 24
        assert len(body["data"]) == 24

    def test_category_shape(self, client):
        category = client.get("/categories").json()["data"][0]
        assert "id" in category
        assert "name" in category

    def test_second_call_is_cached(self, client):
        client.get("/categories")  # warm cache
        resp = client.get("/categories")
        assert resp.json().get("cached") is True

    def test_categories_sorted_alphabetically(self, client):
        names = [c["name"] for c in client.get("/categories").json()["data"]]
        assert names == sorted(names)


# ─── Products — List & Filters ────────────────────────────────────────────────

class TestProductsList:
    def test_list_returns_products(self, client):
        resp = client.get("/products")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 194
        assert len(body["data"]) > 0

    def test_pagination_fields_present(self, client):
        body = client.get("/products?page=1&limit=10").json()
        assert "total" in body
        assert "page" in body
        assert "limit" in body
        assert "pages" in body

    def test_pagination_limit_respected(self, client):
        body = client.get("/products?page=1&limit=5").json()
        assert len(body["data"]) == 5

    def test_pagination_page_2_differs_from_page_1(self, client):
        ids_p1 = {p["id"] for p in client.get("/products?page=1&limit=10").json()["data"]}
        ids_p2 = {p["id"] for p in client.get("/products?page=2&limit=10").json()["data"]}
        assert ids_p1.isdisjoint(ids_p2)

    def test_filter_by_category(self, client):
        resp = client.get("/products?category=smartphones")
        assert resp.status_code == 200
        products = resp.json()["data"]
        assert len(products) > 0
        assert all(p["category"] == "smartphones" for p in products)

    def test_filter_unknown_category_returns_empty(self, client):
        body = client.get("/products?category=doesnotexist999").json()
        assert body["total"] == 0
        assert body["data"] == []

    def test_min_price_filter(self, client):
        products = client.get("/products?min_price=1000&limit=50").json()["data"]
        assert all(p["price"] >= 1000 for p in products)

    def test_max_price_filter(self, client):
        products = client.get("/products?max_price=10&limit=50").json()["data"]
        assert all(p["price"] <= 10 for p in products)

    def test_price_range_filter(self, client):
        products = client.get("/products?min_price=50&max_price=100&limit=50").json()["data"]
        assert all(50 <= p["price"] <= 100 for p in products)

    def test_sort_price_asc(self, client):
        products = client.get("/products?sort=price_asc&limit=20").json()["data"]
        prices = [p["price"] for p in products]
        assert prices == sorted(prices)

    def test_sort_price_desc(self, client):
        products = client.get("/products?sort=price_desc&limit=20").json()["data"]
        prices = [p["price"] for p in products]
        assert prices == sorted(prices, reverse=True)

    def test_sort_rating_desc(self, client):
        products = client.get("/products?sort=rating_desc&limit=20").json()["data"]
        ratings = [p["rating"] for p in products if p["rating"] is not None]
        assert ratings == sorted(ratings, reverse=True)

    def test_invalid_sort_returns_422(self, client):
        resp = client.get("/products?sort=invalid_sort")
        assert resp.status_code == 422

    def test_min_price_greater_than_max_price_returns_422(self, client):
        resp = client.get("/products?min_price=500&max_price=100")
        assert resp.status_code == 422

    def test_product_shape(self, client):
        product = client.get("/products?limit=1").json()["data"][0]
        for field in ["id", "title", "price", "category", "tags", "images"]:
            assert field in product


# ─── Products — Single & Similar ─────────────────────────────────────────────

class TestProductDetail:
    def test_get_by_id(self, client):
        resp = client.get("/products/1")
        assert resp.status_code == 200
        assert resp.json()["id"] == 1

    def test_get_nonexistent_returns_404(self, client):
        resp = client.get("/products/999999")
        assert resp.status_code == 404

    def test_similar_products(self, client):
        resp = client.get("/products/1/similar")
        assert resp.status_code == 200
        body = resp.json()
        assert body["product_id"] == 1
        assert isinstance(body["similar"], list)

    def test_similar_excludes_self(self, client):
        similar_ids = [p["id"] for p in client.get("/products/1/similar").json()["similar"]]
        assert 1 not in similar_ids

    def test_similar_nonexistent_returns_404(self, client):
        resp = client.get("/products/999999/similar")
        assert resp.status_code == 404


# ─── Products — Stats, Top Rated, On Sale ────────────────────────────────────

class TestProductsAggregations:
    def test_stats_shape(self, client):
        body = client.get("/products/stats").json()
        for field in ["total_products", "avg_price", "avg_rating", "top_categories", "top_brands"]:
            assert field in body

    def test_stats_cached_on_second_call(self, client):
        client.get("/products/stats")  # warm
        assert client.get("/products/stats").json().get("cached") is True

    def test_top_rated_default_threshold(self, client):
        products = client.get("/products/top-rated").json()["data"]
        assert all(p["rating"] >= 4.5 for p in products)

    def test_top_rated_custom_threshold(self, client):
        products = client.get("/products/top-rated?min_rating=4.9").json()["data"]
        assert all(p["rating"] >= 4.9 for p in products)

    def test_top_rated_response_includes_min_rating(self, client):
        body = client.get("/products/top-rated?min_rating=4.7").json()
        assert body["min_rating"] == 4.7

    def test_on_sale_default_threshold(self, client):
        products = client.get("/products/on-sale").json()["data"]
        assert all(p["discount_percentage"] >= 10.0 for p in products)

    def test_on_sale_custom_threshold(self, client):
        body = client.get("/products/on-sale?min_discount=15").json()
        assert body["min_discount"] == 15.0


# ─── Products — Search & Suggestions ─────────────────────────────────────────

class TestSearch:
    def test_search_returns_results(self, client):
        resp = client.get("/products?query=phone")
        assert resp.status_code == 200
        assert resp.json()["total"] > 0

    def test_search_returns_facets(self, client):
        body = client.get("/products?query=apple").json()
        assert "facets" in body
        assert "categories" in body["facets"]
        assert "brands" in body["facets"]

    def test_search_fuzzy_typo(self, client):
        # "iphon" should still find iPhone products
        body = client.get("/products?query=iphon").json()
        assert body["total"] > 0

    def test_search_with_price_filter(self, client):
        products = client.get("/products?query=laptop&min_price=500").json()["data"]
        assert all(p["price"] >= 500 for p in products)

    def test_suggestions_returns_list(self, client):
        resp = client.get("/products/suggestions?query=mac")
        assert resp.status_code == 200
        assert isinstance(resp.json()["suggestions"], list)

    def test_suggestions_shape(self, client):
        suggestions = client.get("/products/suggestions?query=apple").json()["suggestions"]
        if suggestions:
            for s in suggestions:
                assert "id" in s
                assert "title" in s
                assert "category" in s

    def test_global_search_returns_products_and_categories(self, client):
        body = client.get("/search?query=phone").json()
        assert "products" in body
        assert "categories" in body
        assert "totals" in body
        assert "query" in body

    def test_global_search_totals(self, client):
        body = client.get("/search?query=phone").json()
        # totals reflect full ES count; products list is capped by limit
        assert body["totals"]["products"] >= len(body["products"])
        assert body["totals"]["categories"] == len(body["categories"])


# ─── Products — CRUD ─────────────────────────────────────────────────────────

class TestProductsCRUD:
    def test_create_product_returns_201(self, client, created_ids):
        resp = client.post("/products", json={
            "title": "CRUD Test Product",
            "price": 29.99,
            "category": "beauty",
        })
        assert resp.status_code == 201
        pid = resp.json()["id"]
        created_ids.append(pid)

    def test_create_product_shape(self, client, created_ids):
        resp = client.post("/products", json={
            "title": "Shape Test",
            "price": 9.99,
            "category": "beauty",
            "brand": "Acme",
            "stock": 5,
            "tags": ["test", "shape"],
        })
        assert resp.status_code == 201
        body = resp.json()
        created_ids.append(body["id"])
        assert body["title"] == "Shape Test"
        assert body["price"] == 9.99
        assert body["category"] == "beauty"
        assert body["brand"] == "Acme"
        assert body["tags"] == ["test", "shape"]

    def test_create_invalid_category_returns_404(self, client):
        resp = client.post("/products", json={
            "title": "Bad Category",
            "price": 9.99,
            "category": "doesnotexist",
        })
        assert resp.status_code == 404

    def test_create_missing_required_fields_returns_422(self, client):
        resp = client.post("/products", json={"title": "No price or category"})
        assert resp.status_code == 422

    def test_update_product(self, client, new_product):
        pid = new_product["id"]
        resp = client.put(f"/products/{pid}", json={"price": 99.99, "stock": 999})
        assert resp.status_code == 200
        body = resp.json()
        assert body["price"] == 99.99
        assert body["stock"] == 999
        assert body["title"] == new_product["title"]  # unchanged

    def test_update_nonexistent_returns_404(self, client):
        resp = client.put("/products/999999", json={"price": 1.0})
        assert resp.status_code == 404

    def test_delete_product(self, client, created_ids):
        resp = client.post("/products", json={"title": "To Delete", "price": 1.0, "category": "beauty"})
        pid = resp.json()["id"]
        del_resp = client.delete(f"/products/{pid}")
        assert del_resp.status_code == 200
        assert str(pid) in del_resp.json()["message"]
        # Confirm it's gone
        assert client.get(f"/products/{pid}").status_code == 404

    def test_delete_nonexistent_returns_404(self, client):
        assert client.delete("/products/999999").status_code == 404

    def test_full_crud_flow(self, client, created_ids):
        # Create
        create_resp = client.post("/products", json={
            "title": "Full Flow Product",
            "price": 50.0,
            "category": "laptops",
            "stock": 20,
        })
        assert create_resp.status_code == 201
        pid = create_resp.json()["id"]
        created_ids.append(pid)

        # Read
        get_resp = client.get(f"/products/{pid}")
        assert get_resp.status_code == 200
        assert get_resp.json()["title"] == "Full Flow Product"

        # Update
        put_resp = client.put(f"/products/{pid}", json={"price": 75.0})
        assert put_resp.status_code == 200
        assert put_resp.json()["price"] == 75.0

        # Delete
        del_resp = client.delete(f"/products/{pid}")
        assert del_resp.status_code == 200
        created_ids.remove(pid)

        # Confirm deleted
        assert client.get(f"/products/{pid}").status_code == 404


# ─── Products — Bulk Create ───────────────────────────────────────────────────

class TestBulkCreate:
    def test_bulk_create_success(self, client, created_ids):
        resp = client.post("/products/bulk", json={"products": [
            {"title": "Bulk One", "price": 10.0, "category": "beauty"},
            {"title": "Bulk Two", "price": 20.0, "category": "laptops"},
            {"title": "Bulk Three", "price": 30.0, "category": "smartphones"},
        ]})
        assert resp.status_code == 201
        body = resp.json()
        assert body["created"] == 3
        assert len(body["ids"]) == 3
        assert "duration_ms" in body
        created_ids.extend(body["ids"])

    def test_bulk_create_invalid_category_rejected(self, client):
        resp = client.post("/products/bulk", json={"products": [
            {"title": "Good Product", "price": 10.0, "category": "beauty"},
            {"title": "Bad Category", "price": 10.0, "category": "doesnotexist"},
        ]})
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "errors" in detail
        assert detail["errors"][0]["index"] == 1

    def test_bulk_create_empty_list_rejected(self, client):
        resp = client.post("/products/bulk", json={"products": []})
        assert resp.status_code == 422

    def test_bulk_create_over_limit_rejected(self, client):
        products = [{"title": f"P{i}", "price": 1.0, "category": "beauty"} for i in range(51)]
        resp = client.post("/products/bulk", json={"products": products})
        assert resp.status_code == 422


# ─── Stats Cache Invalidation ────────────────────────────────────────────────

class TestCacheInvalidation:
    def test_stats_invalidated_after_create(self, client, created_ids):
        # Warm the cache
        client.get("/products/stats")
        assert client.get("/products/stats").json().get("cached") is True

        # Create a product — should bust the cache
        resp = client.post("/products", json={"title": "Cache Buster", "price": 1.0, "category": "beauty"})
        assert resp.status_code == 201
        created_ids.append(resp.json()["id"])

        # Next stats call should be a fresh hit
        assert client.get("/products/stats").json().get("cached") is not True

    def test_stats_invalidated_after_delete(self, client, created_ids):
        # Create a product to delete
        pid = client.post("/products", json={"title": "Del Cache", "price": 1.0, "category": "beauty"}).json()["id"]

        # Warm the cache
        client.get("/products/stats")
        client.get("/products/stats")  # confirm cached

        # Delete busts the cache
        client.delete(f"/products/{pid}")
        assert client.get("/products/stats").json().get("cached") is not True


# ─── Admin — Reindex ──────────────────────────────────────────────────────────

class TestAdmin:
    def test_reindex_success(self, client):
        resp = client.post("/admin/reindex")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["failed"] == 0
        assert body["indexed"] >= 194
        assert "duration_ms" in body

    def test_reindex_after_manual_create(self, client, created_ids):
        # Create a product
        pid = client.post("/products", json={
            "title": "Reindex Test",
            "price": 5.0,
            "category": "beauty",
        }).json()["id"]
        created_ids.append(pid)

        # Reindex should still report ok
        resp = client.post("/admin/reindex")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ─── Stock Management & Buy ───────────────────────────────────────────────────

class TestStockManagement:
    def test_restock(self, client, new_product):
        pid = new_product["id"]
        resp = client.patch(f"/products/{pid}/stock", json={"delta": 10, "reason": "restock"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["delta"] == 10
        assert body["current_stock"] == body["previous_stock"] + 10
        assert body["reason"] == "restock"
        assert body["product_id"] == pid

    def test_stock_writeoff(self, client, new_product):
        pid = new_product["id"]
        # First restock so we have enough to write off
        client.patch(f"/products/{pid}/stock", json={"delta": 20})
        resp = client.patch(f"/products/{pid}/stock", json={"delta": -5, "reason": "damaged"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["delta"] == -5
        assert body["current_stock"] == body["previous_stock"] - 5

    def test_stock_adjust_below_zero_returns_409(self, client, new_product):
        pid = new_product["id"]
        resp = client.patch(f"/products/{pid}/stock", json={"delta": -99999})
        assert resp.status_code == 409
        assert "Insufficient stock" in resp.json()["detail"]["message"]

    def test_stock_adjust_zero_delta_returns_422(self, client, new_product):
        pid = new_product["id"]
        resp = client.patch(f"/products/{pid}/stock", json={"delta": 0})
        assert resp.status_code == 422

    def test_stock_adjust_nonexistent_returns_404(self, client):
        resp = client.patch("/products/999999/stock", json={"delta": 10})
        assert resp.status_code == 404

    def test_buy_success(self, client, new_product):
        pid = new_product["id"]
        # Ensure enough stock
        client.patch(f"/products/{pid}/stock", json={"delta": 20})
        stock_before = client.get(f"/products/{pid}").json()["stock"]

        resp = client.post(f"/products/{pid}/buy", json={"quantity": 3})
        assert resp.status_code == 200
        body = resp.json()
        assert body["quantity_purchased"] == 3
        assert body["stock_remaining"] == stock_before - 3
        assert body["product_id"] == pid
        assert "product_title" in body

    def test_buy_decrements_stock_in_db(self, client, new_product):
        pid = new_product["id"]
        client.patch(f"/products/{pid}/stock", json={"delta": 10})
        stock_before = client.get(f"/products/{pid}").json()["stock"]

        client.post(f"/products/{pid}/buy", json={"quantity": 2})

        stock_after = client.get(f"/products/{pid}").json()["stock"]
        assert stock_after == stock_before - 2

    def test_buy_insufficient_stock_returns_409(self, client, new_product):
        pid = new_product["id"]
        current = client.get(f"/products/{pid}").json()["stock"] or 0
        resp = client.post(f"/products/{pid}/buy", json={"quantity": current + 100})
        assert resp.status_code == 409
        assert "Insufficient stock" in resp.json()["detail"]["message"]

    def test_buy_nonexistent_returns_404(self, client):
        resp = client.post("/products/999999/buy", json={"quantity": 1})
        assert resp.status_code == 404

    def test_buy_quantity_zero_rejected(self, client, new_product):
        resp = client.post(f"/products/{new_product['id']}/buy", json={"quantity": 0})
        assert resp.status_code == 422

    def test_buy_drains_stock_to_zero(self, client, created_ids):
        # Create a product with exactly 3 stock
        pid = client.post("/products", json={
            "title": "Low Stock Item",
            "price": 9.99,
            "category": "beauty",
            "stock": 3,
        }).json()["id"]
        created_ids.append(pid)

        resp = client.post(f"/products/{pid}/buy", json={"quantity": 3})
        assert resp.status_code == 200
        assert resp.json()["stock_remaining"] == 0

        # Now out of stock
        resp2 = client.post(f"/products/{pid}/buy", json={"quantity": 1})
        assert resp2.status_code == 409


# ─── Prometheus Metrics ───────────────────────────────────────────────────────

class TestMetrics:
    def test_metrics_endpoint_exists(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200

    def test_metrics_prometheus_format(self, client):
        text = client.get("/metrics").text
        assert "http_requests_total" in text
        assert "http_request_duration_seconds" in text

    def test_metrics_increments_on_request(self, client):
        client.get("/health")
        text = client.get("/metrics").text
        assert 'handler="/health"' in text


# ─── Response Headers ────────────────────────────────────────────────────────

class TestResponseHeaders:
    def test_x_request_id_present_on_all_responses(self, client):
        for path in ["/health", "/categories", "/products?limit=1", "/products/stats"]:
            resp = client.get(path)
            assert "x-request-id" in resp.headers, f"Missing X-Request-ID on {path}"

    def test_x_response_time_present_on_all_responses(self, client):
        for path in ["/health", "/categories", "/products?limit=1"]:
            resp = client.get(path)
            assert "x-response-time" in resp.headers, f"Missing X-Response-Time on {path}"
            assert resp.headers["x-response-time"].endswith("ms")

    def test_x_request_id_is_unique_per_request(self, client):
        ids = {client.get("/health").headers["x-request-id"] for _ in range(5)}
        assert len(ids) == 5  # all 5 should be unique UUIDs

    def test_client_supplied_request_id_echoed(self, client):
        resp = client.get("/health", headers={"X-Request-ID": "my-custom-id-xyz"})
        assert resp.headers["x-request-id"] == "my-custom-id-xyz"
