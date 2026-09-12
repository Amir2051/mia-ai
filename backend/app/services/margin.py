from decimal import Decimal
from typing import Optional


class InvalidPriceError(Exception):
    pass


def calculate_margin(cost: float, retail_price: float) -> dict:
    """
    Deterministic margin calculation.

    Returns:
        dict with cost, retail_price, gross_profit, margin_percent
    """
    cost_dec = Decimal(str(cost))
    price_dec = Decimal(str(retail_price))

    if cost_dec < 0 or price_dec < 0:
        raise InvalidPriceError('Cost and price must be non-negative')

    gross_profit = price_dec - cost_dec
    margin_percent = (gross_profit / price_dec * 100) if price_dec > 0 else Decimal('0')

    return {
        'cost': float(cost_dec),
        'retail_price': float(price_dec),
        'gross_profit': float(gross_profit),
        'margin_percent': float(margin_percent.quantize(Decimal('0.01'))),
    }


def batch_margins(items: list[dict]) -> list[dict]:
    """
    Batch margin calculation for a list of items with cost/price.
    Each item should include 'cost' and 'retail_price'.
    """
    results = []
    for item in items:
        try:
            result = calculate_margin(item['cost'], item['retail_price'])
        except InvalidPriceError as exc:
            result = {'error': str(exc), 'input': item}
        results.append(result)
    return results
