from typing import List, Optional

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.services.mobile import MobileService

router = APIRouter(prefix="/ai", tags=["ai"])
settings = get_settings()


@router.get("/recommendations")
def recommendations(
    request: Request,
    tenant_id: Optional[str] = None,
    session: Session = Depends(get_session),
) -> List[dict]:
    identifier = tenant_id or request.headers.get(settings.tenant_header)
    service = MobileService(session)

    summary = service.get_summary("7d", tenant_id=identifier)
    products_result = service.get_products(limit=20, cursor=0, sort="margin", tenant_id=identifier)
    forecast = service.get_stock_forecast(days=30, limit=20, tenant_id=identifier)

    recs: List[dict] = []

    low_stock = [
        item for item in forecast.items if item.days_left is not None and item.days_left < 7
    ]
    if low_stock:
        sku_list = ", ".join(item.sku for item in low_stock[:3])
        recs.append(
            {
                "title": "Пополните остатки",
                "detail": f"SKU {sku_list} могут закончиться менее чем через неделю.",
                "severity": "high",
            }
        )

    weak_margin = [p for p in products_result.items if p.margin < 0.15]
    if weak_margin:
        recs.append(
            {
                "title": "Проверьте маржу",
                "detail": "У {} SKU маржа ниже 15%, пересмотрите цены или скидки.".format(
                    len(weak_margin)
                ),
                "severity": "medium",
            }
        )

    if summary.roi is not None and summary.roi < 0.2:
        recs.append(
            {
                "title": "Оптимизируйте рекламу",
                "detail": f"ROI за 7 дней всего {summary.roi:.2f}. Сфокусируйтесь на самых конверсионных SKU.",
                "severity": "medium",
            }
        )

    if not recs:
        recs.append(
            {
                "title": "Продолжайте в том же духе",
                "detail": "Серьезных проблем не найдено, наблюдаем дальше.",
                "severity": "low",
            }
        )

    # TODO: заменить на генерацию LLM и добавить кэширование рекомендаций.
    return recs
