from typing import Dict, List, Optional, Tuple

from elasticsearch import Elasticsearch

from app.schemas.product import ProductSchema

INDEX_NAME = "products"

ES_SORT_MAP = {
    "price_asc":     {"price": {"order": "asc"}},
    "price_desc":    {"price": {"order": "desc"}},
    "rating_asc":    {"rating": {"order": "asc"}},
    "rating_desc":   {"rating": {"order": "desc"}},
    "discount_desc": {"discount_percentage": {"order": "desc"}},
}


def search_products(
    es: Elasticsearch,
    query: str,
    page: int = 1,
    limit: int = 20,
    sort: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
) -> Tuple[List[ProductSchema], int, Dict]:
    filters = []
    if min_price is not None or max_price is not None:
        price_range: dict = {}
        if min_price is not None:
            price_range["gte"] = min_price
        if max_price is not None:
            price_range["lte"] = max_price
        filters.append({"range": {"price": price_range}})

    if filters:
        es_query = {
            "bool": {
                "must": {
                    "multi_match": {
                        "query": query,
                        "fields": ["title^2", "description"],
                        "fuzziness": "AUTO",
                    }
                },
                "filter": filters,
            }
        }
    else:
        es_query = {
            "multi_match": {
                "query": query,
                "fields": ["title^2", "description"],
                "fuzziness": "AUTO",
            }
        }

    body: dict = {
        "query": es_query,
        "from": (page - 1) * limit,
        "size": limit,
        "aggs": {
            "by_category": {"terms": {"field": "category", "size": 10}},
            "by_brand":    {"terms": {"field": "brand",    "size": 10}},
        },
    }

    if sort and sort in ES_SORT_MAP:
        body["sort"] = [ES_SORT_MAP[sort]]

    response = es.search(index=INDEX_NAME, body=body)
    total = response["hits"]["total"]["value"]

    products = []
    for hit in response["hits"]["hits"]:
        src = hit["_source"]
        products.append(
            ProductSchema(
                id=src["id"],
                title=src["title"],
                description=src.get("description"),
                price=src["price"],
                discount_percentage=src.get("discount_percentage"),
                rating=src.get("rating"),
                stock=src.get("stock"),
                brand=src.get("brand"),
                sku=src.get("sku"),
                category=src.get("category"),
                tags=src.get("tags", []),
                thumbnail=src.get("thumbnail"),
                images=src.get("images", []),
            )
        )

    aggs = response.get("aggregations", {})
    facets = {
        "categories": [
            {"name": b["key"], "count": b["doc_count"]}
            for b in aggs.get("by_category", {}).get("buckets", [])
        ],
        "brands": [
            {"name": b["key"], "count": b["doc_count"]}
            for b in aggs.get("by_brand", {}).get("buckets", [])
            if b["key"]
        ],
    }

    return products, total, facets


def suggest_products(es: Elasticsearch, query: str, size: int = 6) -> list:
    body = {
        "query": {
            "match_phrase_prefix": {
                "title": {"query": query, "max_expansions": 10}
            }
        },
        "_source": ["id", "title", "category", "thumbnail"],
        "size": size,
    }
    response = es.search(index=INDEX_NAME, body=body)
    return [
        {
            "id": h["_source"]["id"],
            "title": h["_source"]["title"],
            "category": h["_source"].get("category"),
            "thumbnail": h["_source"].get("thumbnail"),
        }
        for h in response["hits"]["hits"]
    ]


def similar_products(es: Elasticsearch, product_id: int, size: int = 6) -> list:
    body = {
        "query": {
            "more_like_this": {
                "fields": ["title", "description"],
                "like": [{"_index": INDEX_NAME, "_id": str(product_id)}],
                "min_term_freq": 1,
                "min_doc_freq": 1,
                "max_query_terms": 25,
                "minimum_should_match": "20%",
            }
        },
        "_source": ["id", "title", "price", "rating", "category", "thumbnail"],
        "size": size,
    }
    response = es.search(index=INDEX_NAME, body=body)
    return [
        {
            "id":        h["_source"]["id"],
            "title":     h["_source"]["title"],
            "price":     h["_source"]["price"],
            "rating":    h["_source"].get("rating"),
            "category":  h["_source"].get("category"),
            "thumbnail": h["_source"].get("thumbnail"),
        }
        for h in response["hits"]["hits"]
    ]


def global_search(es: Elasticsearch, query: str, limit: int = 5) -> list:
    body = {
        "query": {
            "multi_match": {
                "query": query,
                "fields": ["title^2", "description"],
                "fuzziness": "AUTO",
            }
        },
        "_source": ["id", "title", "price", "rating", "category", "thumbnail"],
        "size": limit,
    }
    response = es.search(index=INDEX_NAME, body=body)
    total = response["hits"]["total"]["value"]
    products = [
        {
            "id":        h["_source"]["id"],
            "title":     h["_source"]["title"],
            "price":     h["_source"]["price"],
            "rating":    h["_source"].get("rating"),
            "category":  h["_source"].get("category"),
            "thumbnail": h["_source"].get("thumbnail"),
        }
        for h in response["hits"]["hits"]
    ]
    return products, total


def get_stats(es: Elasticsearch) -> dict:
    body = {
        "size": 0,
        "aggs": {
            "avg_price":    {"avg":   {"field": "price"}},
            "avg_rating":   {"avg":   {"field": "rating"}},
            "avg_discount": {"avg":   {"field": "discount_percentage"}},
            "total":        {"value_count": {"field": "id"}},
            "price_ranges": {
                "range": {
                    "field": "price",
                    "ranges": [
                        {"key": "under_50",    "to": 50},
                        {"key": "50_to_200",   "from": 50,  "to": 200},
                        {"key": "200_to_1000", "from": 200, "to": 1000},
                        {"key": "above_1000",  "from": 1000},
                    ],
                }
            },
            "top_categories": {"terms": {"field": "category", "size": 5}},
            "top_brands":     {"terms": {"field": "brand",    "size": 5}},
        },
    }

    aggs = es.search(index=INDEX_NAME, body=body)["aggregations"]

    def _round(val):
        return round(val, 2) if val is not None else None

    return {
        "total_products":  aggs["total"]["value"],
        "avg_price":       _round(aggs["avg_price"]["value"]),
        "avg_rating":      _round(aggs["avg_rating"]["value"]),
        "avg_discount":    _round(aggs["avg_discount"]["value"]),
        "price_ranges":    {b["key"]: b["doc_count"] for b in aggs["price_ranges"]["buckets"]},
        "top_categories":  [{"name": b["key"], "count": b["doc_count"]} for b in aggs["top_categories"]["buckets"]],
        "top_brands":      [{"name": b["key"], "count": b["doc_count"]} for b in aggs["top_brands"]["buckets"] if b["key"]],
    }
