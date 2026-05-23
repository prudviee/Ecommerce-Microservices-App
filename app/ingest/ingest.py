import logging
import time

import httpx

from app.database import Base, SessionLocal, engine
from app.elastic import es_client
from app.models.product import Category, Product, ProductImage, ProductTag

logger = logging.getLogger(__name__)

INDEX_NAME = "products"
DUMMYJSON_URL = "https://dummyjson.com/products?limit=0"

ES_MAPPING = {
    "mappings": {
        "properties": {
            "id": {"type": "integer"},
            "title": {"type": "text", "analyzer": "standard"},
            "description": {"type": "text", "analyzer": "standard"},
            "price": {"type": "float"},
            "discount_percentage": {"type": "float"},
            "rating": {"type": "float"},
            "stock": {"type": "integer"},
            "brand": {"type": "keyword"},
            "sku": {"type": "keyword"},
            "category": {"type": "keyword"},
            "tags": {"type": "keyword"},
            "thumbnail": {"type": "keyword", "index": False},
            "images": {"type": "keyword", "index": False},
        }
    }
}


def wait_for_mysql(retries: int = 20, delay: int = 5):
    for i in range(retries):
        try:
            with engine.connect():
                logger.info("MySQL is ready.")
                return
        except Exception:
            logger.info(f"Waiting for MySQL... ({i + 1}/{retries})")
            time.sleep(delay)
    raise RuntimeError("MySQL not available after retries.")


def wait_for_elasticsearch(retries: int = 20, delay: int = 5):
    for i in range(retries):
        try:
            if es_client.ping():
                logger.info("Elasticsearch is ready.")
                return
        except Exception:
            pass
        logger.info(f"Waiting for Elasticsearch... ({i + 1}/{retries})")
        time.sleep(delay)
    raise RuntimeError("Elasticsearch not available after retries.")


def create_tables():
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created.")


def is_already_ingested() -> bool:
    db = SessionLocal()
    try:
        return db.query(Product).count() > 0
    finally:
        db.close()


def fetch_products() -> list:
    with httpx.Client(timeout=30) as client:
        response = client.get(DUMMYJSON_URL)
        response.raise_for_status()
        return response.json()["products"]


def ingest_to_mysql(products: list):
    db = SessionLocal()
    try:
        category_map = {}
        for p in products:
            cat_name = p["category"]
            if cat_name not in category_map:
                existing = db.query(Category).filter_by(name=cat_name).first()
                if not existing:
                    cat = Category(name=cat_name)
                    db.add(cat)
                    db.flush()
                    category_map[cat_name] = cat.id
                else:
                    category_map[cat_name] = existing.id

        for p in products:
            product = Product(
                id=p["id"],
                title=p["title"],
                description=p.get("description"),
                price=p["price"],
                discount_percentage=p.get("discountPercentage"),
                rating=p.get("rating"),
                stock=p.get("stock"),
                brand=p.get("brand"),
                sku=p.get("sku"),
                weight=p.get("weight"),
                thumbnail=p.get("thumbnail"),
                category_id=category_map.get(p["category"]),
            )
            db.add(product)
            db.flush()

            for tag in p.get("tags", []):
                db.add(ProductTag(product_id=product.id, tag=tag))

            for img_url in p.get("images", []):
                db.add(ProductImage(product_id=product.id, url=img_url))

        db.commit()
        logger.info(f"Inserted {len(products)} products into MySQL.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def ingest_to_elasticsearch(products: list):
    if not es_client.indices.exists(index=INDEX_NAME):
        es_client.indices.create(index=INDEX_NAME, body=ES_MAPPING)
        logger.info(f"Created Elasticsearch index: {INDEX_NAME}")

    bulk_body = []
    for p in products:
        bulk_body.append({"index": {"_index": INDEX_NAME, "_id": p["id"]}})
        bulk_body.append({
            "id": p["id"],
            "title": p["title"],
            "description": p.get("description"),
            "price": p["price"],
            "discount_percentage": p.get("discountPercentage"),
            "rating": p.get("rating"),
            "stock": p.get("stock"),
            "brand": p.get("brand"),
            "sku": p.get("sku"),
            "category": p["category"],
            "tags": p.get("tags", []),
            "thumbnail": p.get("thumbnail"),
            "images": p.get("images", []),
        })

    es_client.bulk(body=bulk_body)
    logger.info(f"Indexed {len(products)} products into Elasticsearch.")


def run_ingestion():
    logger.info("Starting ingestion pipeline...")
    wait_for_mysql()
    wait_for_elasticsearch()
    create_tables()

    if is_already_ingested():
        logger.info("Data already ingested. Skipping.")
        return

    products = fetch_products()
    logger.info(f"Fetched {len(products)} products from dummyjson.")
    ingest_to_mysql(products)
    ingest_to_elasticsearch(products)
    logger.info("Ingestion complete.")
