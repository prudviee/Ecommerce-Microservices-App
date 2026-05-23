import time
from typing import Dict, List, Optional, Tuple

from sqlalchemy import asc, desc, func
from sqlalchemy.orm import Session

from app.models.product import Category, Product, ProductImage, ProductTag
from app.schemas.product import BuySchema, CategorySchema, ProductBulkCreateSchema, ProductCreateSchema, ProductSchema, ProductUpdateSchema, StockAdjustSchema

SORT_MAP = {
    "price_asc":      (Product.price, asc),
    "price_desc":     (Product.price, desc),
    "rating_asc":     (Product.rating, asc),
    "rating_desc":    (Product.rating, desc),
    "discount_desc":  (Product.discount_percentage, desc),
}


def get_all_categories(db: Session) -> List[CategorySchema]:
    categories = db.query(Category).order_by(Category.name).all()
    return [CategorySchema.model_validate(c) for c in categories]


def get_products(
    db: Session,
    page: int = 1,
    limit: int = 20,
    category: Optional[str] = None,
    sort: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
) -> Tuple[List[ProductSchema], int]:
    query = db.query(Product)

    if category:
        cat = db.query(Category).filter(func.lower(Category.name) == category.lower()).first()
        if not cat:
            return [], 0
        query = query.filter(Product.category_id == cat.id)

    if min_price is not None:
        query = query.filter(Product.price >= min_price)
    if max_price is not None:
        query = query.filter(Product.price <= max_price)

    if sort and sort in SORT_MAP:
        col, direction = SORT_MAP[sort]
        query = query.order_by(direction(col))

    total = query.count()
    products = query.offset((page - 1) * limit).limit(limit).all()
    return [_to_schema(p) for p in products], total


def get_top_rated(
    db: Session,
    min_rating: float = 4.5,
    page: int = 1,
    limit: int = 20,
) -> Tuple[List[ProductSchema], int]:
    query = (
        db.query(Product)
        .filter(Product.rating >= min_rating)
        .order_by(desc(Product.rating))
    )
    total = query.count()
    products = query.offset((page - 1) * limit).limit(limit).all()
    return [_to_schema(p) for p in products], total


def get_on_sale(
    db: Session,
    min_discount: float = 10.0,
    page: int = 1,
    limit: int = 20,
) -> Tuple[List[ProductSchema], int]:
    query = (
        db.query(Product)
        .filter(Product.discount_percentage >= min_discount)
        .order_by(desc(Product.discount_percentage))
    )
    total = query.count()
    products = query.offset((page - 1) * limit).limit(limit).all()
    return [_to_schema(p) for p in products], total


def search_categories(db: Session, query: str, limit: int = 5) -> List[CategorySchema]:
    categories = (
        db.query(Category)
        .filter(func.lower(Category.name).contains(query.lower()))
        .limit(limit)
        .all()
    )
    return [CategorySchema.model_validate(c) for c in categories]


def create_product(db: Session, data: ProductCreateSchema) -> ProductSchema:
    cat = db.query(Category).filter(func.lower(Category.name) == data.category.lower()).first()
    if not cat:
        return None, f"Category '{data.category}' not found"

    product = Product(
        title=data.title,
        description=data.description,
        price=data.price,
        discount_percentage=data.discount_percentage,
        rating=data.rating,
        stock=data.stock,
        brand=data.brand,
        sku=data.sku,
        weight=data.weight,
        thumbnail=data.thumbnail,
        category_id=cat.id,
    )
    db.add(product)
    db.flush()

    for tag in data.tags:
        db.add(ProductTag(product_id=product.id, tag=tag))
    for url in data.images:
        db.add(ProductImage(product_id=product.id, url=url))

    db.commit()
    db.refresh(product)
    return _to_schema(product), None


def update_product(db: Session, product_id: int, data: ProductUpdateSchema):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        return None, "Product not found"

    if data.category is not None:
        cat = db.query(Category).filter(func.lower(Category.name) == data.category.lower()).first()
        if not cat:
            return None, f"Category '{data.category}' not found"
        product.category_id = cat.id

    for field in ["title", "description", "price", "discount_percentage", "rating", "stock", "brand", "sku", "weight", "thumbnail"]:
        val = getattr(data, field)
        if val is not None:
            setattr(product, field, val)

    if data.tags is not None:
        db.query(ProductTag).filter(ProductTag.product_id == product.id).delete()
        for tag in data.tags:
            db.add(ProductTag(product_id=product.id, tag=tag))

    if data.images is not None:
        db.query(ProductImage).filter(ProductImage.product_id == product.id).delete()
        for url in data.images:
            db.add(ProductImage(product_id=product.id, url=url))

    db.commit()
    db.refresh(product)
    return _to_schema(product), None


def delete_product(db: Session, product_id: int) -> bool:
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        return False
    db.delete(product)
    db.commit()
    return True


def adjust_stock(db: Session, product_id: int, data: StockAdjustSchema):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        return None, "not_found", None

    if data.delta == 0:
        return None, "zero_delta", None

    new_stock = (product.stock or 0) + data.delta
    if new_stock < 0:
        return None, "insufficient", product.stock

    previous = product.stock
    rows = (
        db.query(Product)
        .filter(Product.id == product_id, (Product.stock + data.delta) >= 0)
        .update({"stock": Product.stock + data.delta})
    )
    if rows == 0:
        return None, "insufficient", product.stock

    db.commit()
    db.refresh(product)
    return {
        "product_id": product_id,
        "previous_stock": previous,
        "delta": data.delta,
        "current_stock": product.stock,
        "reason": data.reason,
    }, None, None


def buy_product(db: Session, product_id: int, data: BuySchema):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        return None, "not_found", None

    # Single atomic UPDATE — safe under concurrent load
    rows = (
        db.query(Product)
        .filter(Product.id == product_id, Product.stock >= data.quantity)
        .update({"stock": Product.stock - data.quantity})
    )
    if rows == 0:
        return None, "insufficient", product.stock

    db.commit()
    db.refresh(product)
    return {
        "product_id": product_id,
        "product_title": product.title,
        "quantity_purchased": data.quantity,
        "stock_remaining": product.stock,
    }, None, None


def get_all_products(db: Session) -> List[ProductSchema]:
    products = db.query(Product).all()
    return [_to_schema(p) for p in products]


def bulk_create_products(db: Session, data: ProductBulkCreateSchema):
    # Pre-validate all categories, cache lookups to avoid N queries
    cat_map: Dict[str, Category] = {}
    errors = []
    for i, item in enumerate(data.products):
        key = item.category.lower()
        if key not in cat_map:
            cat = db.query(Category).filter(func.lower(Category.name) == key).first()
            if cat:
                cat_map[key] = cat
            else:
                errors.append({"index": i, "error": f"Category '{item.category}' not found"})

    if errors:
        return None, errors

    products = []
    for item in data.products:
        cat = cat_map[item.category.lower()]
        product = Product(
            title=item.title,
            description=item.description,
            price=item.price,
            discount_percentage=item.discount_percentage,
            rating=item.rating,
            stock=item.stock,
            brand=item.brand,
            sku=item.sku,
            weight=item.weight,
            thumbnail=item.thumbnail,
            category_id=cat.id,
        )
        db.add(product)
        db.flush()

        for tag in item.tags:
            db.add(ProductTag(product_id=product.id, tag=tag))
        for url in item.images:
            db.add(ProductImage(product_id=product.id, url=url))

        products.append(product)

    db.commit()
    for p in products:
        db.refresh(p)

    return [_to_schema(p) for p in products], None


def get_product_by_id(db: Session, product_id: int) -> Optional[ProductSchema]:
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        return None
    return _to_schema(product)


def _to_schema(product: Product) -> ProductSchema:
    return ProductSchema(
        id=product.id,
        title=product.title,
        description=product.description,
        price=float(product.price),
        discount_percentage=float(product.discount_percentage) if product.discount_percentage else None,
        rating=float(product.rating) if product.rating else None,
        stock=product.stock,
        brand=product.brand,
        sku=product.sku,
        category=product.category.name if product.category else None,
        tags=[t.tag for t in product.tags],
        thumbnail=product.thumbnail,
        images=[i.url for i in product.images],
    )
