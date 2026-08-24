"""Génération reproductible de profils énergétiques tertiaires synthétiques."""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd

USAGES = ["EV", "Chauffage", "CVC", "PAC", "Climatisation", "Autres"]


@dataclass(frozen=True)
class Building:
    name: str
    city: str
    kind: str
    area_m2: int
    heat: str


BUILDINGS = [
    Building("Horizon", "Paris", "Bureaux", 18_000, "PAC"),
    Building("Alizé", "Lyon", "Bureaux", 12_500, "Électrique"),
    Building("Agora", "Lille", "Centre commercial", 24_000, "PAC"),
    Building("Méridien", "Bordeaux", "Bureaux", 9_500, "Électrique"),
    Building("Canopée", "Nantes", "Campus", 21_000, "PAC"),
    Building("Prisme", "Marseille", "Bureaux", 14_000, "PAC"),
]


def _temperature(index: pd.DatetimeIndex, cold_factor: float, rng: np.random.Generator) -> np.ndarray:
    day = index.dayofyear.to_numpy()
    hour = index.hour.to_numpy()
    seasonal = 13 + 9 * np.sin(2 * np.pi * (day - 172) / 365)
    daily = 2.2 * np.sin(2 * np.pi * (hour - 14) / 24)
    return seasonal + daily + rng.normal(0, 1.5, len(index)) - (cold_factor - 1) * 4


def generate_portfolio(year: int = 2025, seed: int = 42, cold_factor: float = 1.0) -> pd.DataFrame:
    """Retourne une table longue bâtiment/heure, en kWh par pas horaire."""
    idx = pd.date_range(f"{year}-01-01", f"{year + 1}-01-01", inclusive="left", freq="h")
    frames: list[pd.DataFrame] = []
    for number, b in enumerate(BUILDINGS):
        rng = np.random.default_rng(seed + number * 101)
        temp = _temperature(idx, cold_factor, rng)
        hour = idx.hour.to_numpy()
        weekday = (idx.dayofweek < 5).astype(float)
        open_hours = ((hour >= 7) & (hour < 20)).astype(float)
        occupancy = (0.14 + 0.86 * weekday * open_hours) * (0.9 + 0.1 * np.sin(np.pi * np.clip((hour - 7) / 13, 0, 1)))
        scale = b.area_m2 / 10_000
        heating_need = np.clip(17 - temp, 0, None) / 17
        cooling_need = np.clip(temp - 21, 0, None) / 12
        noise = np.clip(rng.normal(1, 0.045, len(idx)), 0.82, 1.18)

        other = scale * (24 + 35 * occupancy) * noise
        cvc = scale * (4 + 31 * occupancy) * (1 + 0.15 * cooling_need) * noise
        ev = scale * weekday * np.exp(-0.5 * ((hour - 10) / 2.3) ** 2) * 29 * noise
        climate = scale * cooling_need * (8 + 48 * occupancy) * noise
        if b.heat == "PAC":
            pac = scale * heating_need * (8 + 46 * occupancy) * noise
            heating = np.zeros(len(idx))
        else:
            heating = scale * heating_need * (9 + 65 * occupancy) * noise
            pac = np.zeros(len(idx))

        frame = pd.DataFrame({
            "timestamp": idx, "building": b.name, "city": b.city, "type": b.kind,
            "area_m2": b.area_m2, "temperature_c": temp, "EV": ev,
            "Chauffage": heating, "CVC": cvc, "PAC": pac,
            "Climatisation": climate, "Autres": other,
        })
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def generate_spot_prices(index: pd.DatetimeIndex, peak_mean: float = 105.0,
                         offpeak_mean: float = 72.0, volatility: float = 18.0,
                         seed: int = 7, extreme_intensity: float = 1.0) -> pd.Series:
    """Prix spot synthétique en €/MWh. Peak = lun-ven, 08:00-20:00."""
    rng = np.random.default_rng(seed)
    peak = (index.dayofweek < 5) & (index.hour >= 8) & (index.hour < 20)
    base = np.where(peak, peak_mean, offpeak_mean).astype(float)
    winter = 11 * np.cos(2 * np.pi * (index.dayofyear.to_numpy() - 15) / 365)
    price = base + winter + rng.normal(0, volatility, len(index))
    n_spikes = max(3, int(15 * extreme_intensity))
    spike_ids = rng.choice(len(index), n_spikes, replace=False)
    price[spike_ids] += rng.uniform(100, 320, n_spikes) * extreme_intensity
    low_ids = rng.choice(len(index), max(3, int(12 * extreme_intensity)), replace=False)
    price[low_ids] -= rng.uniform(80, 150, len(low_ids)) * extreme_intensity
    return pd.Series(np.clip(price, -120, 650), index=index, name="spot_eur_mwh")


def validate_import(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise un CSV au format timestamp, building et colonnes d'usages (kWh)."""
    required = {"timestamp", "building", *USAGES}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"Colonnes manquantes : {', '.join(sorted(missing))}")
    out = df.copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"], errors="raise")
    if out[list(USAGES)].isna().any().any() or (out[list(USAGES)] < 0).any().any():
        raise ValueError("Les consommations doivent être positives et sans valeur manquante.")
    return out.sort_values(["timestamp", "building"])
