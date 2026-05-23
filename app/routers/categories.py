import json
import logging

import redis
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.cache import get_redis
from app.database import get_db
from app.services import product_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/categories", tags=["Categories"])

CACHE_KEY = "categories:all"
CACHE_TTL = 3600  # 1 hour


@router.get("")
def list_categories(db: Session = Depends(get_db), cache: redis.Redis = Depends(get_redis)):
    try:
        cached = cache.get(CACHE_KEY)
        if cached:
            data = json.loads(cached)
            return {"data": data, "total": len(data), "cached": True}
    except Exception as e:
        logger.warning(f"Redis read failed, falling through: {e}")

    categories = product_service.get_all_categories(db)
    data = [c.model_dump() for c in categories]

    try:
        cache.set(CACHE_KEY, json.dumps(data), ex=CACHE_TTL)
    except Exception as e:
        logger.warning(f"Redis write failed: {e}")

    return {"data": categories, "total": len(categories)}
