import logging
import time

from elasticsearch import Elasticsearch, helpers
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.elastic import get_es
from app.limiter import limiter
from app.services import product_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.post("/reindex")
@limiter.limit("5/minute")
def reindex(
    request: Request,
    db: Session = Depends(get_db),
    es: Elasticsearch = Depends(get_es),
):
    start = time.monotonic()

    try:
        products = product_service.get_all_products(db)
    except Exception as e:
        logger.error(f"MySQL read failed during reindex: {e}")
        raise HTTPException(status_code=500, detail={"code": 500, "message": f"MySQL read failed: {str(e)}"})

    actions = [
        {
            "_index": "products",
            "_id": str(p.id),
            "_source": {
                "id": p.id,
                "title": p.title,
                "description": p.description,
                "price": p.price,
                "discount_percentage": p.discount_percentage,
                "rating": p.rating,
                "stock": p.stock,
                "brand": p.brand,
                "sku": p.sku,
                "category": p.category,
                "tags": p.tags,
                "thumbnail": p.thumbnail,
                "images": p.images,
            },
        }
        for p in products
    ]

    try:
        indexed, failed = helpers.bulk(es, actions, raise_on_error=False, stats_only=True)
    except Exception as e:
        logger.error(f"ES bulk reindex failed: {e}")
        raise HTTPException(status_code=500, detail={"code": 500, "message": f"Elasticsearch bulk failed: {str(e)}"})

    duration_ms = round((time.monotonic() - start) * 1000)
    logger.info(f"Reindex complete: {indexed} indexed, {failed} failed in {duration_ms}ms")

    return {
        "indexed": indexed,
        "failed": failed,
        "duration_ms": duration_ms,
        "status": "ok" if failed == 0 else "partial",
    }
