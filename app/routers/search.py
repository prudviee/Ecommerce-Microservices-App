from elasticsearch import Elasticsearch
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.elastic import get_es
from app.services import product_service, search_service

router = APIRouter(prefix="/search", tags=["Search"])


@router.get("")
def global_search(
    query: str = Query(..., min_length=1, description="Search term"),
    limit: int = Query(5, ge=1, le=20, description="Max results per type"),
    db: Session = Depends(get_db),
    es: Elasticsearch = Depends(get_es),
):
    products, products_total = search_service.global_search(es, query, limit)
    categories = product_service.search_categories(db, query, limit)

    return {
        "query": query,
        "products": products,
        "categories": categories,
        "totals": {
            "products": products_total,
            "categories": len(categories),
        },
    }
