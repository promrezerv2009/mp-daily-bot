import logging
from datetime import datetime
from typing import Any, Dict, List

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from app.services.loaders.auth import wb_headers

logger = logging.getLogger(__name__)


class WildberriesApiClient:
    def __init__(self, base_url: str, token: str, timeout: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(wb_headers(token))
        self.timeout = timeout

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=1, max=10))
    def _get(self, endpoint: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        url = f"{self.base_url}{endpoint}"
        logger.debug("Requesting WB endpoint %s", url)
        response = self.session.get(url, params=params, timeout=self.timeout)
        if response.status_code >= 400:
            logger.error("WB API error %s: %s", response.status_code, response.text)
            response.raise_for_status()
        return response.json()

    def fetch_orders(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
        params = {
            "dateFrom": date_from.strftime("%Y-%m-%d"),
            "flag": 0,
        }
        data = self._get("/api/v1/supplier/orders", params)
        return [row for row in data if date_from.date() <= datetime.fromisoformat(row["date"]).date() <= date_to.date()]

    def fetch_sales(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
        params = {
            "dateFrom": date_from.strftime("%Y-%m-%d"),
        }
        data = self._get("/api/v1/supplier/sales", params)
        return [row for row in data if date_from.date() <= datetime.fromisoformat(row["date"]).date() <= date_to.date()]

    def fetch_ads_costs(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
        params = {
            "dateFrom": date_from.strftime("%Y-%m-%d"),
            "dateTo": date_to.strftime("%Y-%m-%d"),
        }
        data = self._get("/api/v1/advertising/expenditures", params)
        return data
