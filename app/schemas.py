from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class SearchFilters(BaseModel):
    price_min: Optional[float] = None
    price_max: Optional[float] = None
    price_currency: str = "USD"
    conditions: list[str] = Field(default_factory=list)
    buying_options: list[str] = Field(default_factory=list)
    free_shipping: bool = False
    item_location_country: Optional[str] = None
    delivery_country: Optional[str] = None
    category_ids: list[str] = Field(default_factory=list)


class SearchBase(BaseModel):
    name: str
    query: str
    filters: SearchFilters = Field(default_factory=SearchFilters)
    interval_minutes: int = 60
    enabled: bool = True


class SearchCreate(SearchBase):
    pass


class SearchUpdate(BaseModel):
    name: Optional[str] = None
    query: Optional[str] = None
    filters: Optional[SearchFilters] = None
    interval_minutes: Optional[int] = None
    enabled: Optional[bool] = None


class SearchOut(SearchBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    last_run_at: Optional[datetime] = None
    last_error: Optional[str] = None
    created_at: datetime


class ResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    search_id: int
    ebay_item_id: str
    title: str
    price: Optional[float]
    currency: Optional[str]
    condition: Optional[str]
    image_url: Optional[str]
    item_url: Optional[str]
    buying_option: Optional[str]
    current_bid: Optional[float]
    bid_count: Optional[int]
    end_date: Optional[datetime]
    seller_username: Optional[str]
    seller_feedback_score: Optional[int]
    shipping_cost: Optional[float]
    first_seen_at: datetime
    last_seen_at: datetime
    discarded: bool


class PriceHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    price: float
    currency: Optional[str]
    recorded_at: datetime


class StatusOut(BaseModel):
    status: str
    api_calls_today: int
    api_call_limit: int
    scheduler_running: bool
    searches_count: int
    enabled_searches_count: int
