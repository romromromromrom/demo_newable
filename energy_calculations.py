"""Calculs de couverture, coûts, flexibilité et allocation des factures."""
from __future__ import annotations

import numpy as np
import pandas as pd

from data_generation import USAGES


def monthly_blocks(index: pd.DatetimeIndex, powers_mw: dict[int, float], prices: dict[int, float]) -> pd.DataFrame:
    months = index.month.to_numpy()
    return pd.DataFrame({
        "block_mwh": [max(0.0, powers_mw.get(int(m), 0.0)) for m in months],
        "block_price": [max(0.0, prices.get(int(m), 0.0)) for m in months],
    }, index=index)


def calculate_cost(load_kwh: pd.Series, spot: pd.Series, blocks: pd.DataFrame,
                   surplus_mode: str = "take_or_pay") -> tuple[pd.DataFrame, dict[str, float]]:
    """Calcule les flux horaires en MWh et les coûts en euros."""
    load = load_kwh.astype(float) / 1000
    block = blocks["block_mwh"].reindex(load.index).fillna(0.0)
    price = spot.reindex(load.index)
    if price.isna().any() or (load < 0).any() or (block < 0).any():
        raise ValueError("Séries invalides ou incomplètes.")
    spot_buy = (load - block).clip(lower=0)
    surplus = (block - load).clip(lower=0)
    block_cost = block * blocks["block_price"].reindex(load.index)
    spot_cost = spot_buy * price
    resale = surplus * price if surplus_mode == "resale" else surplus * 0
    hourly = pd.DataFrame({"load_mwh": load, "block_mwh": block, "spot_buy_mwh": spot_buy,
                           "surplus_mwh": surplus, "block_cost": block_cost,
                           "spot_cost": spot_cost, "resale_revenue": resale})
    hourly["total_cost"] = hourly.block_cost + hourly.spot_cost - hourly.resale_revenue
    energy = hourly.load_mwh.sum()
    covered = np.minimum(hourly.load_mwh, hourly.block_mwh).sum()
    summary = {
        "energy_mwh": energy, "block_cost": hourly.block_cost.sum(),
        "spot_cost": hourly.spot_cost.sum(), "resale_revenue": hourly.resale_revenue.sum(),
        "total_cost": hourly.total_cost.sum(),
        "average_price": hourly.total_cost.sum() / energy if energy else 0,
        "coverage_rate": covered / energy if energy else 0,
        "spot_volume_mwh": hourly.spot_buy_mwh.sum(), "surplus_mwh": hourly.surplus_mwh.sum(),
        "peak_mw": hourly.load_mwh.max(),
    }
    return hourly, summary


def optimize_flexibility(data: pd.DataFrame, spot: pd.Series, blocks: pd.DataFrame,
                         usages: list[str], buildings: list[str], share: float,
                         mode: str = "auto", shift: int = 1,
                         allow_cross_day: bool = False) -> tuple[pd.DataFrame, dict]:
    """Déplace une fraction des usages vers h±1, sans créer/détruire d'énergie."""
    out = data.copy()
    moved = 0.0
    movements: dict[tuple[int, int], float] = {}
    residual_price = spot.copy()
    # Sous bloc, une heure supplémentaire n'a pas de coût marginal; au-delà: spot.
    total = data.groupby("timestamp")[USAGES].sum().sum(axis=1) / 1000
    residual_price.loc[total <= blocks["block_mwh"]] = 0
    for building in buildings:
        mask_b = out["building"].eq(building)
        positions = np.flatnonzero(mask_b.to_numpy())
        if not len(positions):
            continue
        timestamps = pd.DatetimeIndex(out.loc[mask_b, "timestamp"])
        for usage in usages:
            if usage not in USAGES:
                continue
            values = out.loc[mask_b, usage].to_numpy(copy=True)
            delta = np.zeros_like(values)
            for i in range(len(values)):
                candidates = [j for j in (i - 1, i, i + 1) if 0 <= j < len(values)]
                if usage in {"Chauffage", "PAC", "CVC", "Climatisation"} and not allow_cross_day:
                    candidates = [j for j in candidates if timestamps[j].date() == timestamps[i].date()]
                if mode == "manual":
                    target = i + shift
                    if target not in candidates:
                        continue
                else:
                    target = min(candidates, key=lambda j: float(residual_price.loc[timestamps[j]]))
                    if residual_price.loc[timestamps[target]] >= residual_price.loc[timestamps[i]]:
                        continue
                amount = values[i] * np.clip(share, 0, 1)
                if target != i and amount > 0:
                    delta[i] -= amount
                    delta[target] += amount
                    moved += amount
                    movements[(timestamps[i].hour, timestamps[target].hour)] = movements.get((timestamps[i].hour, timestamps[target].hour), 0) + amount
            out.loc[mask_b, usage] = np.maximum(0, values + delta)
    top = sorted(movements.items(), key=lambda x: x[1], reverse=True)[:5]
    return out, {"moved_mwh": moved / 1000, "top_movements": top}


def aggregate_load(data: pd.DataFrame) -> pd.DataFrame:
    return data.groupby("timestamp", as_index=True)[USAGES].sum().sort_index()


def monthly_invoices(data: pd.DataFrame, hourly_cost: pd.DataFrame,
                     baseline_cost: pd.DataFrame | None = None) -> pd.DataFrame:
    """Alloue les coûts chaque heure au prorata de la consommation bâtiment."""
    work = data.copy()
    work["consumption_kwh"] = work[USAGES].sum(axis=1)
    totals = work.groupby("timestamp")["consumption_kwh"].transform("sum")
    work["share"] = np.divide(work.consumption_kwh, totals, out=np.zeros(len(work)), where=totals > 0)
    costs = hourly_cost[["block_cost", "spot_cost", "resale_revenue", "total_cost"]]
    for col in costs:
        work[col] = work["share"] * work["timestamp"].map(costs[col])
    if baseline_cost is not None:
        work["savings"] = work["share"] * work["timestamp"].map(baseline_cost.total_cost - hourly_cost.total_cost)
    else:
        work["savings"] = 0.0
    work["month"] = work.timestamp.dt.to_period("M").astype(str)
    agg = {u: "sum" for u in USAGES}
    agg.update({"consumption_kwh": "sum", "block_cost": "sum", "spot_cost": "sum",
                "resale_revenue": "sum", "total_cost": "sum", "savings": "sum"})
    result = work.groupby(["building", "month"], as_index=False).agg(agg)
    result["consumption_mwh"] = result.pop("consumption_kwh") / 1000
    result["average_price"] = np.divide(result.total_cost, result.consumption_mwh,
                                         out=np.zeros(len(result)), where=result.consumption_mwh > 0)
    return result
