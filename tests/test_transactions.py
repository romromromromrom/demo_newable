import pandas as pd

from transaction_data import default_transactions, validate_transactions


def test_default_transaction_register_contains_all_products():
    products = set(default_transactions()["Produit"])
    assert {"Énergie", "Capacité", "Garantie d'origine", "VNU", "PPA"}.issubset(products)


def test_transaction_controls_detect_unsigned_and_re_pending():
    issues = validate_transactions(default_transactions())
    vnu = issues[issues["Référence"].eq("TRX-2025-005")]
    assert len(vnu) == 2
    assert vnu["Contrôle"].str.contains("non signé").any()
    assert vnu["Contrôle"].str.contains("RE").any()


def test_transaction_controls_detect_invalid_delivery_dates():
    data = default_transactions().head(1)
    data.loc[data.index[0], "Fin livraison"] = pd.Timestamp("2024-01-01")
    assert validate_transactions(data)["Contrôle"].str.contains("antérieure").any()
