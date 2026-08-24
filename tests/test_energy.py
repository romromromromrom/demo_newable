import numpy as np
import pandas as pd

from data_generation import generate_portfolio, generate_spot_prices, USAGES
from energy_calculations import (aggregate_load, calculate_cost, monthly_blocks,
                                 monthly_invoices, optimize_flexibility)


def sample():
    idx = pd.date_range("2025-01-01", periods=24, freq="h")
    load = pd.Series(1000.0, index=idx)
    blocks = monthly_blocks(idx, {1: .8}, {1: 100})
    spot = pd.Series(120.0, index=idx)
    return idx, load, blocks, spot


def test_block_and_spot_cost():
    _, load, blocks, spot = sample()
    hourly, summary = calculate_cost(load, spot, blocks)
    assert np.isclose(hourly.block_cost.sum(), 24 * .8 * 100)
    assert np.isclose(hourly.spot_buy_mwh.sum(), 24 * .2)
    assert np.isclose(summary["spot_cost"], 24 * .2 * 120)


def test_surplus_resale():
    idx, load, _, spot = sample()
    blocks = monthly_blocks(idx, {1: 1.2}, {1: 100})
    hourly, summary = calculate_cost(load, spot, blocks, "resale")
    assert np.isclose(summary["surplus_mwh"], 4.8)
    assert np.isclose(summary["resale_revenue"], 4.8 * 120)


def test_flex_energy_conservation_and_nonnegative():
    data = generate_portfolio(2025).query("building == 'Horizon'").head(72).copy()
    load = aggregate_load(data)
    spot = pd.Series(np.tile([100, 20, 80], 24), index=load.index)
    blocks = monthly_blocks(load.index, {1: 0}, {1: 0})
    optimized, info = optimize_flexibility(data, spot, blocks, ["EV", "PAC"], ["Horizon"], .3)
    assert np.isclose(data[USAGES].sum().sum(), optimized[USAGES].sum().sum())
    assert (optimized[USAGES] >= 0).all().all()
    assert info["moved_mwh"] >= 0


def test_hourly_monthly_annual_consistency():
    data = generate_portfolio(2025).query("building in ['Horizon', 'Alizé']")
    load = aggregate_load(data)
    spot = generate_spot_prices(load.index)
    blocks = monthly_blocks(load.index, {m: .1 for m in range(1,13)}, {m: 90 for m in range(1,13)})
    hourly, summary = calculate_cost(load.sum(axis=1), spot, blocks)
    invoices = monthly_invoices(data, hourly)
    assert np.isclose(hourly.total_cost.sum(), summary["total_cost"])
    assert np.isclose(invoices.total_cost.sum(), summary["total_cost"])


def test_peak_offpeak_parameter_effect():
    idx, load, blocks, _ = sample()
    low = generate_spot_prices(idx, 80, 50, 0, seed=1, extreme_intensity=0)
    high = generate_spot_prices(idx, 160, 100, 0, seed=1, extreme_intensity=0)
    assert high.mean() > low.mean()
    assert calculate_cost(load, high, blocks)[1]["total_cost"] > calculate_cost(load, low, blocks)[1]["total_cost"]


def test_default_hourly_shapes_are_plausible():
    weekday = pd.date_range("2025-01-06", periods=24, freq="h")  # lundi
    weekend = pd.date_range("2025-01-11", periods=24, freq="h")  # samedi
    wd = generate_spot_prices(weekday, 105, 72, 0, seed=1, extreme_intensity=0)
    we = generate_spot_prices(weekend, 105, 72, 0, seed=1, extreme_intensity=0)
    assert wd.iloc[18] > wd.iloc[3]  # pointe du soir > creux nocturne
    assert we.iloc[18] > we.iloc[3]
    assert wd.mean() > we.mean()     # semaine plus chère que week-end


def test_custom_hourly_shape_is_applied():
    idx = pd.date_range("2025-01-06", periods=24, freq="h")
    flat = tuple([0.0] * 24)
    shaped = list(flat)
    shaped[12] = 40.0
    base = generate_spot_prices(idx, 100, 100, 0, 1, 0, flat, flat)
    custom = generate_spot_prices(idx, 100, 100, 0, 1, 0, tuple(shaped), flat)
    assert np.isclose(custom.iloc[12] - base.iloc[12], 40.0)
