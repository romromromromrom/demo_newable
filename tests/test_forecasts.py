import numpy as np
import pandas as pd

from forecast_calculations import (PROVIDERS, barycentric_forecast,
    calculate_balancing_pnl, generate_imbalance_prices,
    generate_provider_forecasts, inverse_mape_weights)


def test_forecast_status_and_barycentre():
    idx = pd.date_range("2025-01-01", periods=48, freq="h")
    actual = pd.Series(10 + np.sin(np.arange(48) / 4), index=idx)
    forecasts = generate_provider_forecasts(actual)
    assert forecasts["LoadSense"].isna().all()
    weights = inverse_mape_weights(actual, forecasts)
    barycentre, normalized = barycentric_forecast(forecasts, weights)
    assert np.isclose(sum(normalized.values()), 1)
    assert barycentre.notna().all()
    assert set(PROVIDERS) == set(forecasts)


def test_balancing_pnl_hourly_reconciles():
    idx = pd.date_range("2025-01-01", periods=3, freq="h")
    actual = pd.Series([10., 12., 8.], index=idx)
    forecast = pd.Series([10., 10., 10.], index=idx)
    spot = pd.Series(100., index=idx)
    prices = pd.DataFrame({"up_price": 130., "down_price": 70.}, index=idx)
    hourly, summary = calculate_balancing_pnl(actual, forecast, spot, prices)
    assert np.isclose(hourly.balancing_pnl.sum(), summary["pnl"])
    assert summary["pnl"] < 0
    assert np.isclose(summary["short_mwh"], 2)
    assert np.isclose(summary["long_mwh"], 2)


def test_imbalance_prices_surround_spot():
    idx = pd.date_range("2025-01-01", periods=240, freq="h")
    spot = pd.Series(100., index=idx)
    prices = generate_imbalance_prices(spot)
    assert (prices.up_price > spot).all()
    assert (prices.down_price < spot).all()
