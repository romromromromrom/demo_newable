# Note méthodologique — EnergyPilot

Cette note décrit les données, hypothèses, calculs et options du démonstrateur. EnergyPilot est un outil pédagogique d'aide à la décision : ses résultats illustrent les mécanismes d'une fourniture électrique **bloc mensuel + spot**, mais ne constituent ni une offre commerciale, ni une facture fournisseur, ni un bilan carbone réglementaire.

## 1. Périmètre et conventions d'unités

Le portefeuille synthétique comprend six bâtiments tertiaires et couvre l'année 2025 au pas horaire, soit 8 760 heures par bâtiment. Pour un bâtiment `b`, un usage `u` et une heure `h`, la consommation est notée :

$$E_{b,u,h}\quad [\mathrm{kWh}]$$

Comme chaque pas dure exactement une heure, cette énergie est numériquement égale à la puissance moyenne en kW sur l'heure. Les calculs financiers utilisent des MWh :

$$E_{b,h}^{MWh}=\frac{\sum_u E_{b,u,h}}{1000}$$

La consommation du portefeuille est la somme des bâtiments sélectionnés :

$$E_h=\sum_b E_{b,h}^{MWh}$$

Une puissance de bloc de `1 MW` livrée pendant une heure représente donc `1 MWh`. Les graphiques peuvent afficher des kW, MW, MWh ou GWh selon le niveau d'agrégation ; le moteur financier reste toujours horaire et en MWh.

## 2. Construction des consommations synthétiques

Les profils sont reproductibles grâce à une seed fixe. La surface, le type de bâtiment, la localisation et le mode de chauffage modulent leur amplitude. Chaque série combine :

- une charge de fond permanente ;
- une occupation plus forte les jours ouvrés entre 07 h et 20 h ;
- un cycle intra-journalier ;
- une température extérieure synthétique saisonnière et journalière ;
- un bruit multiplicatif borné, identique à chaque exécution pour une même seed.

Les besoins thermiques normalisés sont approximativement :

$$B_h^{chauffage}=\max\left(\frac{17-T_h}{17},0\right)$$

$$B_h^{froid}=\max\left(\frac{T_h-21}{12},0\right)$$

Les six usages sont exclusifs : EV, Chauffage, CVC, PAC, Climatisation et Autres. Un bâtiment équipé d'une PAC n'a pas de consommation dans la colonne Chauffage. La CVC représente uniquement la ventilation et les auxiliaires de traitement d'air ; elle ne reproduit ni la chaleur de la PAC, ni le froid de la climatisation. Cette convention évite le double comptage.

Le curseur **Sévérité de l'hiver** abaisse ou relève la température synthétique. Il modifie donc le chauffage électrique et les PAC, sans multiplier arbitrairement tous les usages.

## 3. Prix spot synthétique

Une heure est dite **peak** du lundi au vendredi, de 08 h inclus à 20 h exclu. Les autres heures sont off-peak. Les jours fériés ne sont pas retirés dans cette version.

Le prix avant événements extrêmes suit la forme :

$$P_h^{spot}=\mu_h+S_h+\epsilon_h$$

avec :

- $\mu_h=\mu_{peak}$ ou $\mu_{offpeak}$ selon l'heure ;
- $S_h$ une saisonnalité hivernale sinusoïdale ;
- $\epsilon_h\sim\mathcal{N}(0,\sigma)$, où $\sigma$ est la volatilité choisie.

Des heures rares reçoivent ensuite un choc positif ou négatif afin de représenter les pointes de marché et les prix faibles ou négatifs. Le paramètre **Intensité des extrêmes** agit sur leur nombre et leur amplitude. Les prix finaux sont bornés entre `-120 €/MWh` et `650 €/MWh`. Modifier la seed change les tirages, pas la logique du modèle.

## 4. Produit de couverture mensuel

Pour chaque mois `m`, l'utilisateur définit :

- une puissance baseload $B_m$ en MW ;
- un prix contractuel $P_m^{bloc}$ en €/MWh.

Pour toute heure `h` du mois `m`, le volume contracté est :

$$V_h^{bloc}=B_m\times 1\mathrm{h}=B_m\quad [\mathrm{MWh}]$$

Le coût du bloc est dû sur la totalité du volume contracté, indépendamment de la consommation :

$$C_h^{bloc}=V_h^{bloc}\times P_m^{bloc}$$

La saisie rapide applique une même puissance et un même prix aux douze mois. La personnalisation permet de modifier chaque mois. La proposition en pourcentage applique le ratio choisi à la puissance horaire moyenne du mois :

$$B_m=r\times\frac{1}{N_m}\sum_{h\in m}E_h$$

Ce réglage est une aide au dimensionnement, pas une optimisation mathématique du volume contractuel.

## 5. Résiduel spot et traitement du surplus

Le résiduel signé vaut :

$$R_h=E_h-V_h^{bloc}$$

Les achats spot et le surplus sont séparés pour ne jamais créer de volumes négatifs :

$$V_h^{achat}=\max(R_h,0)$$

$$V_h^{surplus}=\max(-R_h,0)$$

Le coût d'achat spot est :

$$C_h^{spot}=V_h^{achat}\times P_h^{spot}$$

Deux traitements du surplus sont disponibles.

### Take-or-pay

Le surplus n'est pas valorisé. Le bloc reste intégralement payé :

$$R_h^{revente}=0$$

$$C_h^{total}=C_h^{bloc}+C_h^{spot}$$

### Revente du surplus

Le surplus est vendu au même prix spot horaire, sans spread, frais ni pénalité :

$$R_h^{revente}=V_h^{surplus}\times P_h^{spot}$$

$$C_h^{total}=C_h^{bloc}+C_h^{spot}-R_h^{revente}$$

Avec un prix spot négatif, une revente peut produire un revenu négatif, c'est-à-dire un coût de cession. C'est volontaire et cohérent avec l'hypothèse de règlement au prix spot.

## 6. Flexibilité des usages

Les usages pilotables sont Chauffage, PAC, CVC, Climatisation et EV. **Autres** reste toujours inchangé. L'utilisateur choisit les usages, les bâtiments et une fraction flexible $f$ entre 0 et 50 %.

Pour une consommation source $E_{b,u,h}$, le volume candidat au déplacement est :

$$D_{b,u,h}=f\times E_{b,u,h}$$

### Mode manuel

Le volume est déplacé vers `h-1` ou `h+1`, si l'heure cible est admissible. Pour les usages thermiques, un passage entre deux dates civiles est interdit par défaut. L'option dédiée permet de lever cette contrainte.

### Mode automatique

Pour chaque heure, l'algorithme compare `h-1`, `h` et `h+1`. Le coût marginal pris en compte est nul tant que la charge totale reste sous le bloc, puis égal au spot lorsque le portefeuille dépasse le bloc :

$$P_h^{marginal}=\begin{cases}
0 & \text{si } E_h\leq V_h^{bloc}\\
P_h^{spot} & \text{si } E_h>V_h^{bloc}
\end{cases}$$

Le déplacement n'a lieu que si une heure admissible présente un coût marginal strictement inférieur. Le modèle ne décale donc pas systématiquement toute la flexibilité.

Pour tout bâtiment et usage piloté, l'invariant énergétique est :

$$\sum_h E_{b,u,h}^{optimisé}=\sum_h E_{b,u,h}^{initial}$$

Les consommations sont contraintes à rester positives ou nulles. La consommation non flexible n'est jamais modifiée. Cette démonstration ne modélise toutefois ni puissance maximale d'équipement, ni rebond thermique, ni durée minimale de fonctionnement, ni état de charge des véhicules.

## 7. Scénarios comparés

Trois résultats sont calculés sur les mêmes consommations et les mêmes prix spot :

1. **Référence spot seul** : bloc nul, aucune couverture ;
2. **Bloc + spot** : couverture active, profil initial ;
3. **Bloc + spot optimisé** : même couverture, profil après flexibilité.

Les économies de pilotage sont mesurées entre les scénarios 2 et 3 :

$$Économies=C_{bloc+spot}^{initial}-C_{bloc+spot}^{optimisé}$$

Le pourcentage d'économie utilise le budget bloc + spot initial comme dénominateur. Les scénarios prédéfinis modifient simultanément des hypothèses de couverture, de prix ou de météo ; ils servent de points de départ et restent modifiables.

## 8. Indicateurs

Le prix moyen de fourniture est :

$$P^{moyen}=\frac{\sum_h C_h^{total}}{\sum_h E_h}$$

Le taux de couverture physique par les blocs évite de compter le surplus comme de l'énergie consommée couverte :

$$T^{couverture}=\frac{\sum_h\min(E_h,V_h^{bloc})}{\sum_h E_h}$$

L'exposition spot affichée est :

$$T^{spot}=\frac{\sum_h V_h^{achat}}{\sum_h E_h}$$

La pointe est le maximum de la puissance moyenne horaire du portefeuille :

$$P^{pointe}=\max_h(E_h)\quad [\mathrm{MW}]$$

Le volume excédentaire est la somme des surplus horaires, même en mode take-or-pay. Les MWh déplacés correspondent aux volumes retirés de leurs heures sources ; ils ne sont comptés qu'une fois.

## 9. Factures mensuelles par bâtiment

Le contrat est simulé au niveau du portefeuille. Pour ventiler ses coûts, une clé horaire proportionnelle à la consommation est utilisée :

$$\alpha_{b,h}=\frac{E_{b,h}}{\sum_j E_{j,h}}$$

Chaque composante financière est affectée selon cette même clé :

$$C_{b,h}^{x}=\alpha_{b,h}\times C_h^x$$

où `x` désigne le bloc, le spot, la revente ou le total. Les valeurs horaires sont ensuite sommées par bâtiment et par mois. Ainsi, la somme de toutes les factures mensuelles est égale au résultat financier annuel du portefeuille, aux seuls arrondis d'affichage près.

Cette allocation ne représente pas un sous-comptage contractuel réel : elle est neutre, simple et réconciliable. Une allocation par puissance souscrite, surface ou quote-part fixe nécessiterait une autre règle métier.

## 10. Émissions de CO₂

L'indicateur utilise une intensité constante illustrative de `52 kgCO₂/MWh` :

$$CO_2=E^{annuel}\times 0{,}052\quad [\mathrm{tCO_2}]$$

Il dépend uniquement de la consommation totale. Une flexibilité qui conserve l'énergie ne change donc pas cet indicateur. Le calcul n'utilise ni facteur horaire marginal, ni garanties d'origine, ni analyse de cycle de vie.

## 11. Import, contrôles et performance

Le CSV facultatif doit contenir `timestamp`, `building` et les six colonnes d'usages, en kWh. L'application refuse les colonnes obligatoires absentes, les valeurs manquantes et les consommations négatives.

Les contrôles intégrés signalent également une série temporelle incomplète, une puissance ou un prix invalide et une incohérence de données. La génération et les prix spot sont mis en cache. Les calculs budgétaires conservent l'année horaire complète, tandis que les graphiques agrègent les données avant envoi au navigateur. En affichage horaire, la période initiale est limitée à environ un mois pour préserver la fluidité.

## 12. Limites et interprétation

- Les données et prix sont synthétiques ; ils ne doivent pas servir à prévoir un marché réel.
- Les tarifs réseau, taxes, garanties d'origine, capacité, pertes, frais de gestion et profilage fournisseur ne sont pas inclus.
- Le prix du bloc est fixe par mois et aucun coût de transaction ou spread bid-ask n'est appliqué.
- Le modèle suppose des pas horaires parfaits et n'intègre pas les changements d'heure légale.
- L'optimiseur est un algorithme local à ±1 heure, pas une optimisation globale sous contraintes techniques.
- Les températures sont communes dans leur logique mais simulées indépendamment par bâtiment ; elles ne proviennent pas d'une station météo observée.
- Les résultats financiers non arrondis font foi dans les calculs ; les cartes et tableaux peuvent présenter de petits écarts visuels dus à l'arrondi.

Ces simplifications rendent les mécanismes transparents et facilement modifiables. Pour un usage opérationnel, il faudrait remplacer les données synthétiques par des courbes de charge validées, intégrer les clauses contractuelles réelles et calibrer les contraintes de flexibilité équipement par équipement.
