from datetime import date, datetime, timedelta
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

from app.schemas import PeriodEnum


def parse_period_argument(argument: Optional[str], tz: ZoneInfo) -> Tuple[date, date, PeriodEnum]:
    today = datetime.now(tz).date()
    if not argument or argument.lower() == PeriodEnum.yesterday.value:
        target = today - timedelta(days=1)
        return target, target, PeriodEnum.yesterday

    argument = argument.strip()
    if argument in {PeriodEnum.last_7.value, PeriodEnum.last_14.value}:
        delta = int(argument)
        start = today - timedelta(days=delta)
        end = today - timedelta(days=1)
        return start, end, PeriodEnum.last_7 if delta == 7 else PeriodEnum.last_14

    if argument.lower() == PeriodEnum.month.value:
        start = today.replace(day=1)
        end = today
        return start, end, PeriodEnum.month

    if ".." in argument:
        left, right = argument.split("..", 1)
        start = datetime.strptime(left.strip(), "%Y-%m-%d").date()
        end = datetime.strptime(right.strip(), "%Y-%m-%d").date()
        return start, end, PeriodEnum.custom

    raise ValueError("Invalid period argument")


def resolve_period(
    period: Optional[str],
    date_from: Optional[date],
    date_to: Optional[date],
    tz: ZoneInfo,
) -> Tuple[date, date, PeriodEnum]:
    if date_from and date_to:
        return date_from, date_to, PeriodEnum.custom
    if period:
        return parse_period_argument(period, tz)
    return parse_period_argument(None, tz)
