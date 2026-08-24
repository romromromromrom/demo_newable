"""EnergyPilot — démonstrateur Streamlit de pilotage énergétique tertiaire."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from data_generation import (BUILDINGS, USAGES, WEEKDAY_HOURLY_SHAPE,
                             WEEKEND_HOURLY_SHAPE, generate_portfolio,
                             generate_spot_prices, validate_import)
from energy_calculations import (aggregate_load, calculate_cost, monthly_blocks,
                                 monthly_invoices, optimize_flexibility)
from forecast_calculations import (PROVIDERS, barycentric_forecast,
    calculate_balancing_pnl, generate_imbalance_prices,
    generate_provider_forecasts, inverse_mape_weights, mae_table)

st.set_page_config(page_title="EnergyPilot", page_icon="⚡", layout="wide")

COLORS = {"EV": "#8B5CF6", "Chauffage": "#EF4444", "CVC": "#06B6D4",
          "PAC": "#F59E0B", "Climatisation": "#3B82F6", "Autres": "#64748B"}
MONTHS = {1: "Jan", 2: "Fév", 3: "Mar", 4: "Avr", 5: "Mai", 6: "Juin",
          7: "Juil", 8: "Août", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Déc"}
SCENARIOS = {
    "Couverture équilibrée": (0.70, 95.0, 105.0, 72.0, 18.0, 1.0),
    "Faible couverture bloc": (0.40, 92.0, 105.0, 72.0, 18.0, 1.0),
    "Forte couverture bloc": (1.05, 100.0, 105.0, 72.0, 18.0, 1.0),
    "Prix spot élevé": (0.70, 95.0, 145.0, 105.0, 22.0, 1.0),
    "Forte volatilité": (0.70, 95.0, 105.0, 72.0, 42.0, 1.8),
    "Hiver froid": (0.70, 95.0, 115.0, 78.0, 22.0, 1.0),
}

st.markdown("""
<style>
  .stApp {background: #F4F7FB;} [data-testid="stSidebar"] {background:#0B1F33;color:white}
  [data-testid="stMetric"] {background:white;border:1px solid #DFE7EF;border-radius:12px;padding:14px;box-shadow:0 2px 8px #0b1f3310}
  h1,h2,h3 {color:#102A43} .small-note{color:#627D98;font-size:.83rem}
</style>""", unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def cached_portfolio(year: int, seed: int, cold: float) -> pd.DataFrame:
    return generate_portfolio(year, seed, cold)


@st.cache_data(show_spinner=False)
def cached_spot(index_tuple: tuple, peak: float, offpeak: float, vol: float,
                seed: int, extreme: float, weekday_shape: tuple,
                weekend_shape: tuple):
    return generate_spot_prices(pd.DatetimeIndex(index_tuple), peak, offpeak, vol,
                                seed, extreme, weekday_shape, weekend_shape)


def init_state() -> None:
    defaults = {"scenario": "Couverture équilibrée", "peak": 105.0, "offpeak": 72.0,
                "vol": 18.0, "spot_seed": 7, "extreme": 1.0, "cold": 1.0,
                "block_ratio": .70, "block_price": 95.0}
    for k, v in defaults.items():
        st.session_state.setdefault(k, v)


def apply_scenario() -> None:
    ratio, bp, peak, off, vol, extreme = SCENARIOS[st.session_state.scenario]
    st.session_state.update(block_ratio=ratio, block_price=bp, peak=peak, offpeak=off,
                            vol=vol, extreme=extreme,
                            cold=1.22 if st.session_state.scenario == "Hiver froid" else 1.0)


def money(x: float) -> str:
    return f"{x:,.0f} €".replace(",", " ")


def delta(current: float, ref: float, euro: bool = False) -> str:
    d = current - ref
    pct = d / ref * 100 if ref else 0
    return f"{money(d) if euro else f'{d:,.2f}'} ({pct:+.1f} %)"


def render_markdown_with_math(text_value: str) -> None:
    """Rend séparément les blocs mathématiques pour un affichage KaTeX fiable."""
    for position, part in enumerate(text_value.split("$$")):
        if not part.strip():
            continue
        st.latex(part.strip()) if position % 2 else st.markdown(part)


init_state()
st.sidebar.markdown("## ⚡ EnergyPilot")
st.sidebar.caption("Simulation bloc + spot · portefeuille tertiaire")
scenario = st.sidebar.selectbox("Scénario prédéfini", SCENARIOS, key="scenario", on_change=apply_scenario)
if st.sidebar.button("↺ Réinitialiser les hypothèses", use_container_width=True):
    for key in list(st.session_state):
        del st.session_state[key]
    st.rerun()

st.sidebar.markdown("### Prix spot")
peak = st.sidebar.number_input("Moyenne peak (€/MWh)", 0.0, 500.0, key="peak")
offpeak = st.sidebar.number_input("Moyenne off-peak (€/MWh)", 0.0, 500.0, key="offpeak")
vol = st.sidebar.slider("Volatilité (€/MWh)", 0.0, 100.0, key="vol")
spot_seed = st.sidebar.number_input("Seed spot", 0, 9999, key="spot_seed")
extreme = st.sidebar.slider("Intensité des extrêmes", 0.0, 3.0, key="extreme")
st.sidebar.info("Peak : du lundi au vendredi, de 08 h à 20 h. Les jours fériés ne sont pas exclus dans cette démo.")

with st.sidebar.expander("Shape horaire — 24 h", expanded=False):
    st.caption("Ajustement ajouté en €/MWh à chaque heure. Modifiez directement les 48 cellules.")
    default_shape = pd.DataFrame({
        "Heure": range(24),
        "Semaine (€/MWh)": WEEKDAY_HOURLY_SHAPE,
        "Week-end (€/MWh)": WEEKEND_HOURLY_SHAPE,
    })
    shape_table = st.data_editor(
        default_shape,
        hide_index=True,
        width="stretch",
        disabled=["Heure"],
        num_rows="fixed",
        column_config={
            "Heure": st.column_config.NumberColumn("Heure", format="%02d h"),
            "Semaine (€/MWh)": st.column_config.NumberColumn("Semaine", min_value=-100.0, max_value=100.0, step=1.0, format="%.0f"),
            "Week-end (€/MWh)": st.column_config.NumberColumn("Week-end", min_value=-100.0, max_value=100.0, step=1.0, format="%.0f"),
        },
        key="hourly_spot_shape",
    )
    if st.button("Réinitialiser la shape", width="stretch"):
        st.session_state.pop("hourly_spot_shape", None)
        st.rerun()
weekday_shape = tuple(shape_table["Semaine (€/MWh)"].astype(float))
weekend_shape = tuple(shape_table["Week-end (€/MWh)"].astype(float))

st.sidebar.markdown("### Portefeuille")
cold = st.sidebar.slider("Sévérité de l'hiver", .8, 1.4, key="cold")
upload = st.sidebar.file_uploader("Importer un CSV (facultatif)", type="csv",
    help="Colonnes : timestamp, building et les six usages, en kWh par pas horaire.")

try:
    if upload:
        data = validate_import(pd.read_csv(upload))
        st.sidebar.success("CSV validé et chargé.")
    else:
        data = cached_portfolio(2025, 42, cold)
except Exception as exc:
    st.error(f"Import impossible : {exc}")
    st.stop()

buildings_all = sorted(data.building.unique())
selection = st.sidebar.multiselect("Bâtiments analysés", buildings_all, default=buildings_all)
if not selection:
    st.warning("Sélectionnez au moins un bâtiment.")
    st.stop()
selected = data[data.building.isin(selection)].copy()
base_load = aggregate_load(selected)
index = base_load.index
spot = cached_spot(tuple(index), peak, offpeak, vol, int(spot_seed), extreme,
                   weekday_shape, weekend_shape)

st.sidebar.markdown("### Flexibilité")
flex_enabled = st.sidebar.toggle("Activer le pilotage", value=True)
flex_usages = st.sidebar.multiselect("Usages flexibles", USAGES[:-1], default=["EV", "PAC", "CVC"])
flex_buildings = st.sidebar.multiselect("Bâtiments pilotés", selection, default=selection)
flex_share = st.sidebar.slider("Part déplaçable", 0, 50, 15, format="%d %%") / 100
flex_mode_label = st.sidebar.radio("Mode", ["Automatique", "Manuel -1 h", "Manuel +1 h"])
cross_day = st.sidebar.checkbox("Autoriser le passage au jour suivant (thermique)", False)

st.title("Pilotage énergétique du portefeuille")
st.caption("Simulation annuelle horaire · données synthétiques reproductibles · énergie électrique")

# Couverture mensuelle, initialisée sur un ratio de la moyenne mensuelle.
monthly_mean_mw = base_load.sum(axis=1).groupby(index.month).mean() / 1000
with st.expander("⚙️ Paramétrage de la couverture mensuelle bloc + spot", expanded=False):
    c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
    ratio = c1.slider("% de la puissance moyenne", 0, 150, int(st.session_state.block_ratio * 100),
                      help="Le bloc baseload est livré à puissance constante chaque heure du mois.") / 100
    quick_power = c2.number_input("Bloc rapide identique (MW)", 0.0, 20.0, 0.0, step=.05)
    quick_price = c3.number_input("Prix rapide (€/MWh)", 0.0, 500.0, key="block_price")
    personalize = c4.toggle("Personnaliser les mois", True)
    powers, prices = {}, {}
    cols = st.columns(6)
    for m in range(1, 13):
        default_power = quick_power if quick_power > 0 else float(monthly_mean_mw.get(m, 0) * ratio)
        with cols[(m - 1) % 6]:
            powers[m] = st.number_input(f"{MONTHS[m]} MW", 0.0, 50.0, default_power, .01,
                                         key=f"p_{m}_{scenario}_{len(selection)}", disabled=not personalize)
            prices[m] = st.number_input(f"{MONTHS[m]} €/MWh", 0.0, 500.0, float(quick_price), 1.0,
                                         key=f"b_{m}_{scenario}", disabled=not personalize)
    if not personalize:
        powers = {m: (quick_power if quick_power > 0 else float(monthly_mean_mw.get(m, 0) * ratio)) for m in range(1, 13)}
        prices = {m: quick_price for m in range(1, 13)}
    st.caption("Astuce : le curseur propose automatiquement un bloc en pourcentage de la puissance moyenne de chaque mois.")

surplus_mode_label = st.sidebar.radio("Traitement du surplus", ["Take-or-pay", "Revente du surplus"],
    help="Take-or-pay : le volume contracté est payé même s'il n'est pas consommé. Revente : l'excédent est vendu au spot.")
surplus_mode = "resale" if surplus_mode_label.startswith("Revente") else "take_or_pay"
blocks = monthly_blocks(index, powers, prices)

# Trois scénarios : spot seul, couverture sans pilotage, couverture pilotée.
zero_blocks = monthly_blocks(index, {m: 0 for m in range(1, 13)}, {m: 0 for m in range(1, 13)})
ref_hourly, ref_summary = calculate_cost(base_load.sum(axis=1), spot, zero_blocks, surplus_mode)
covered_hourly, covered_summary = calculate_cost(base_load.sum(axis=1), spot, blocks, surplus_mode)
if flex_enabled and flex_usages and flex_buildings:
    mode = "auto" if flex_mode_label == "Automatique" else "manual"
    shift = -1 if "-1" in flex_mode_label else 1
    optimized_data, flex_info = optimize_flexibility(selected, spot, blocks, flex_usages,
        flex_buildings, flex_share, mode, shift, cross_day)
else:
    optimized_data, flex_info = selected.copy(), {"moved_mwh": 0, "top_movements": []}
opt_load = aggregate_load(optimized_data)
opt_hourly, opt_summary = calculate_cost(opt_load.sum(axis=1), spot, blocks, surplus_mode)
savings = covered_summary["total_cost"] - opt_summary["total_cost"]

metrics = st.columns(8)
metric_data = [
    ("Consommation", f"{opt_summary['energy_mwh']/1000:.2f} GWh", delta(opt_summary['energy_mwh']/1000, ref_summary['energy_mwh']/1000)),
    ("Budget annuel", money(opt_summary["total_cost"]), delta(opt_summary["total_cost"], ref_summary["total_cost"], True)),
    ("Prix moyen", f"{opt_summary['average_price']:.1f} €/MWh", delta(opt_summary['average_price'], ref_summary['average_price'])),
    ("Couverture bloc", f"{opt_summary['coverage_rate']:.1%}", f"{opt_summary['coverage_rate']-ref_summary['coverage_rate']:+.1%}"),
    ("Exposition spot", f"{opt_summary['spot_volume_mwh']/opt_summary['energy_mwh']:.1%}", f"{opt_summary['spot_volume_mwh']-ref_summary['spot_volume_mwh']:+,.0f} MWh"),
    ("Pointe", f"{opt_summary['peak_mw']:.2f} MW", f"{opt_summary['peak_mw']-covered_summary['peak_mw']:+.2f} MW"),
    ("Économies pilotage", money(savings), f"{savings/covered_summary['total_cost']:.2%}"),
    ("Émissions estimées", f"{opt_summary['energy_mwh']*0.052:.0f} tCO₂", "52 kgCO₂/MWh"),
]
for col, (label, value, change) in zip(metrics, metric_data):
    col.metric(label, value, change)

# Une vue de consommation immédiatement visible, avant la navigation détaillée.
st.subheader("Consommations du portefeuille")
overview = opt_load.resample("MS").sum() / 1000
overview_fig = go.Figure()
for usage in USAGES:
    overview_fig.add_bar(
        x=overview.index,
        y=overview[usage],
        name=usage,
        marker_color=COLORS[usage],
        hovertemplate=f"{usage}<br>%{{x|%B %Y}}<br>%{{y:,.1f}} MWh<extra></extra>",
    )
overview_fig.update_layout(
    barmode="stack",
    height=390,
    yaxis_title="Consommation mensuelle (MWh)",
    xaxis_title=None,
    legend=dict(orientation="h", y=1.12),
    margin=dict(l=20, r=20, t=45, b=20),
    hovermode="x unified",
)
st.plotly_chart(overview_fig, width="stretch", key="portfolio_consumption_overview")
st.caption("Répartition mensuelle par usage. Le graphique horaire/journalier, le prix spot et le bloc sont disponibles dans l’onglet « Vue portefeuille » ci-dessous.")

tabs = st.tabs(["Vue portefeuille", "Couverture & optimisation", "Factures",
                "Détail des bâtiments", "Prévisions court terme",
                "Hypothèses & données"])

with tabs[0]:
    st.subheader("Profil de consommation et marchés")
    controls = st.columns([1, 1, 2])
    granularity = controls[0].selectbox("Pas d'affichage", ["Journalier", "Horaire", "Mensuel"])
    min_date, max_date = index.min().date(), index.max().date()
    default_end = min(max_date, min_date + timedelta(days=30)) if granularity == "Horaire" else max_date
    dates = controls[1].date_input("Période", (min_date, default_end), min_value=min_date, max_value=max_date)
    profile = controls[2].radio("Profil", ["Optimisé", "Initial"], horizontal=True)
    chart_load = opt_load if profile == "Optimisé" else base_load
    start, end = pd.Timestamp(dates[0]), pd.Timestamp(dates[-1] + timedelta(days=1))
    chart_load = chart_load.loc[(chart_load.index >= start) & (chart_load.index < end)]
    freq = {"Horaire": "h", "Journalier": "D", "Mensuel": "MS"}[granularity]
    agg = chart_load.resample(freq).mean() if granularity != "Mensuel" else chart_load.resample(freq).mean()
    spot_plot = spot.loc[chart_load.index].resample(freq).mean()
    block_plot = blocks.block_mwh.loc[chart_load.index].resample(freq).mean() * 1000
    unit, divisor = ("MW", 1000) if agg.sum(axis=1).max() >= 1000 else ("kW", 1)
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    for usage in USAGES:
        fig.add_trace(go.Scatter(x=agg.index, y=agg[usage]/divisor, name=usage, stackgroup="load",
                                 line=dict(width=.5, color=COLORS[usage]), hovertemplate="%{y:.2f} "+unit), secondary_y=False)
    fig.add_trace(go.Scatter(x=block_plot.index, y=block_plot/divisor, name="Bloc", line_shape="hv",
                             line=dict(color="#10B981", width=2), fill="tozeroy", opacity=.28), secondary_y=False)
    fig.add_trace(go.Scatter(x=spot_plot.index, y=spot_plot, name="Prix spot", line=dict(color="#111827", width=1.4)), secondary_y=True)
    fig.update_yaxes(title_text=f"Puissance moyenne ({unit})", secondary_y=False)
    fig.update_yaxes(title_text="Prix (€/MWh)", secondary_y=True, showgrid=False)
    fig.update_layout(height=570, hovermode="x unified", legend=dict(orientation="h", y=1.08), margin=dict(l=20,r=20,t=50,b=20))
    st.plotly_chart(fig, width="stretch")
    st.caption("Les séries affichent une puissance moyenne par pas. Les calculs budgétaires restent réalisés heure par heure sur l'année complète.")

with tabs[1]:
    st.subheader("Comparaison des trois scénarios")
    compare = pd.DataFrame({
        "Scénario": ["Référence spot seul", "Bloc + spot", "Bloc + spot optimisé"],
        "Budget (€)": [ref_summary["total_cost"], covered_summary["total_cost"], opt_summary["total_cost"]],
        "Prix moyen (€/MWh)": [ref_summary["average_price"], covered_summary["average_price"], opt_summary["average_price"]],
        "Achats spot (MWh)": [ref_summary["spot_volume_mwh"], covered_summary["spot_volume_mwh"], opt_summary["spot_volume_mwh"]],
    })
    st.dataframe(compare.style.format({"Budget (€)": "{:,.0f}", "Prix moyen (€/MWh)": "{:.2f}", "Achats spot (MWh)": "{:,.1f}"}), hide_index=True, width="stretch")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("MWh déplacés", f"{flex_info['moved_mwh']:.1f}")
    c2.metric("Économies", money(savings), f"{savings/covered_summary['total_cost']:.2%}")
    c3.metric("Nouvelle pointe", f"{opt_summary['peak_mw']:.2f} MW", f"{opt_summary['peak_mw']-covered_summary['peak_mw']:+.2f} MW")
    c4.metric("Effet coût spot", money(covered_summary['spot_cost']-opt_summary['spot_cost']))
    if flex_info["top_movements"]:
        moves = ", ".join(f"{a} h → {b} h ({v/1000:.1f} MWh)" for (a,b),v in flex_info["top_movements"])
        st.info(f"Principaux déplacements : {moves}")
    st.plotly_chart(go.Figure([go.Bar(x=compare["Scénario"], y=compare["Budget (€)"], marker_color=["#64748B", "#3B82F6", "#10B981"])]).update_layout(height=360, yaxis_title="Budget (€)"), width="stretch")

with tabs[2]:
    invoices = monthly_invoices(optimized_data, opt_hourly, covered_hourly)
    st.subheader("Factures mensuelles allouées")
    st.info("Les coûts du portefeuille sont alloués à chaque heure au prorata de la consommation du bâtiment. Le bloc n'est donc pas affecté physiquement à un site.")
    f1, f2 = st.columns(2)
    invoice_buildings = f1.multiselect("Bâtiment", selection, default=selection, key="invoice_b")
    invoice_months = f2.multiselect("Mois", sorted(invoices.month.unique()), default=sorted(invoices.month.unique()), key="invoice_m")
    filtered = invoices[invoices.building.isin(invoice_buildings) & invoices.month.isin(invoice_months)]
    st.dataframe(filtered, hide_index=True, width="stretch", column_config={
        "total_cost": st.column_config.NumberColumn("Coût total", format="%.0f €"),
        "average_price": st.column_config.NumberColumn("Prix moyen", format="%.2f €/MWh")})
    st.download_button("Télécharger les factures CSV", filtered.to_csv(index=False).encode(), "factures_energy_pilot.csv", "text/csv")
    if len(filtered):
        row = filtered.iloc[0]
        st.markdown(f"### Facture simplifiée · {row['building']} · {row['month']}")
        a,b,c,d = st.columns(4)
        a.metric("Consommation", f"{row.consumption_mwh:.1f} MWh")
        b.metric("Bloc alloué", money(row.block_cost))
        c.metric("Spot net", money(row.spot_cost-row.resale_revenue))
        d.metric("Total", money(row.total_cost))
    monthly_chart = filtered.groupby("month", as_index=False).total_cost.sum()
    st.plotly_chart(go.Figure(go.Bar(x=monthly_chart.month, y=monthly_chart.total_cost, marker_color="#3B82F6")).update_layout(height=340, yaxis_title="Coût (€)"), width="stretch")

with tabs[3]:
    st.subheader("Détail des bâtiments")
    meta = pd.DataFrame([b.__dict__ for b in BUILDINGS if b.name in selection]).rename(columns={"name":"Bâtiment","city":"Ville","kind":"Type","area_m2":"Surface (m²)","heat":"Chauffage"})
    annual = selected.assign(total_kwh=selected[USAGES].sum(axis=1)).groupby("building").total_kwh.sum()/1e6
    meta["Consommation (GWh)"] = meta["Bâtiment"].map(annual)
    st.dataframe(meta, hide_index=True, width="stretch")
    detail_b = st.selectbox("Analyser", selection)
    detail = selected[selected.building.eq(detail_b)].set_index("timestamp")[USAGES].resample("MS").sum()/1000
    fig_detail = go.Figure()
    for u in USAGES:
        fig_detail.add_bar(x=detail.index, y=detail[u], name=u, marker_color=COLORS[u])
    fig_detail.update_layout(barmode="stack", height=420, yaxis_title="Énergie (MWh)")
    st.plotly_chart(fig_detail, width="stretch")

with tabs[4]:
    st.subheader("Plateforme de prévisions court terme")
    st.caption("Simulation day-ahead des échanges avec des prévisionnistes externes et valorisation des écarts au pas horaire.")

    forecast_controls = st.columns(4)
    forecast_days = forecast_controls[0].selectbox("Fenêtre de suivi", [7, 14, 30], index=1, format_func=lambda x: f"{x} jours")
    imbalance_spread = forecast_controls[1].slider("Spread moyen des écarts", 5.0, 60.0, 22.0, 1.0, help="Écart moyen en €/MWh autour du spot.")
    imbalance_jumps = forecast_controls[2].slider("Intensité des sauts", 0.0, 3.0, 1.0, .1)
    imbalance_seed = forecast_controls[3].number_input("Seed balancing", 0, 9999, 818)

    forecast_index = index[-forecast_days * 24:]
    actual_forecast = opt_load.sum(axis=1).loc[forecast_index] / 1000
    provider_forecasts = generate_provider_forecasts(actual_forecast)
    provider_metrics = mae_table(actual_forecast, provider_forecasts)
    best_weights = inverse_mape_weights(actual_forecast, provider_forecasts)
    available_providers = provider_metrics.loc[provider_metrics.Disponible, "Prestataire"].tolist()

    st.markdown("### Supervision de la chaîne")
    status_cols = st.columns(4)
    stage_cards = [
        ("Génération du réalisé", "OK", "#10B981", "Courbe portefeuille disponible"),
        ("Calcul des features", "OK", "#10B981", "Calendrier, météo et historique prêts"),
        ("Réception prestataires", "ALERTE", "#F59E0B", "3 reçues sur 4 attendues"),
        ("LoadSense", "KO · EN RETARD", "#EF4444", "Échéance dépassée de 42 min"),
    ]
    for col, (title, state, color, detail) in zip(status_cols, stage_cards):
        col.markdown(f"""<div style="background:white;border:1px solid #DFE7EF;border-left:6px solid {color};border-radius:10px;padding:13px;height:112px">
        <div style="font-size:.82rem;color:#627D98">{title}</div><div style="font-weight:700;color:{color};margin:5px 0">● {state}</div><div style="font-size:.78rem;color:#486581">{detail}</div></div>""", unsafe_allow_html=True)

    provider_view = provider_metrics.copy()
    provider_view["Voyant"] = provider_view["Statut"].map({"Reçue": "🟢", "Dégradée": "🟠", "En retard": "🔴"})
    provider_view["Dernière réception"] = ["06:08", "06:11", "06:19", "Attendue 06:15"]
    provider_view["SLA"] = ["OK", "OK", "Qualité dégradée", "KO · +42 min"]
    st.dataframe(provider_view[["Voyant", "Prestataire", "Statut", "Dernière réception", "SLA", "MAPE (%)"]],
                 hide_index=True, width="stretch",
                 column_config={"MAPE (%)": st.column_config.NumberColumn(format="%.2f %%")})

    st.markdown("### Pondération barycentrique")
    preset = st.radio("Point de pondération proposé", ["Meilleure précision", "Équipondéré", "Prudent"], horizontal=True,
                      help="Les poids sont normalisés pour former une combinaison convexe : leur somme vaut 100 %.")
    if preset == "Meilleure précision":
        proposed = best_weights
    elif preset == "Équipondéré":
        proposed = {p: 1 / len(available_providers) for p in available_providers}
    else:
        ranking = provider_metrics.dropna(subset=["MAPE (%)"]).sort_values("MAPE (%)").Prestataire.tolist()
        proposed = dict(zip(ranking, [.60, .25, .15]))
    weight_cols = st.columns(len(available_providers))
    raw_weights = {}
    for col, provider in zip(weight_cols, available_providers):
        raw_weights[provider] = col.slider(provider, 0, 100, int(round(proposed.get(provider, 0) * 100)), 1,
                                            key=f"forecast_weight_{preset}_{provider}", format="%d %%")
    barycentre, normalized_weights = barycentric_forecast(provider_forecasts, raw_weights)
    bary_mape = float(((barycentre - actual_forecast).abs() / actual_forecast.replace(0, np.nan)).mean() * 100)

    # Simplexe barycentrique : trois prestataires disponibles = trois sommets.
    simplex = go.Figure()
    points = {
        "Choix actuel": normalized_weights,
        "Équipondéré": {p: 1/3 for p in available_providers},
        "Précision": best_weights,
        "Prudent": proposed if preset == "Prudent" else dict(zip(provider_metrics.dropna(subset=["MAPE (%)"]).sort_values("MAPE (%)").Prestataire, [.60, .25, .15])),
    }
    for label, point in points.items():
        simplex.add_trace(go.Scatterternary(
            a=[point.get(available_providers[0], 0) * 100],
            b=[point.get(available_providers[1], 0) * 100],
            c=[point.get(available_providers[2], 0) * 100],
            mode="markers+text", text=[label], textposition="top center",
            marker=dict(size=14 if label == "Choix actuel" else 9), name=label))
    simplex.update_layout(height=360, showlegend=False, ternary=dict(sum=100,
        aaxis_title=available_providers[0], baxis_title=available_providers[1], caxis_title=available_providers[2]),
        margin=dict(l=35, r=35, t=20, b=35))

    imbalance_prices = generate_imbalance_prices(spot.loc[forecast_index], int(imbalance_seed), imbalance_spread, imbalance_jumps)
    balancing_hourly, balancing_summary = calculate_balancing_pnl(actual_forecast, barycentre,
                                                                   spot.loc[forecast_index], imbalance_prices)
    kpis = st.columns(6)
    kpis[0].metric("MAPE barycentre", f"{bary_mape:.2f} %")
    kpis[1].metric("Poids normalisés", "100 %", "combinaison convexe")
    kpis[2].metric("Écart court", f"{balancing_summary['short_mwh']:.1f} MWh")
    kpis[3].metric("Écart long", f"{balancing_summary['long_mwh']:.1f} MWh")
    kpis[4].metric("P&L balancing", money(balancing_summary["pnl"]), "positif = gain")
    kpis[5].metric("Coût des écarts", money(balancing_summary["balancing_cost"]))

    chart_cols = st.columns([1, 1])
    with chart_cols[0]:
        st.plotly_chart(simplex, width="stretch", key="forecast_barycentric_simplex")
    with chart_cols[1]:
        mape_fig = go.Figure(go.Bar(x=provider_view.Prestataire, y=provider_view["MAPE (%)"],
                                   marker_color=["#10B981", "#10B981", "#F59E0B", "#EF4444"]))
        mape_fig.add_hline(y=bary_mape, line_dash="dash", line_color="#2563EB", annotation_text="Barycentre")
        mape_fig.update_layout(height=360, yaxis_title="MAPE (%)", margin=dict(l=20, r=20, t=20, b=35))
        st.plotly_chart(mape_fig, width="stretch", key="forecast_mape")

    forecast_fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
        specs=[[{"secondary_y": True}], [{"secondary_y": False}]],
        row_heights=[.68, .32], vertical_spacing=.08)
    forecast_fig.add_trace(go.Scatter(x=actual_forecast.index, y=actual_forecast, name="Réalisé", line=dict(color="#111827", width=2.5)), row=1, col=1, secondary_y=False)
    for provider in available_providers:
        forecast_fig.add_trace(go.Scatter(x=provider_forecasts.index, y=provider_forecasts[provider], name=provider, opacity=.42, line=dict(width=1)), row=1, col=1, secondary_y=False)
        provider_error = actual_forecast - provider_forecasts[provider]
        forecast_fig.add_trace(go.Scatter(x=provider_error.index, y=provider_error,
            name=f"Écart {provider}", opacity=.32, line=dict(width=1), showlegend=False), row=2, col=1)
    forecast_fig.add_trace(go.Scatter(x=barycentre.index, y=barycentre, name="Barycentre", line=dict(color="#2563EB", width=2.5, dash="dash")), row=1, col=1, secondary_y=False)
    forecast_fig.add_trace(go.Scatter(x=imbalance_prices.index, y=imbalance_prices.up_price, name="Prix écart +", line=dict(color="#EF4444", width=1)), row=1, col=1, secondary_y=True)
    forecast_fig.add_trace(go.Scatter(x=imbalance_prices.index, y=imbalance_prices.down_price, name="Prix écart −", line=dict(color="#10B981", width=1)), row=1, col=1, secondary_y=True)
    bary_error = actual_forecast - barycentre
    forecast_fig.add_trace(go.Bar(x=bary_error.index, y=bary_error, name="Écart barycentre",
        marker_color=np.where(bary_error >= 0, "#EF4444", "#10B981")), row=2, col=1)
    forecast_fig.add_hline(y=0, line_color="#64748B", line_width=1, row=2, col=1)
    forecast_fig.update_yaxes(title_text="Puissance (MW)", row=1, col=1, secondary_y=False)
    forecast_fig.update_yaxes(title_text="Prix (€/MWh)", row=1, col=1, secondary_y=True, showgrid=False)
    forecast_fig.update_yaxes(title_text="Réalisé − prévision (MW)", row=2, col=1)
    forecast_fig.update_layout(height=650, hovermode="x unified", legend=dict(orientation="h", y=1.08), margin=dict(l=20,r=20,t=55,b=20))
    st.plotly_chart(forecast_fig, width="stretch", key="short_term_forecasts")

    pnl_fig = go.Figure()
    pnl_fig.add_bar(x=balancing_hourly.index, y=balancing_hourly.balancing_pnl,
                    marker_color=np.where(balancing_hourly.balancing_pnl >= 0, "#10B981", "#EF4444"), name="P&L horaire")
    pnl_fig.add_scatter(x=balancing_hourly.index, y=balancing_hourly.balancing_pnl.cumsum(),
                        name="P&L cumulé", yaxis="y2", line=dict(color="#2563EB", width=2))
    pnl_fig.update_layout(height=390, hovermode="x unified", yaxis_title="P&L horaire (€)",
                          yaxis2=dict(title="P&L cumulé (€)", overlaying="y", side="right", showgrid=False),
                          legend=dict(orientation="h", y=1.1), margin=dict(l=20,r=20,t=40,b=20))
    st.plotly_chart(pnl_fig, width="stretch", key="balancing_pnl")
    st.caption("P&L = coût du réalisé valorisé au spot − coût de la nomination et du règlement des écarts. Un P&L négatif est un surcoût.")
    export_balancing = balancing_hourly.assign(barycentric_forecast=barycentre)
    st.download_button("Télécharger le suivi horaire CSV", export_balancing.to_csv().encode(),
                       "previsions_et_balancing.csv", "text/csv")

with tabs[5]:
    st.subheader("Hypothèses, qualité et données")
    note_path = Path(__file__).with_name("CALCULS_ET_HYPOTHESES.md")
    note_text = note_path.read_text(encoding="utf-8")
    with st.expander("📘 Note méthodologique détaillée", expanded=True):
        render_markdown_with_math(note_text)
        st.download_button(
            "Télécharger la note méthodologique",
            note_text.encode("utf-8"),
            file_name="EnergyPilot_note_methodologique.md",
            mime="text/markdown",
        )
    st.markdown("### Shape horaire du prix spot")
    shape_hours = pd.date_range("2025-01-06", periods=24, freq="h")
    shape_weekday = generate_spot_prices(shape_hours, peak, offpeak, 0, 1, 0,
                                         weekday_shape, weekend_shape)
    weekend_hours = pd.date_range("2025-01-11", periods=24, freq="h")
    shape_weekend = generate_spot_prices(weekend_hours, peak, offpeak, 0, 1, 0,
                                         weekday_shape, weekend_shape)
    shape_fig = go.Figure()
    shape_fig.add_scatter(x=list(range(24)), y=shape_weekday.to_numpy(), name="Jour ouvré", line=dict(color="#2563EB", width=3))
    shape_fig.add_scatter(x=list(range(24)), y=shape_weekend.to_numpy(), name="Week-end", line=dict(color="#10B981", width=3))
    shape_fig.update_layout(height=330, xaxis_title="Heure", yaxis_title="Prix hors volatilité (€/MWh)", xaxis=dict(dtick=2), margin=dict(l=20, r=20, t=20, b=20))
    st.plotly_chart(shape_fig, width="stretch", key="spot_hourly_shapes")
    st.caption("Exemple avec la saisonnalité du jour sélectionné, sans bruit ni épisode extrême.")
    st.markdown("### Contrôles de qualité")
    expected = len(index)
    issues = []
    if selected[USAGES].isna().any().any(): issues.append("Consommation manquante")
    if (selected[USAGES] < 0).any().any(): issues.append("Consommation négative")
    counts = selected.groupby("building").timestamp.nunique()
    if counts.nunique() != 1 or counts.min() != expected: issues.append("Série temporelle incomplète")
    if any(v < 0 for v in powers.values()) or any(v < 0 for v in prices.values()): issues.append("Prix ou puissance invalide")
    if issues: st.error("Contrôles : " + " · ".join(issues))
    else: st.success(f"Contrôles réussis : {expected:,} heures, aucune valeur manquante ou négative, unités cohérentes.")
    st.dataframe(selected.head(500), hide_index=True, width="stretch")
