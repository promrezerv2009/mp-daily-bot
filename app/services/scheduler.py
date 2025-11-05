from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict
from zoneinfo import ZoneInfo

import aiohttp
from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.dialects.postgresql import insert

from app.config import get_settings
from app.db import session_scope
from app.models import AdsCost, Cost, DailyAggregate, Order
from app.services.analytics import calculate_daily_metrics, summarize
from app.services.loaders.ozon_api import OzonApiClient
from app.services.loaders.wb_api import WildberriesApiClient
from app.services.transform import (
    build_costs_from_finance,
    transform_ads,
    transform_ozon_orders,
    transform_wb_sales,
)

logger = logging.getLogger(__name__)


def _fmt_money(value: Any) -> str:
    try:
        return f"{float(value):,.0f}".replace(",", " ")
    except Exception:
        return str(value)


class DataRefreshService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.tz = ZoneInfo(self.settings.tz)
        self.ozon_client = OzonApiClient(
            base_url=self.settings.ozon_api_url,
            client_id=self.settings.ozon_client_id,
            api_key=self.settings.ozon_api_key,
        )
        self.wb_client = WildberriesApiClient(
            base_url=self.settings.wb_api_url,
            token=self.settings.wb_token,
        )

    async def refresh_yesterday(self) -> None:
        now = datetime.now(self.tz)
        end_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start_date = end_date - timedelta(days=1)
        await self.refresh_range(start_date, end_date - timedelta(seconds=1))

    async def refresh_range(self, date_from: datetime, date_to: datetime) -> None:
        await asyncio.to_thread(self._refresh_range_sync, date_from, date_to)

    def _refresh_range_sync(self, date_from: datetime, date_to: datetime) -> None:
        logger.info("Starting data refresh from %s to %s", date_from, date_to)
        ozon_orders_raw = self.ozon_client.fetch_orders(date_from, date_to)
        ozon_finance_raw = self.ozon_client.fetch_finance(date_from, date_to)
        ozon_ads_raw = self.ozon_client.fetch_ads_costs(date_from, date_to)

        wb_orders_raw = self.wb_client.fetch_orders(date_from, date_to)
        wb_sales_raw = self.wb_client.fetch_sales(date_from, date_to)
        wb_ads_raw = self.wb_client.fetch_ads_costs(date_from, date_to)

        ozon_orders = transform_ozon_orders(ozon_orders_raw)
        wb_orders = transform_wb_sales(wb_sales_raw or wb_orders_raw)

        costs = build_costs_from_finance(ozon_finance_raw)
        ads_costs = transform_ads(ozon_ads_raw, platform="ozon") + transform_ads(
            wb_ads_raw, platform="wb"
        )
        all_orders = ozon_orders + wb_orders

        daily_metrics = calculate_daily_metrics(all_orders, costs, ads_costs)
        summary = summarize(daily_metrics)

        self._persist_data(
            all_orders=all_orders,
            costs=costs,
            ads_costs=ads_costs,
            daily_metrics=daily_metrics,
        )
        logger.info(
            "Data refresh finished. Totals: %s",
            summary.get("total"),
        )

    def _persist_data(
        self,
        all_orders,
        costs,
        ads_costs,
        daily_metrics,
    ) -> None:
        with session_scope() as session:
            if all_orders:
                order_stmt = insert(Order).values(
                    [
                        {
                            "id": order["id"],
                            "platform": order["platform"],
                            "date": order["date"],
                            "sku": order["sku"],
                            "price": order["price"],
                            "qty_ordered": order["qty_ordered"],
                            "qty_delivered": order["qty_delivered"],
                            "qty_returned": order["qty_returned"],
                        }
                        for order in all_orders
                    ]
                )
                order_stmt = order_stmt.on_conflict_do_update(
                    index_elements=["id"],
                    set_={
                        "platform": order_stmt.excluded.platform,
                        "date": order_stmt.excluded.date,
                        "sku": order_stmt.excluded.sku,
                        "price": order_stmt.excluded.price,
                        "qty_ordered": order_stmt.excluded.qty_ordered,
                        "qty_delivered": order_stmt.excluded.qty_delivered,
                        "qty_returned": order_stmt.excluded.qty_returned,
                    },
                )
                session.execute(order_stmt)

            if costs:
                cost_stmt = insert(Cost).values(
                    [
                        {
                            "platform": cost["platform"],
                            "sku": cost["sku"],
                            "date": cost["date"],
                            "commission_fee": cost["commission_fee"],
                            "logistics_fee": cost["logistics_fee"],
                            "storage_fee": cost["storage_fee"],
                            "cogs_per_unit": cost["cogs_per_unit"],
                        }
                        for cost in costs
                    ]
                )
                cost_stmt = cost_stmt.on_conflict_do_update(
                    index_elements=["platform", "date", "sku"],
                    set_={
                        "commission_fee": cost_stmt.excluded.commission_fee,
                        "logistics_fee": cost_stmt.excluded.logistics_fee,
                        "storage_fee": cost_stmt.excluded.storage_fee,
                        "cogs_per_unit": cost_stmt.excluded.cogs_per_unit,
                    },
                )
                session.execute(cost_stmt)

            if ads_costs:
                ads_stmt = insert(AdsCost).values(
                    [
                        {
                            "platform": ad["platform"],
                            "sku": ad.get("sku") or "__general__",
                            "date": ad["date"],
                            "amount": ad["amount"],
                        }
                        for ad in ads_costs
                    ]
                )
                ads_stmt = ads_stmt.on_conflict_do_update(
                    index_elements=["platform", "date", "sku"],
                    set_={
                        "amount": ads_stmt.excluded.amount,
                    },
                )
                session.execute(ads_stmt)

            for platform, by_date in daily_metrics.items():
                for metric_date, acc in by_date.items():
                    stmt = insert(DailyAggregate).values(
                        {
                            "platform": platform,
                            "date": metric_date,
                            "orders_count": acc.orders_count,
                            "delivered": acc.delivered,
                            "returns": acc.returns,
                            "revenue_delivered": acc.revenue_delivered,
                            "cogs": acc.cogs,
                            "commission": acc.commission,
                            "logistics": acc.logistics,
                            "storage": acc.storage,
                            "ads": acc.ads,
                            "gross_profit": acc.gross_profit,
                            "profit": acc.profit,
                            "romi": acc.romi,
                        }
                    )
                    stmt = stmt.on_conflict_do_update(
                        constraint="uq_aggregates_platform_date",
                        set_={
                            "orders_count": stmt.excluded.orders_count,
                            "delivered": stmt.excluded.delivered,
                            "returns": stmt.excluded.returns,
                            "revenue_delivered": stmt.excluded.revenue_delivered,
                            "cogs": stmt.excluded.cogs,
                            "commission": stmt.excluded.commission,
                            "logistics": stmt.excluded.logistics,
                            "storage": stmt.excluded.storage,
                            "ads": stmt.excluded.ads,
                            "gross_profit": stmt.excluded.gross_profit,
                            "profit": stmt.excluded.profit,
                            "romi": stmt.excluded.romi,
                        },
                    )
                    session.execute(stmt)


async def send_daily_digest() -> None:
    settings = get_settings()
    if not settings.bot_recipients or not settings.telegram_bot_token:
        return

    base_url = os.getenv("API_BASE_URL", "http://app:8000")
    summary: Dict[str, Any] | None = None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url}/api/mobile/summary?period=1d", timeout=15) as resp:
                if resp.status >= 400:
                    detail = await resp.text()
                    raise RuntimeError(f"summary request failed: {resp.status} {detail[:200]}")
                summary = await resp.json()
    except Exception as exc:  # broad catch to avoid crashing scheduler
        logger.warning("Daily digest fetch failed: %s", exc)
        return
    if not summary:
        return

    text = (
        "📅 Дайджест за вчера\n"
        f"Выручка: {_fmt_money(summary.get('revenue', 0))} ₽\n"
        f"Прибыль: {_fmt_money(summary.get('profit', 0))} ₽\n"
        f"Заказы: {summary.get('orders', 0)}\n"
        f"ROI: {summary.get('roi', '-')}"
    )

    bot = Bot(token=settings.telegram_bot_token)
    try:
        for chat_id in settings.bot_recipients:
            try:
                await bot.send_message(chat_id, text)
            except Exception as exc:
                logger.warning("Failed to send digest to %s: %s", chat_id, exc)
    finally:
        await bot.session.close()


def setup_scheduler() -> tuple[AsyncIOScheduler, DataRefreshService]:
    settings = get_settings()
    scheduler = AsyncIOScheduler(timezone=ZoneInfo(settings.scheduler_timezone))
    service = DataRefreshService()
    tz = ZoneInfo(settings.scheduler_timezone)
    refresh_trigger = CronTrigger.from_crontab(settings.scheduler_cron, timezone=tz)
    scheduler.add_job(
        service.refresh_yesterday,
        trigger=refresh_trigger,
        id="daily_refresh",
        replace_existing=True,
    )
    digest_trigger = CronTrigger(hour=9, minute=0, timezone=tz)
    scheduler.add_job(
        send_daily_digest,
        trigger=digest_trigger,
        id="daily_mobile_digest",
        replace_existing=True,
    )
    scheduler.start()
    return scheduler, service
