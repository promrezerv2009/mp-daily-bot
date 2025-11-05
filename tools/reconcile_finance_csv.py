#!/usr/bin/env python
"""
Utility script that compares breakdown of operations from the local database
(`ozon_finance_ops`) with the raw Ozon finance CSV export.

Usage:
    python -m tools.reconcile_finance_csv <finance_csv_path> [<date>]

The optional <date> parameter should be in ISO format (YYYY-MM-DD).  When it is
omitted, the date is inferred from the CSV filename (anything that looks like
DD.MM.YYYY-DD.MM.YYYY).  The script prints two tables:
  * sums per `Тип начисления` taken from the CSV file
  * sums per `operation_type_name` stored in the database for the same date

This helps to pinpoint where a particular charge (комиссия, логистика,
кросс-докинг, баллы за скидки, etc.) has landed inside the ETL pipeline.
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable

import pandas as pd
from sqlalchemy import create_engine, text

from app.config import get_settings


def _clean_amount(series: Iterable[str]) -> pd.Series:
    """Convert strings like '1 234,56 ₽' to numeric floats."""
    translation = str.maketrans(
        {
            "\u00a0": "",  # NBSP in many CSV exports
            " ": "",
            ",": ".",
            "\u20bd": "",  # ₽
            "?": "",
        }
    )
    normalized = [str(value).translate(translation) for value in series]
    return pd.to_numeric(pd.Series(normalized), errors="coerce").fillna(0.0)


def _guess_date_from_filename(path: Path) -> str | None:
    match = re.search(r"(\d{2}\.\d{2}\.\d{4})-(\d{2}\.\d{2}\.\d{4})", path.name)
    if not match:
        return None
    return match.group(1)[6:] + "-" + match.group(1)[3:5] + "-" + match.group(1)[:2]


def load_csv_totals(path: Path) -> Dict[str, float]:
    frame = pd.read_csv(path, sep=";", encoding="utf-8-sig", skiprows=1).dropna(how="all")
    type_column = frame.columns[3]
    amount_column = frame.columns[-1]
    frame["amount"] = _clean_amount(frame[amount_column])
    grouped = frame.groupby(type_column)["amount"].sum()
    return {str(key): float(value) for key, value in grouped.items()}


def load_db_totals(target_date: str) -> Dict[str, float]:
    settings = get_settings()
    engine = create_engine(settings.db_url)
    sql = text(
        """
        SELECT payload->>'operation_type_name' AS type_name,
               SUM(amount)                     AS total
        FROM ozon_finance_ops
        WHERE platform = 'ozon' AND op_date = :dt
        GROUP BY type_name
        ORDER BY type_name
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(sql, {"dt": target_date}).fetchall()
    return {row.type_name or "—": float(row.total or 0.0) for row in rows}


def pretty_print(title: str, data: Dict[str, float]) -> None:
    print(f"\n{title}")
    print("-" * len(title))
    for key in sorted(data):
        print(f"{key:>45}: {data[key]:12.2f}")
    print(f"{'Итого':>45}: {sum(data.values()):12.2f}")


def compare(csv_totals: Dict[str, float], db_totals: Dict[str, float]) -> None:
    union_keys = set(csv_totals) | set(db_totals)
    diffs = defaultdict(float)
    for key in union_keys:
        diffs[key] = csv_totals.get(key, 0.0) - db_totals.get(key, 0.0)
    print("\nСводная разница (CSV - DB):")
    print("------------------------------")
    for key in sorted(diffs):
        print(f"{key:>45}: {diffs[key]:12.2f}")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    csv_path = Path(argv[1]).resolve()
    if not csv_path.exists():
        print(f"Файл {csv_path} не найден")
        return 1
    target_date = argv[2] if len(argv) > 2 else _guess_date_from_filename(csv_path)
    if not target_date:
        print("Не удалось определить дату, передайте её вторым аргументом (YYYY-MM-DD).")
        return 1

    csv_totals = load_csv_totals(csv_path)
    db_totals = load_db_totals(target_date)

    pretty_print("CSV: суммы по типам начислений", csv_totals)
    pretty_print("DB: суммы по operation_type_name", db_totals)
    compare(csv_totals, db_totals)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
