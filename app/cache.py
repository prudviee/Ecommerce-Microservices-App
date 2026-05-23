import logging

import redis

from app.config import settings

logger = logging.getLogger(__name__)

redis_client = redis.Redis(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    decode_responses=True,
)


def get_redis() -> redis.Redis:
    return redis_client
