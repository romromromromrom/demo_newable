"""EnergyPilot — démonstrateur Streamlit de pilotage énergétique tertiaire."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from data_generation import BUILDINGS, USAGES, generate_portfolio, generate_spot_prices, validate_import
from energy_calculations import (aggregate_load, calculate_cost, monthly_blocks,
                                 monthly_invoices, optimize_flexibility)

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
def cached_spot(index_tuple: tuple, peak: float, offpeak: float, vol: float, seed: int, extreme: float):
    return generate_spot_prices(pd.DatetimeIndex(index_tuple), peak, offpeak, vol, seed, extreme)


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
spot = cached_spot(tuple(index), peak, offpeak, vol, int(spot_seed), extreme)

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

tabs = st.tabs(["Vue portefeuille", "Couverture & optimisation", "Factures", "Détail des bâtiments", "Hypothèses & données"])

with tabs[0]:
    st.subheader("Profil de consommation et marchés")
    controls = st.columns([1, 1, 2])
    granularity = controls[0].selectbox("Pas d'affichage", ["Journalier", "Horaire", "Mensuel"])
    min_date, max_date = index.min().date(), index.max().date()
    default_end = min(max_date, min_date + pd.Timedelta(days=30)) if granularity == "Horaire" else max_date
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
    st.subheader("Hypothèses, qualité et données")
    note_path = Path(__file__).with_name("CALCULS_ET_HYPOTHESES.md")
    note_text = note_path.read_text(encoding="utf-8")
    with st.expander("📘 Note méthodologique détaillée", expanded=True):
        st.markdown(note_text)
        st.download_button(
            "Télécharger la note méthodologique",
            note_text.encode("utf-8"),
            file_name="EnergyPilot_note_methodologique.md",
            mime="text/markdown",
        )
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
