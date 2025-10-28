import logging
from datetime import datetime
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

    def fetch_orders(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
        """Fetch postings (orders) from Ozon within the date range."""
        orders: List[Dict[str, Any]] = []
        offset = 0
        limit = 100
        while True:
            payload = {
                "filter": {
                    "since": date_from.isoformat(),
                    "to": date_to.isoformat(),
                },
                "limit": limit,
                "offset": offset,
                "with": {
                    "analytics_data": True,
                    "financial_data": True,
                },
            }
            data = self._post("/v3/posting/fbs/list", payload)
            result = data.get("result", [])
            orders.extend(result)
            if len(result) < limit:
                break
            offset += limit
        logger.info("Fetched %s Ozon postings", len(orders))
        return orders
    @staticmethod
    def _iso_z(dt: datetime) -> str:
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    
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

    # объявляем переменные, чтобы IDE не ругалась
    page = 1
    page_size = 1000
    max_pages = 2000  # предохранитель от бесконечного цикла

    while True:
        payload["page"] = page
        payload["page_size"] = page_size
        data = self._post("/v4/finance/transaction/list", payload)
        result = data.get("result", {})
        ops = result.get("operations") or result.get("records") or []
        if not ops:
            break

        records.extend(ops)

        # если операций меньше размера страницы — дальше пусто
        if len(ops) < page_size:
            break

        page += 1
        if page > max_pages:
            break

    logger.info("Fetched %s Ozon finance records (pages=%s)", len(records), page - 1)
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
