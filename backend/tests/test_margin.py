import pytest

from app.services.margin import (
    InvalidPriceError,
    batch_margins,
    calculate_margin,
)


def test_calculate_margin():
    result = calculate_margin(40, 100)

    assert result["cost"] == 40.0
    assert result["retail_price"] == 100.0
    assert result["gross_profit"] == 60.0
    assert result["margin_percent"] == 60.0


def test_calculate_margin_zero_price():
    result = calculate_margin(10, 0)

    assert result["gross_profit"] == -10.0
    assert result["margin_percent"] == 0.0


def test_calculate_margin_rejects_negative_cost():
    with pytest.raises(InvalidPriceError):
        calculate_margin(-1, 100)


def test_calculate_margin_rejects_negative_price():
    with pytest.raises(InvalidPriceError):
        calculate_margin(10, -1)


def test_batch_margins():
    results = batch_margins(
        [
            {"cost": 20, "retail_price": 50},
            {"cost": 50, "retail_price": 100},
        ]
    )

    assert len(results) == 2
    assert results[0]["gross_profit"] == 30.0
    assert results[0]["margin_percent"] == 60.0
    assert results[1]["gross_profit"] == 50.0
    assert results[1]["margin_percent"] == 50.0


def test_batch_margins_handles_invalid_price():
    results = batch_margins(
        [
            {"cost": -5, "retail_price": 100},
        ]
    )

    assert len(results) == 1
    assert "error" in results[0]
    assert results[0]["input"]["cost"] == -5
