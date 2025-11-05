import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from app.services.loaders.auth import ozon_headers

logger = logging.getLogger(__name__)


class OzonApiClient:
    def __init__(
        self,
        base_url: str,
        client_id: str,
        api_key: str,
        timeout: int = 30,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(ozon_headers(client_id, api_key))
        self.timeout = timeout

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=1, max=10))
    def _post(self, endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}{endpoint}"
        logger.debug("Requesting Ozon endpoint %s", url)
        response = self.session.post(url, json=payload, timeout=self.timeout)
        if response.status_code >= 400:
            logger.error("Ozon API error %s: %s", response.status_code, response.text)
            response.raise_for_status()
        return response.json()

    @staticmethod
    def _iso_z(dt: datetime) -> str:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def fetch_orders_fbs(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
        """Fetch FBS postings from Ozon within the date range."""
        orders: List[Dict[str, Any]] = []
        offset = 0
        limit = 100
        while True:
            payload = {
                "filter": {
                    "since": self._iso_z(date_from),
                    "to": self._iso_z(date_to),
                },
                "limit": limit,
                "offset": offset,
                "with": {
                    "analytics_data": True,
                    "financial_data": True,
                },
            }
            data = self._post("/v3/posting/fbs/list", payload)
            result = data.get("result") or {}
            postings = result.get("postings") or []
            orders.extend(postings)
            if len(postings) < limit:
                break
            offset += limit
        logger.info("Fetched %s Ozon postings", len(orders))
        return orders

    def fetch_orders_fbo(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
        """Fetch FBO postings within the date range."""
        postings: List[Dict[str, Any]] = []
        offset = 0
        limit = 100
        while True:
            payload = {
                "filter": {
                    "since": self._iso_z(date_from),
                    "to": self._iso_z(date_to),
                },
                "limit": limit,
                "offset": offset,
                "dir": "ASC",
                "with": {
                    "analytics_data": True,
                    "financial_data": True,
                },
            }
            data = self._post("/v2/posting/fbo/list", payload)
            result = data.get("result")
            if isinstance(result, list):
                postings_batch = result
            else:
                postings_batch = (result or {}).get("postings") or []
            postings.extend(postings_batch)
            if len(postings_batch) < limit:
                break
            offset += limit
        logger.info("Fetched %s Ozon FBO postings", len(postings))
        return postings

    def fetch_orders(
        self,
        date_from: datetime,
        date_to: datetime,
        include_fbs: bool = True,
        include_fbo: bool = True,
    ) -> List[Dict[str, Any]]:
        postings: List[Dict[str, Any]] = []
        if include_fbs:
            postings.extend(self.fetch_orders_fbs(date_from, date_to))
        if include_fbo:
            postings.extend(self.fetch_orders_fbo(date_from, date_to))
        return postings
    def fetch_finance(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
        """
        Ozon v4/finance/transaction/list:
        - обязательна нумерация страниц с 1
        - page_size до 1000
        - прекращаем, когда вернулась пустая страница или меньше page_size
        """
        records: List[Dict[str, Any]] = []

        payload: Dict[str, Any] = {
            "filter": {
                "date": {
                    "from": self._iso_z(date_from),
                    "to": self._iso_z(date_to),
                }
            },
            "page": 1,
            "page_size": 1000,
            "language": "RU",
        }

        max_pages = 2000
        while True:
            data = self._post("/v4/finance/transaction/list", payload)
            result = data.get("result", {})
            ops = result.get("operations") or result.get("records") or []
            if not ops:
                break
            records.extend(ops)
            if len(ops) < payload["page_size"]:
                break
            payload["page"] += 1
            if payload["page"] > max_pages:
                break

        logger.info("Fetched %s Ozon finance records (pages=%s)", len(records), payload["page"] - 1)
        return records

    def fetch_ads_costs(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
        """Fetch advertising spend per day."""
        payload = {
            "date_from": date_from.strftime("%Y-%m-%d"),
            "date_to": date_to.strftime("%Y-%m-%d"),
            "metrics": ["shows", "clicks", "spend"],
            "group_by": ["date", "sku"],
        }
        data = self._post("/v1/analytics/advertising/expense", payload)
        results = data.get("result", [])
        logger.info("Fetched %s Ozon ads rows", len(results))
        return results
