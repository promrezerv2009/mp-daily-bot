import asyncio
import os
import aiohttp
from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.config import get_settings
from app.services.rbac import Role, get_role

settings = get_settings()
router = Router()

# адрес API FastAPI (сервис "app" в docker-compose)
API_BASE = os.getenv("API_BASE_URL", "http://app:8000")

# --- UI ---

PERIOD_BUTTONS = [
    [InlineKeyboardButton(text="Вчера", callback_data="period:yesterday"),
     InlineKeyboardButton(text="7 дней", callback_data="period:7")],
    [InlineKeyboardButton(text="14 дней", callback_data="period:14"),
     InlineKeyboardButton(text="Месяц", callback_data="period:month")],
    [InlineKeyboardButton(text="Диапазон", callback_data="period:range")],
]

def period_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=PERIOD_BUTTONS)

def _fmt_money(x):
    try:
        return f"{float(x):,.2f}".replace(",", " ").replace(".00", ",0").replace(".", ",")
    except Exception:
        return str(x)

def _title_for(period: str) -> str:
    m = {
        "yesterday": "за вчера",
        "7d": "за 7 дней",
        "14d": "за 14 дней",
        "30d": "за месяц",
        "7": "за 7 дней",
        "14": "за 14 дней",
        "month": "за месяц",
        "вчера": "за вчера",
    }
    p = (period or "").strip().lower()
    return m.get(p, f"за период {period}")

# --- Команды ---

@router.message(Command("start", "help"))
async def cmd_help(message: Message) -> None:
    role = get_role(message.chat.id)
    commands = [
        "/yesterday",
        "/orders [period]",
        "/ads [period]",
        "/topsku [period]",
        "/export [period]",
    ]
    if role == Role.admin:
        commands.append("/refresh [period|YYYY-MM-DD..YYYY-MM-DD]")
    text = (
        "Привет! Я mp-daily-bot.\n"
        "Доступные команды:\n"
        + "\n".join(commands)
        + "\nПериоды: yesterday, 7, 14, month или диапазон YYYY-MM-DD..YYYY-MM-DD."
    )
    await message.answer(text, reply_markup=period_keyboard())

@router.message(Command("yesterday"))
async def cmd_yesterday(message: Message):
    await send_metrics(message, "yesterday")

@router.message(Command("orders"))
async def cmd_orders(message: Message):
    # пока используем ту же сводку; позже сделаем отдельный ответ с заказами
    period = "yesterday"
    if message.text and len(message.text.split()) > 1:
        period = message.text.split(maxsplit=1)[1]
    await send_metrics(message, period)

# --- Общая функция отправки метрик ---

async def send_metrics(message: Message, period: str):
    url = f"{API_BASE}/api/metrics?period={period}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=20) as resp:
                data = await resp.json()
    except Exception as e:
        await message.reply(f"⚠️ Ошибка запроса к API: {e}")
        return

    if not data.get("ok"):
        await message.reply("Нет данных.")
        return

    s = data["summary"]
    title = _title_for(period)
    text = (
        f"📊 *Сводка {title}*\n\n"
        f"💰 *Выручка:* { _fmt_money(s['revenue_delivered']) } ₽\n"
        f"📦 Заказы: {s['orders']}  |  Доставлено: {s['delivered']}  |  Возвраты: {s['returns']}\n"
        f"📣 Реклама: { _fmt_money(s['ads']) } ₽\n"
        f"💸 Комиссия: { _fmt_money(s['commission']) } ₽  |  Логистика: { _fmt_money(s['logistics']) } ₽  |  Хранение: { _fmt_money(s['storage']) } ₽\n"
        f"🏭 Себестоимость: { _fmt_money(s['cogs']) } ₽\n\n"
        f"💵 *Прибыль:* { _fmt_money(s['profit']) } ₽\n"
        f"📈 ROMI: { s['romi'] if s['romi'] is not None else '—' }"
    )
    await message.reply(text, parse_mode=ParseMode.MARKDOWN, reply_markup=period_keyboard())

# --- Callback с выбором периода ---

@router.callback_query(F.data.startswith("period:"))
async def period_callback(callback: CallbackQuery):
    period = callback.data.split(":", 1)[1]
    if period == "range":
        await callback.answer(
            "Введите диапазон в формате YYYY-MM-DD..YYYY-MM-DD", show_alert=True
        )
        return
    # не мутируем callback.message — просто отправляем сводку в тот же чат
    await send_metrics(callback.message, period)
    await callback.answer()

# --- Точка входа ---

async def main():
    bot = Bot(token=settings.telegram_bot_token)
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
