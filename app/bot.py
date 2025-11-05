import asyncio
import os
from typing import Any, Dict, List, Optional

import aiohttp
from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.config import get_settings
from app.services.rbac import Role, get_role

settings = get_settings()
router = Router()

API_BASE = settings.api_base_url


PERIOD_BUTTONS: List[List[InlineKeyboardButton]] = [
    [
        InlineKeyboardButton(text="Yesterday", callback_data="period:yesterday"),
        InlineKeyboardButton(text="7 days", callback_data="period:7"),
    ],
    [
        InlineKeyboardButton(text="14 days", callback_data="period:14"),
        InlineKeyboardButton(text="This month", callback_data="period:month"),
    ],
    [
        InlineKeyboardButton(text="Custom range", callback_data="period:range"),
    ],
]


def period_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=PERIOD_BUTTONS)


def _fmt_money(value: Any) -> str:
    try:
        return f"{float(value):,.2f}".replace(",", " ").replace(".00", ",0").replace(".", ",")
    except Exception:
        return str(value)


def _title_for(period: str) -> str:
    mapping = {
        "yesterday": "за вчера",
        "7": "за 7 дней",
        "14": "за 14 дней",
        "7d": "за 7 дней",
        "14d": "за 14 дней",
        "30d": "за 30 дней",
        "month": "за месяц",
    }
    return mapping.get((period or "").lower(), f"за период {period}")


async def _api_json(method: str, path: str, **kwargs) -> Dict[str, Any]:
    url = f"{API_BASE}{path}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.request(method, url, timeout=25, **kwargs) as resp:
                if resp.status >= 400:
                    detail = await resp.text()
                    raise RuntimeError(f"API {resp.status}: {detail[:200]}")
                content_type = resp.headers.get("Content-Type", "")
                if "application/json" in content_type:
                    return await resp.json()
                raise RuntimeError(f"Unexpected content type: {content_type}")
    except asyncio.TimeoutError as exc:
        raise RuntimeError("timeout") from exc
    except aiohttp.ClientError as exc:
        raise RuntimeError(str(exc)) from exc


async def _activate_trial(user_id: str) -> Optional[str]:
    payload = {
        "user_id": user_id,
        "tenant_id": None,
        "plan": "trial",
        "days": settings.free_trial_days,
    }
    try:
        await _api_json("POST", "/api/billing/activate", json=payload)
    except RuntimeError as exc:
        return str(exc)
    return None


async def _send_metrics(message: Message, period: str) -> None:
    try:
        data = await _api_json("GET", f"/api/metrics?period={period}")
    except RuntimeError as exc:
        await message.reply(f"Не удалось получить метрики: {exc}")
        return

    if not data.get("ok"):
        await message.reply("Данных за выбранный период нет.")
        return

    summary = data.get("summary") or {}
    text = (
        f"📊 *Сводка {_title_for(period)}*\n\n"
        f"💰 *Выручка:* {_fmt_money(summary.get('revenue_delivered', 0))} ₽\n"
        f"🛒 Заказы: {summary.get('orders', 0)} | Доставлено: {summary.get('delivered', 0)} | Возвраты: {summary.get('returns', 0)}\n"
        f"📦 Расходы: комиссия {_fmt_money(summary.get('commission', 0))} ₽ | логистика {_fmt_money(summary.get('logistics', 0))} ₽ | хранение {_fmt_money(summary.get('storage', 0))} ₽\n"
        f"📈 Реклама: {_fmt_money(summary.get('ads', 0))} ₽ | Себестоимость: {_fmt_money(summary.get('cogs', 0))} ₽\n\n"
        f"💵 *Прибыль:* {_fmt_money(summary.get('profit', 0))} ₽\n"
        f"🎯 ROMI: {summary.get('romi', '-')}"
    )
    await message.reply(text, parse_mode=ParseMode.MARKDOWN, reply_markup=period_keyboard())


def _format_forecast(items: List[Dict[str, Any]]) -> str:
    lines = ["📦 *Прогноз остатков на 30 дней*"]
    for idx, item in enumerate(items[:10], start=1):
        sku = item.get("sku", "—")
        stock = item.get("stock", 0)
        days_left = item.get("days_left")
        qty_reco = item.get("qty_reco", 0)
        days_text = f"{days_left:.1f}" if isinstance(days_left, (int, float)) else "—"
        lines.append(
            f"{idx}. SKU {sku} — остаток {stock}, хватит на {days_text} дн., рекомендуем заказать {qty_reco}"
        )
    return "\n".join(lines)


# --- Команды ---


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    user_id = str(message.from_user.id)
    error = await _activate_trial(user_id)
    if error:
        await message.answer(
            "Не удалось активировать триал, свяжись с менеджером.\n"
            f"Причина: {error}"
        )
        return
    await message.answer(
        f"Триал активирован на {settings.free_trial_days} дней. "
        "Воспользуйся /dashboard, чтобы выбрать период и посмотреть сводку."
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    role = get_role(message.chat.id)
    commands = [
        "/dashboard — выбор периода",
        "/yesterday — метрики за вчера",
        "/orders [period] — метрики за период",
        "/forecast — прогноз остатков",
        "/subscribe — как оплатить подписку",
    ]
    if role == Role.admin:
        commands.append("/refresh [period|YYYY-MM-DD..YYYY-MM-DD] — запустить обновление данных")
    await message.answer("Доступные команды:\n" + "\n".join(commands), reply_markup=period_keyboard())


@router.message(Command("dashboard"))
async def cmd_dashboard(message: Message) -> None:
    await message.answer("Выберите период:", reply_markup=period_keyboard())


@router.message(Command("yesterday"))
async def cmd_yesterday(message: Message) -> None:
    await _send_metrics(message, "yesterday")


@router.message(Command("orders"))
async def cmd_orders(message: Message) -> None:
    period = "yesterday"
    if message.text:
        parts = message.text.split(maxsplit=1)
        if len(parts) > 1:
            period = parts[1].strip()
    await _send_metrics(message, period)


@router.message(Command("forecast"))
async def cmd_forecast(message: Message) -> None:
    try:
        data = await _api_json("GET", "/api/mobile/stocks/forecast?days=30&limit=20")
    except RuntimeError as exc:
        await message.reply(f"Не удалось получить прогноз: {exc}")
        return

    items = data.get("items") or []
    if not items:
        await message.reply("Прогноз пока пуст — пополните остатки или дождитесь данных.")
        return

    await message.reply(_format_forecast(items), parse_mode=ParseMode.MARKDOWN)


@router.message(Command("subscribe"))
async def cmd_subscribe(message: Message) -> None:
    await message.answer(
        "Подписка оформляется через менеджера. Напишите в поддержку, указав свой аккаунт. "
        "После оплаты команда активирует доступ моментально."
    )


# --- Callback ---


@router.callback_query(F.data.startswith("period:"))
async def period_callback(callback: CallbackQuery) -> None:
    period = callback.data.split(":", 1)[1]
    if period == "range":
        await callback.answer("Укажите период в формате YYYY-MM-DD..YYYY-MM-DD", show_alert=True)
        return
    await _send_metrics(callback.message, period)
    await callback.answer()


# --- Entrypoint ---


async def main() -> None:
    bot = Bot(token=settings.telegram_bot_token)
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
