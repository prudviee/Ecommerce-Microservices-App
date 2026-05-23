import json
import logging
import math
import time
from typing import Optional

import redis
from elasticsearch import Elasticsearch, helpers
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.cache import get_redis
from app.database import get_db
from app.elastic import get_es
from app.limiter import limiter
from app.schemas.product import BuySchema, ProductBulkCreateSchema, ProductCreateSchema, ProductUpdateSchema, StockAdjustSchema
from app.services import product_service, search_service

STATS_CACHE_KEY = "products:stats"
STATS_CACHE_TTL = 300  # 5 minutes

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/products", tags=["Products"])

VALID_SORT_VALUES = ["price_asc", "price_desc", "rating_asc", "rating_desc", "discount_desc"]


@router.get("")
def list_products(
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    category: Optional[str] = Query(None, description="Filter by category name"),
    query: Optional[str] = Query(None, description="Full-text search using Elasticsearch"),
    sort: Optional[str] = Query(None, description="Sort: price_asc, price_desc, rating_asc, rating_desc, discount_desc"),
    min_price: Optional[float] = Query(None, ge=0, description="Minimum price (inclusive)"),
    max_price: Optional[float] = Query(None, ge=0, description="Maximum price (inclusive)"),
    db: Session = Depends(get_db),
    es: Elasticsearch = Depends(get_es),
):
    if sort and sort not in VALID_SORT_VALUES:
        raise HTTPException(
            status_code=422,
            detail={"code": 422, "message": f"Invalid sort value. Allowed: {', '.join(VALID_SORT_VALUES)}"},
        )
    if min_price is not None and max_price is not None and min_price > max_price:
        raise HTTPException(
            status_code=422,
            detail={"code": 422, "message": "min_price cannot be greater than max_price"},
        )

    facets = None
    if query:
        products, total, facets = search_service.search_products(
            es, query, page, limit, sort=sort, min_price=min_price, max_price=max_price
        )
    else:
        products, total = product_service.get_products(
            db, page, limit, category=category, sort=sort, min_price=min_price, max_price=max_price
        )

    response = {
        "data": products,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": math.ceil(total / limit) if total > 0 else 0,
    }
    if facets is not None:
        response["facets"] = facets

    return response


@router.get("/stats")
def product_stats(es: Elasticsearch = Depends(get_es), cache: redis.Redis = Depends(get_redis)):
    try:
        cached = cache.get(STATS_CACHE_KEY)
        if cached:
            return {**json.loads(cached), "cached": True}
    except Exception as e:
        logger.warning(f"Redis read failed, falling through: {e}")

    stats = search_service.get_stats(es)

    try:
        cache.set(STATS_CACHE_KEY, json.dumps(stats), ex=STATS_CACHE_TTL)
    except Exception as e:
        logger.warning(f"Redis write failed: {e}")

    return stats


@router.get("/top-rated")
def top_rated(
    min_rating: float = Query(4.5, ge=0.0, le=5.0, description="Minimum rating threshold"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    products, total = product_service.get_top_rated(db, min_rating, page, limit)
    return {
        "data": products,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": math.ceil(total / limit) if total > 0 else 0,
        "min_rating": min_rating,
    }


@router.get("/on-sale")
def on_sale(
    min_discount: float = Query(10.0, ge=0.0, le=100.0, description="Minimum discount % threshold"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    products, total = product_service.get_on_sale(db, min_discount, page, limit)
    return {
        "data": products,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": math.ceil(total / limit) if total > 0 else 0,
        "min_discount": min_discount,
    }


@router.get("/suggestions")
def suggestions(
    query: str = Query(..., min_length=1, description="Partial search term"),
    es: Elasticsearch = Depends(get_es),
):
    return {"suggestions": search_service.suggest_products(es, query)}


def _invalidate_stats_cache(cache: redis.Redis):
    try:
        cache.delete(STATS_CACHE_KEY)
    except Exception as e:
        logger.warning(f"Redis stats invalidation failed: {e}")


@router.post("/bulk", status_code=201)
@limiter.limit("10/minute")
def bulk_create_products(
    request: Request,
    body: ProductBulkCreateSchema,
    db: Session = Depends(get_db),
    es: Elasticsearch = Depends(get_es),
    cache: redis.Redis = Depends(get_redis),
):
    start = time.monotonic()
    products, errors = product_service.bulk_create_products(db, body)
    if errors:
        raise HTTPException(
            status_code=422,
            detail={
                "code": 422,
                "message": f"Validation failed for {len(errors)} product(s)",
                "errors": errors,
            },
        )

    actions = [
        {
            "_index": "products",
            "_id": str(p.id),
            "_source": {
                "id": p.id, "title": p.title, "description": p.description,
                "price": p.price, "discount_percentage": p.discount_percentage,
                "rating": p.rating, "stock": p.stock, "brand": p.brand,
                "sku": p.sku, "category": p.category, "tags": p.tags,
                "thumbnail": p.thumbnail, "images": p.images,
            },
        }
        for p in products
    ]
    try:
        helpers.bulk(es, actions, raise_on_error=False, stats_only=True)
    except Exception as e:
        logger.error(f"ES bulk index failed for bulk create: {e}")

    _invalidate_stats_cache(cache)
    duration_ms = round((time.monotonic() - start) * 1000)
    return JSONResponse(
        content={
            "created": len(products),
            "ids": [p.id for p in products],
            "duration_ms": duration_ms,
        },
        status_code=201,
    )


@router.post("", status_code=201)
@limiter.limit("30/minute")
def create_product(
    request: Request,
    body: ProductCreateSchema,
    db: Session = Depends(get_db),
    es: Elasticsearch = Depends(get_es),
    cache: redis.Redis = Depends(get_redis),
):
    product, error = product_service.create_product(db, body)
    if error:
        raise HTTPException(status_code=404, detail={"code": 404, "message": error})

    # dual-write to ES — failure doesn't fail the request
    try:
        es.index(index="products", id=product.id, body={
            "id": product.id, "title": product.title, "description": product.description,
            "price": product.price, "discount_percentage": product.discount_percentage,
            "rating": product.rating, "stock": product.stock, "brand": product.brand,
            "sku": product.sku, "category": product.category, "tags": product.tags,
            "thumbnail": product.thumbnail, "images": product.images,
        })
    except Exception as e:
        logger.error(f"ES index failed for product {product.id}: {e}")

    _invalidate_stats_cache(cache)
    return JSONResponse(content=product.model_dump(), status_code=201)


@router.put("/{product_id}")
@limiter.limit("30/minute")
def update_product(
    request: Request,
    product_id: int,
    body: ProductUpdateSchema,
    db: Session = Depends(get_db),
    es: Elasticsearch = Depends(get_es),
    cache: redis.Redis = Depends(get_redis),
):
    product, error = product_service.update_product(db, product_id, body)
    if error == "Product not found":
        raise HTTPException(status_code=404, detail={"code": 404, "message": error})
    if error:
        raise HTTPException(status_code=404, detail={"code": 404, "message": error})

    # dual-write to ES — full replace
    try:
        es.index(index="products", id=product.id, body={
            "id": product.id, "title": product.title, "description": product.description,
            "price": product.price, "discount_percentage": product.discount_percentage,
            "rating": product.rating, "stock": product.stock, "brand": product.brand,
            "sku": product.sku, "category": product.category, "tags": product.tags,
            "thumbnail": product.thumbnail, "images": product.images,
        })
    except Exception as e:
        logger.error(f"ES update failed for product {product.id}: {e}")

    _invalidate_stats_cache(cache)
    return product


@router.delete("/{product_id}")
@limiter.limit("30/minute")
def delete_product(
    request: Request,
    product_id: int,
    db: Session = Depends(get_db),
    es: Elasticsearch = Depends(get_es),
    cache: redis.Redis = Depends(get_redis),
):
    deleted = product_service.delete_product(db, product_id)
    if not deleted:
        raise HTTPException(status_code=404, detail={"code": 404, "message": "Product not found"})

    # dual-delete from ES
    try:
        es.delete(index="products", id=str(product_id))
    except Exception as e:
        logger.error(f"ES delete failed for product {product_id}: {e}")

    _invalidate_stats_cache(cache)
    return {"message": f"Product {product_id} deleted successfully"}


@router.patch("/{product_id}/stock")
def adjust_stock(
    product_id: int,
    body: StockAdjustSchema,
    db: Session = Depends(get_db),
    cache: redis.Redis = Depends(get_redis),
):
    if body.delta == 0:
        raise HTTPException(status_code=422, detail={"code": 422, "message": "delta cannot be 0"})

    result, error, available = product_service.adjust_stock(db, product_id, body)

    if error == "not_found":
        raise HTTPException(status_code=404, detail={"code": 404, "message": "Product not found"})
    if error == "insufficient":
        raise HTTPException(
            status_code=409,
            detail={"code": 409, "message": f"Insufficient stock. Available: {available}, requested reduction: {abs(body.delta)}"},
        )

    _invalidate_stats_cache(cache)
    return result


@router.post("/{product_id}/buy")
def buy_product(
    product_id: int,
    body: BuySchema,
    db: Session = Depends(get_db),
):
    result, error, available = product_service.buy_product(db, product_id, body)

    if error == "not_found":
        raise HTTPException(status_code=404, detail={"code": 404, "message": "Product not found"})
    if error == "insufficient":
        raise HTTPException(
            status_code=409,
            detail={"code": 409, "message": f"Insufficient stock. Available: {available}, requested: {body.quantity}"},
        )

    return result


@router.get("/{product_id}")
def get_product(product_id: int, db: Session = Depends(get_db)):
    product = product_service.get_product_by_id(db, product_id)
    if not product:
        raise HTTPException(status_code=404, detail={"code": 404, "message": "Product not found"})
    return product


@router.get("/{product_id}/similar")
def similar_products(product_id: int, db: Session = Depends(get_db), es: Elasticsearch = Depends(get_es)):
    product = product_service.get_product_by_id(db, product_id)
    if not product:
        raise HTTPException(status_code=404, detail={"code": 404, "message": "Product not found"})
    return {
        "product_id": product_id,
        "similar": search_service.similar_products(es, product_id),
    }
