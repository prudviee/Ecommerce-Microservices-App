from typing import List, Optional

from pydantic import BaseModel, Field, model_validator


class ProductSchema(BaseModel):
    id: int
    title: str
    description: Optional[str] = None
    price: float
    discount_percentage: Optional[float] = None
    rating: Optional[float] = None
    stock: Optional[int] = None
    brand: Optional[str] = None
    sku: Optional[str] = None
    category: Optional[str] = None
    tags: List[str] = []
    thumbnail: Optional[str] = None
    images: List[str] = []

    model_config = {"from_attributes": True}


class CategorySchema(BaseModel):
    id: int
    name: str

    model_config = {"from_attributes": True}


class ProductCreateSchema(BaseModel):
    title: str = Field(..., max_length=255)
    price: float = Field(..., gt=0)
    category: str
    description: Optional[str] = None
    discount_percentage: Optional[float] = Field(None, ge=0, le=100)
    rating: Optional[float] = Field(None, ge=0, le=5)
    stock: Optional[int] = Field(None, ge=0)
    brand: Optional[str] = None
    sku: Optional[str] = None
    weight: Optional[float] = None
    thumbnail: Optional[str] = None
    images: List[str] = []
    tags: List[str] = []


class ProductUpdateSchema(BaseModel):
    title: Optional[str] = Field(None, max_length=255)
    price: Optional[float] = Field(None, gt=0)
    category: Optional[str] = None
    description: Optional[str] = None
    discount_percentage: Optional[float] = Field(None, ge=0, le=100)
    rating: Optional[float] = Field(None, ge=0, le=5)
    stock: Optional[int] = Field(None, ge=0)
    brand: Optional[str] = None
    sku: Optional[str] = None
    weight: Optional[float] = None
    thumbnail: Optional[str] = None
    images: Optional[List[str]] = None
    tags: Optional[List[str]] = None


class ProductBulkCreateSchema(BaseModel):
    products: List[ProductCreateSchema] = Field(..., min_length=1, max_length=50)


class StockAdjustSchema(BaseModel):
    delta: int = Field(..., description="Units to add (positive) or remove (negative)")
    reason: Optional[str] = Field(None, max_length=255)


class BuySchema(BaseModel):
    quantity: int = Field(..., gt=0, le=1000)
