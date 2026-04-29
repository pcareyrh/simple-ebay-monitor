import asyncio
import base64
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from app.config import settings
from app.schemas import SearchFilters

logger = logging.getLogger(__name__)

DAILY_API_CALL_LIMIT = 5000


@dataclass
class OAuthToken:
    access_token: str
    expires_at: datetime


class EbayAPIError(Exception):
    pass


class EbayClient:
    """eBay Browse API client using OAuth client credentials flow."""

    def __init__(self) -> None:
        self._token: Optional[OAuthToken] = None
        self._token_lock = asyncio.Lock()

    @property
    def base_url(self) -> str:
        return settings.ebay_api_base

    async def _get_token(self) -> str:
        async with self._token_lock:
            now = datetime.now(timezone.utc)
            if self._token and self._token.expires_at - timedelta(minutes=2) > now:
                return self._token.access_token

            if not settings.ebay_client_id or not settings.ebay_client_secret:
                raise EbayAPIError("EBAY_CLIENT_ID and EBAY_CLIENT_SECRET are not configured")

            creds = f"{settings.ebay_client_id}:{settings.ebay_client_secret}"
            basic = base64.b64encode(creds.encode()).decode()
            headers = {
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            }
            data = {
                "grant_type": "client_credentials",
                "scope": "https://api.ebay.com/oauth/api_scope",
            }
            url = f"{self.base_url}/identity/v1/oauth2/token"

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, headers=headers, data=data)
            if resp.status_code != 200:
                raise EbayAPIError(f"Token request failed: {resp.status_code} {resp.text}")
            payload = resp.json()
            token = payload["access_token"]
            expires_in = int(payload.get("expires_in", 7200))
            self._token = OAuthToken(
                access_token=token,
                expires_at=now + timedelta(seconds=expires_in),
            )
            return token

    def _build_filter_string(self, filters: SearchFilters) -> Optional[str]:
        parts: list[str] = []

        if filters.price_min is not None or filters.price_max is not None:
            lo = f"{filters.price_min:.2f}" if filters.price_min is not None else ""
            hi = f"{filters.price_max:.2f}" if filters.price_max is not None else ""
            parts.append(f"price:[{lo}..{hi}]")
            parts.append(f"priceCurrency:{filters.price_currency or 'USD'}")

        if filters.conditions:
            conds = "|".join(c.upper() for c in filters.conditions)
            parts.append(f"conditions:{{{conds}}}")

        buying = filters.buying_options or ["FIXED_PRICE", "AUCTION"]
        parts.append(f"buyingOptions:{{{'|'.join(buying)}}}")

        if filters.free_shipping:
            parts.append("maxDeliveryCost:0")

        if filters.item_location_country:
            parts.append(f"itemLocationCountry:{filters.item_location_country.upper()}")

        if filters.delivery_country:
            parts.append(f"deliveryCountry:{filters.delivery_country.upper()}")

        return ",".join(parts) if parts else None

    async def search(
        self,
        query: str,
        filters: SearchFilters,
        limit: int = 200,
        offset: int = 0,
    ) -> dict[str, Any]:
        token = await self._get_token()
        url = f"{self.base_url}/buy/browse/v1/item_summary/search"
        params: dict[str, Any] = {
            "q": query,
            "limit": min(limit, 200),
            "offset": offset,
            "fieldgroups": "ADDITIONAL_SELLER_DETAILS",
        }
        filter_str = self._build_filter_string(filters)
        if filter_str:
            params["filter"] = filter_str
        if filters.category_ids:
            params["category_ids"] = ",".join(filters.category_ids)

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
        }

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(url, headers=headers, params=params)

        if resp.status_code == 401:
            # Token may have been revoked; drop and retry once
            async with self._token_lock:
                self._token = None
            token = await self._get_token()
            headers["Authorization"] = f"Bearer {token}"
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.get(url, headers=headers, params=params)

        if resp.status_code >= 400:
            raise EbayAPIError(f"Search failed: {resp.status_code} {resp.text}")

        return resp.json()


ebay_client = EbayClient()


def parse_item_summary(item: dict[str, Any]) -> dict[str, Any]:
    """Extract the fields we store from an eBay itemSummary response object."""
    price_info = item.get("price") or {}
    current_bid = item.get("currentBidPrice") or {}
    shipping_options = item.get("shippingOptions") or []
    shipping_cost: Optional[float] = None
    if shipping_options:
        cost = shipping_options[0].get("shippingCost") or {}
        try:
            shipping_cost = float(cost["value"]) if cost.get("value") is not None else None
        except (TypeError, ValueError):
            shipping_cost = None

    seller = item.get("seller") or {}
    buying_options = item.get("buyingOptions") or []
    buying_option = buying_options[0] if buying_options else None

    end_date: Optional[datetime] = None
    raw_end = item.get("itemEndDate")
    if raw_end:
        try:
            end_date = datetime.fromisoformat(raw_end.replace("Z", "+00:00"))
        except ValueError:
            end_date = None

    def _to_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    image = (item.get("image") or {}).get("imageUrl") or (item.get("thumbnailImages") or [{}])[0].get("imageUrl")

    feedback = seller.get("feedbackScore")
    try:
        feedback_score = int(feedback) if feedback is not None else None
    except (TypeError, ValueError):
        feedback_score = None

    return {
        "ebay_item_id": item.get("itemId"),
        "title": item.get("title") or "",
        "price": _to_float(price_info.get("value")),
        "currency": price_info.get("currency"),
        "condition": item.get("condition"),
        "image_url": image,
        "item_url": item.get("itemWebUrl") or item.get("itemHref"),
        "buying_option": buying_option,
        "current_bid": _to_float(current_bid.get("value")),
        "bid_count": item.get("bidCount"),
        "end_date": end_date,
        "seller_username": seller.get("username"),
        "seller_feedback_score": feedback_score,
        "shipping_cost": shipping_cost,
    }
