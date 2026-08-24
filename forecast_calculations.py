"""Prévisions court terme simulées et calcul du coût des écarts."""
from __future__ import annotations

import numpy as np
import pandas as pd


PROVIDERS = {
    "MétéoWatt": {"status": "Reçue", "quality": 0.030, "bias": 0.004, "seed": 101},
    "GridCast": {"status": "Reçue", "quality": 0.042, "bias": -0.008, "seed": 202},
    "EnerVision": {"status": "Dégradée", "quality": 0.060, "bias": 0.015, "seed": 303},
    "LoadSense": {"status": "En retard", "quality": 0.050, "bias": 0.000, "seed": 404},
}


def generate_provider_forecasts(actual_mwh: pd.Series) -> pd.DataFrame:
    """Crée des prévisions day-ahead plausibles à partir du réalisé.

    Le prestataire en retard ne livre aucune valeur et reste exclu du barycentre.
    """
    actual = actual_mwh.astype(float)
    smooth = actual.rolling(3, center=True, min_periods=1).mean()
    forecasts = pd.DataFrame(index=actual.index)
    scale = max(float(actual.mean()), 1e-6)
    hours = np.arange(len(actual))
    for name, spec in PROVIDERS.items():
        if spec["status"] == "En retard":
            forecasts[name] = np.nan
            continue
        rng = np.random.default_rng(spec["seed"])
        correlated = np.convolve(rng.normal(0, 1, len(actual)), np.ones(5) / 5, mode="same")
        daily_error = np.sin(2 * np.pi * hours / 24 + spec["seed"] / 100) * spec["quality"] * .35
        error = spec["bias"] + daily_error + correlated * spec["quality"]
        forecasts[name] = np.maximum(0, smooth * (1 + error) + rng.normal(0, scale * .008, len(actual)))
    return forecasts


def generate_imbalance_prices(spot: pd.Series, seed: int = 818,
                              spread: float = 22.0,
                              jump_intensity: float = 1.0) -> pd.DataFrame:
    """Génère les prix d'activation à la hausse et à la baisse en €/MWh."""
    rng = np.random.default_rng(seed)
    n = len(spot)
    up = spot.to_numpy(float) + spread + rng.normal(0, spread * .28, n)
    down = spot.to_numpy(float) - spread + rng.normal(0, spread * .28, n)
    jump_count = max(2, int(n / 120 * jump_intensity))
    up_ids = rng.choice(n, min(jump_count, n), replace=False)
    down_ids = rng.choice(n, min(jump_count, n), replace=False)
    up[up_ids] += rng.uniform(70, 240, len(up_ids)) * jump_intensity
    down[down_ids] -= rng.uniform(70, 190, len(down_ids)) * jump_intensity
    # Le prix d'achat à la hausse reste au-dessus du spot et celui de vente en dessous.
    up = np.maximum(up, spot.to_numpy(float) + 1)
    down = np.minimum(down, spot.to_numpy(float) - 1)
    return pd.DataFrame({"up_price": up, "down_price": down}, index=spot.index)


def mae_table(actual: pd.Series, forecasts: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for provider, spec in PROVIDERS.items():
        prediction = forecasts.get(provider)
        available = prediction is not None and prediction.notna().any()
        mae = float((prediction - actual).abs().mean()) if available else np.nan
        mape = float(((prediction - actual).abs() / actual.replace(0, np.nan)).mean() * 100) if available else np.nan
        rows.append({"Prestataire": provider, "Statut": spec["status"], "MAE (MW)": mae,
                     "MAPE (%)": mape, "Disponible": available})
    return pd.DataFrame(rows)


def barycentric_forecast(forecasts: pd.DataFrame, weights: dict[str, float]) -> tuple[pd.Series, dict[str, float]]:
    """Normalise les poids disponibles et retourne leur combinaison convexe."""
    available = [c for c in forecasts if forecasts[c].notna().any() and weights.get(c, 0) >= 0]
    raw = np.array([weights.get(c, 0.0) for c in available], dtype=float)
    if not available or raw.sum() <= 0:
        raise ValueError("Au moins un poids positif est requis pour une prévision disponible.")
    normalized = raw / raw.sum()
    result = forecasts[available].mul(normalized, axis=1).sum(axis=1)
    return result.rename("Prévision barycentrique"), dict(zip(available, normalized))


def inverse_mae_weights(actual: pd.Series, forecasts: pd.DataFrame) -> dict[str, float]:
    errors = (forecasts.subtract(actual, axis=0)).abs().mean().dropna().clip(lower=1e-9)
    inverse = 1 / errors
    return (inverse / inverse.sum()).to_dict()


def inverse_mape_weights(actual: pd.Series, forecasts: pd.DataFrame) -> dict[str, float]:
    """Poids normalisés inversement proportionnels à la MAPE."""
    denominator = actual.replace(0, np.nan)
    errors = forecasts.subtract(actual, axis=0).abs().divide(denominator, axis=0).mean().dropna().clip(lower=1e-9)
    inverse = 1 / errors
    return (inverse / inverse.sum()).to_dict()


def calculate_balancing_pnl(actual_mwh: pd.Series, forecast_mwh: pd.Series,
                            spot: pd.Series, imbalance_prices: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, float]]:
    """Valorise l'écart entre nomination prévisionnelle et consommation réalisée.

    P&L = coût théorique du réalisé au spot - coût nomination + règlement des écarts.
    Un P&L négatif représente un surcoût de balancing.
    """
    common_index = actual_mwh.index.intersection(forecast_mwh.index).intersection(spot.index).intersection(imbalance_prices.index)
    actual = actual_mwh.reindex(common_index).astype(float)
    forecast = forecast_mwh.reindex(common_index).astype(float)
    spot = spot.reindex(common_index).astype(float)
    prices = imbalance_prices.reindex(actual.index)
    if forecast.isna().any() or prices.isna().any().any() or spot.isna().any():
        raise ValueError("Séries de balancing incomplètes.")
    short = (actual - forecast).clip(lower=0)
    long = (forecast - actual).clip(lower=0)
    nominated_cost = forecast * spot
    up_cost = short * prices.up_price
    down_revenue = long * prices.down_price
    settlement_cost = nominated_cost + up_cost - down_revenue
    reference_cost = actual * spot
    hourly = pd.DataFrame({"actual_mwh": actual, "forecast_mwh": forecast,
                           "short_mwh": short, "long_mwh": long, "spot": spot,
                           "up_price": prices.up_price, "down_price": prices.down_price,
                           "reference_cost": reference_cost, "settlement_cost": settlement_cost})
    hourly["balancing_pnl"] = hourly.reference_cost - hourly.settlement_cost
    summary = {"mae_mw": float((actual - forecast).abs().mean()),
               "short_mwh": float(short.sum()), "long_mwh": float(long.sum()),
               "pnl": float(hourly.balancing_pnl.sum()),
               "balancing_cost": float(-hourly.balancing_pnl.sum())}
    return hourly, summary
