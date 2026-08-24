"""Registre synthétique et contrôles des transactions énergétiques."""
from __future__ import annotations

import pandas as pd


TRANSACTION_COLUMNS = [
    "Référence", "Date transaction", "Produit", "Profil", "Début livraison",
    "Fin livraison", "Volume", "Unité volume", "Prix", "Unité prix",
    "Contrepartie / trader", "Collatéral (€)", "Accusé signé",
    "Déclaration RE", "Date déclaration RE", "Statut",
]


def default_transactions() -> pd.DataFrame:
    """Transactions fictives représentatives d'un portefeuille multi-produits."""
    rows = [
        ["TRX-2025-001", "2024-09-12", "Énergie", "Base", "2025-01-01", "2025-03-31", 1.80, "MW", 91.50, "€/MWh", "NorthSea Energy / L. Martin", 185000, True, "Déclarée", "2024-12-20", "Actif"],
        ["TRX-2025-002", "2024-10-03", "Énergie", "Peak", "2025-01-01", "2025-01-31", 0.75, "MW", 108.00, "€/MWh", "Hexa Trading / C. Leroy", 62000, True, "Déclarée", "2024-12-20", "Livré"],
        ["TRX-2025-003", "2024-11-18", "Capacité", "Capacité", "2025-01-01", "2025-12-31", 4.20, "MW", 7.40, "k€/MW", "CapFlex / S. Vidal", 30000, True, "Non applicable", None, "Actif"],
        ["TRX-2025-004", "2024-12-02", "Garantie d'origine", "GO", "2025-01-01", "2025-12-31", 18_000, "MWh", 3.85, "€/MWh", "GreenCert / A. Petit", 0, True, "Non applicable", None, "Actif"],
        ["TRX-2025-005", "2025-01-14", "VNU", "VNU", "2025-02-01", "2025-02-28", 420, "MWh", 76.20, "€/MWh", "Utility Desk / M. Cohen", 18000, False, "En attente", None, "À régulariser"],
        ["TRX-2025-006", "2024-06-25", "PPA", "Base", "2025-01-01", "2027-12-31", 1.10, "MW", 68.00, "€/MWh", "Solaris PPA / E. Roux", 240000, True, "Déclarée", "2024-12-18", "Actif"],
    ]
    df = pd.DataFrame(rows, columns=TRANSACTION_COLUMNS)
    for col in ["Date transaction", "Début livraison", "Fin livraison", "Date déclaration RE"]:
        df[col] = pd.to_datetime(df[col])
    return df


def validate_transactions(df: pd.DataFrame) -> pd.DataFrame:
    """Retourne les anomalies du registre, une ligne par transaction/contrôle."""
    issues: list[dict[str, str]] = []
    required = ["Référence", "Produit", "Début livraison", "Fin livraison", "Volume", "Prix"]
    for idx, row in df.iterrows():
        ref = str(row.get("Référence") or f"Ligne {idx + 1}")
        for col in required:
            if pd.isna(row.get(col)) or row.get(col) == "":
                issues.append({"Référence": ref, "Niveau": "Rouge", "Contrôle": f"Champ obligatoire manquant : {col}"})
        if pd.notna(row.get("Début livraison")) and pd.notna(row.get("Fin livraison")) and row["Fin livraison"] < row["Début livraison"]:
            issues.append({"Référence": ref, "Niveau": "Rouge", "Contrôle": "Fin de livraison antérieure au début"})
        if pd.notna(row.get("Volume")) and float(row["Volume"]) <= 0:
            issues.append({"Référence": ref, "Niveau": "Rouge", "Contrôle": "Volume non positif"})
        if pd.notna(row.get("Prix")) and float(row["Prix"]) < 0:
            issues.append({"Référence": ref, "Niveau": "Orange", "Contrôle": "Prix négatif à confirmer"})
        if not bool(row.get("Accusé signé", False)):
            issues.append({"Référence": ref, "Niveau": "Rouge", "Contrôle": "Accusé de réception trader non signé"})
        if row.get("Produit") in {"Énergie", "PPA", "VNU"} and row.get("Déclaration RE") != "Déclarée":
            issues.append({"Référence": ref, "Niveau": "Orange", "Contrôle": "Livraison non déclarée au RE"})
    return pd.DataFrame(issues, columns=["Référence", "Niveau", "Contrôle"])
