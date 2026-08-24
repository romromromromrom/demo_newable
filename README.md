# EnergyPilot

Démonstrateur Streamlit de simulation de consommation, flexibilité et fourniture électrique **bloc mensuel + spot** pour six bâtiments tertiaires fictifs.

## Installation et lancement

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
streamlit run app.py
```

Puis ouvrir `http://localhost:8501`. Tests :

```bash
pytest -q
```

## Modèle métier

La description exhaustive des formules, options et limites se trouve dans
[`CALCULS_ET_HYPOTHESES.md`](CALCULS_ET_HYPOTHESES.md). Cette note est également
affichée et téléchargeable depuis l'onglet **Hypothèses & données** de l'application.

- Les profils synthétiques couvrent 2025 heure par heure avec une seed fixe. Chaque ligne d'usage contient des kWh sur une heure, numériquement équivalents à des kW moyens.
- Chauffage résistif et PAC sont exclusifs par bâtiment. La climatisation produit le froid ; la CVC correspond seulement aux auxiliaires de ventilation et traitement d'air, ce qui évite le double comptage.
- Le bloc est un baseload mensuel exprimé en MW, soit autant de MWh livrés à chaque heure du mois. Toute l'énergie contractée est payée au prix bloc.
- Au-dessus du bloc, le résiduel est acheté au spot. Sous le bloc, le surplus est soit perdu en take-or-pay, soit revendu au prix spot.
- Les coûts sont calculés heure par heure. Les factures bâtiments répartissent chaque composante horaire au prorata de leur consommation à cette heure.
- La flexibilité conserve l'énergie de chaque usage. Le mode automatique compare `h-1`, `h`, `h+1` et ne déplace que vers un coût marginal inférieur. Les usages thermiques restent dans la même journée sauf option contraire.
- L'intensité carbone fixe de 52 kgCO₂/MWh est une indication pédagogique configurable dans le code, non un bilan réglementaire.
- Le peak est défini du lundi au vendredi de 08 h à 20 h, sans retrait des jours fériés.

## Import CSV

Le CSV facultatif doit contenir `timestamp`, `building`, puis `EV`, `Chauffage`, `CVC`, `PAC`, `Climatisation`, `Autres`. Les consommations sont en kWh par pas horaire. Sans fichier, l'application génère automatiquement toutes les données.

## Structure

- `app.py` : interface, scénarios, visualisations et contrôles.
- `data_generation.py` : portefeuille, consommations et prix spot synthétiques.
- `energy_calculations.py` : couverture, coûts, flexibilité et facturation.
- `tests/` : contrôles unitaires des invariants physiques et financiers.
