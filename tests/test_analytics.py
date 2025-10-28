from datetime import date

from app.services.analytics import MetricAccumulator, calculate_daily_metrics, summarize


def test_calculate_daily_metrics_basic():
    orders = [
        {
            "id": "ozon-1",
            "platform": "ozon",
            "date": date(2024, 5, 1),
            "sku": "SKU1",
            "price": 1000.0,
            "qty_ordered": 1,
            "qty_delivered": 1,
            "qty_returned": 0,
        },
        {
            "id": "wb-1",
            "platform": "wb",
            "date": date(2024, 5, 1),
            "sku": "SKU2",
            "price": 500.0,
            "qty_ordered": 2,
            "qty_delivered": 2,
            "qty_returned": 0,
        },
    ]
    costs = [
        {
            "platform": "ozon",
            "sku": "SKU1",
            "date": date(2024, 5, 1),
            "commission_fee": 50.0,
            "logistics_fee": 30.0,
            "storage_fee": 20.0,
            "cogs_per_unit": 400.0,
        },
        {
            "platform": "wb",
            "sku": "SKU2",
            "date": date(2024, 5, 1),
            "commission_fee": 40.0,
            "logistics_fee": 10.0,
            "storage_fee": 0.0,
            "cogs_per_unit": 150.0,
        },
    ]
    ads = [
        {"platform": "ozon", "sku": "SKU1", "date": date(2024, 5, 1), "amount": 100.0},
        {"platform": "wb", "sku": None, "date": date(2024, 5, 1), "amount": 80.0},
    ]

    metrics = calculate_daily_metrics(orders, costs, ads)
    assert metrics["ozon"][date(2024, 5, 1)].revenue_delivered == 1000.0
    assert metrics["ozon"][date(2024, 5, 1)].ads == 100.0
    assert metrics["wb"][date(2024, 5, 1)].ads == 80.0

    summary = summarize(metrics)
    assert summary["ozon"]["profit"] == 1000.0 - 400.0 - 50.0 - 30.0 - 20.0 - 100.0
    assert summary["wb"]["ads"] == 80.0
    assert summary["total"]["delivered"] == 3
    assert summary["total"]["romi"] is not None


def test_metric_accumulator_romi_none_when_no_ads():
    acc = MetricAccumulator()
    acc.register_order(
        qty_ordered=1,
        qty_delivered=1,
        qty_returned=0,
        price=100.0,
        cogs_per_unit=50.0,
        commission_fee=10.0,
        logistics_fee=5.0,
        storage_fee=0.0,
        ads_amount=0.0,
    )
    assert acc.romi is None
