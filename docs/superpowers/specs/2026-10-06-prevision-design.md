# Design de la prévision de disponibilité

- Date : 6 octobre 2026
- Statut : décidé en mode autonome, à la demande de l'auteur (« travailler sur les deux semaines dispos afin de
  quantifier le modèle »)
- Périmètre : sous-projet 3, premier modèle, sur l'archive Kaggle (2 au 16 décembre 2025)

## Objectif

Prévoir le nombre de vélos disponibles dans chaque station à plusieurs horizons, et mesurer précisément ce que le
modèle apporte par rapport à des références simples.

## Critère de réussite

`velib-forecast evaluate --source kaggle` produit un rapport qui donne, pour chaque horizon, l'erreur moyenne du
modèle et des références sur les 4 jours de test. Le modèle doit battre la meilleure référence à chaque horizon.

## Données, vérifiées le 6 octobre 2026

- 6 353 181 relevés : les 1 503 stations ont toutes leurs 4 227 créneaux de 5 minutes, sans trou
- du mardi 2 au mardi 16 décembre 2025, heure de Paris, dont 2 week-ends
- 97,6 % des relevés en service ; station vide 2,9 % du temps, pleine 1,0 %
- météo par créneau : 1,3 à 16,5 °C, pluie sur 445 créneaux sur 4 227, vent jusqu'à 6,2 m/s
- erreur moyenne de la persistance : 0,93 vélo à 15 minutes, 1,89 à 1 heure, 3,69 à 3 heures

## Problème posé

- cible : vélos disponibles (mécaniques + électriques) dans la station à l'instant t + h
- horizons h : 15, 30, 60, 120 et 180 minutes, un modèle par horizon
- découpage dans le temps : apprentissage sur les instants dont la cible tombe avant le samedi 13 décembre 2025 à
  0 h UTC, test sur les instants à partir de ce moment (2 jours de week-end et 2 jours de semaine)
- on n'évalue que les couples où la station est en service à t et à t + h
- seules les informations connues à l'instant t servent : la météo est celle de t, pas celle de t + h

## Références

| Nom | Prévision |
|---|---|
| persistance | les vélos de l'instant t |
| profil | moyenne de la station, à l'heure et au type de jour de t + h, calculée sur l'apprentissage |
| persistance corrigée | vélos de t + (profil à t + h − profil à t), bornée entre 0 et la capacité |

## Modèle

- `HistGradientBoostingRegressor` de scikit-learn, un par horizon, qui prévoit la variation de vélos entre t et t + h
- prévision finale : vélos de t + variation prévue, bornée entre 0 et la capacité (vélos + places libres à t)
- réglages fixes et sans tirage aléatoire : 300 itérations, pas de 0,08, 63 feuilles au plus, régularisation L2 de 1
- apprentissage sur un instant sur 3 (toutes les 15 minutes) pour limiter le volume ; test sur tous les instants

Variables, toutes connues à l'instant t :

- état : vélos, vélos électriques, places libres, capacité, taux de remplissage
- passé récent : vélos il y a 5, 15, 30 et 60 minutes, et les variations correspondantes
- calendrier : heure de t et de t + h (sinus et cosinus), jour de la semaine, week-end
- habitudes : profil de la station à t et à t + h, leur différence, profil tous jours confondus à t + h
- station : latitude, longitude
- météo à t : température, pluie, vent

Les profils sont calculés sur l'apprentissage seulement, par station, heure de la journée et type de jour.

## Mesures

Pour chaque horizon et chaque méthode :

- erreur absolue moyenne (MAE) et racine de l'erreur quadratique moyenne (RMSE), en vélos
- gain par rapport à la persistance : 1 − MAE / MAE de la persistance
- erreur moyenne sur le taux de remplissage, en points
- détection des stations vides à t + h (prévision sous 0,5 vélo) : précision, rappel, F1

Et aussi :

- la MAE du modèle et de la persistance pour chacun des 4 jours de test
- l'importance des variables par permutation, pour l'horizon d'1 heure, sur un échantillon de 200 000 couples

## Code

- package `src/velib/forecast/` : `features.py` (grille de 5 minutes, variables, profils, exemples par horizon),
  `baselines.py`, `model.py`, `metrics.py`, `cli.py` (commande `velib-forecast evaluate`)
- scikit-learn dans un nouveau groupe de dépendances `ml`, pour que la collecte reste légère :
  `uv run --group ml velib-forecast evaluate --source kaggle`
- rapport écrit dans `data/forecast/<source>/` : `metrics.json` et `report.md`
- la source se choisit comme pour la heatmap : nom d'une archive importée ou dossier au format de `velib-data`

## Tests

- grille, décalages, cibles et variables de calendrier sur de petites tables construites à la main
- profils calculés sur l'apprentissage seulement
- références et mesures sur des valeurs connues
- modèle : sur des données synthétiques où la variation suit le profil, il bat la persistance
- commande de bout en bout sur une petite archive synthétique

## Publication

Une page « Forecasting » du site résume la méthode et les résultats agrégés, avec l'attribution du jeu Kaggle
(CC BY-SA 4.0). Les données elles-mêmes ne sont pas republiées.

## Hors périmètre

- l'optimisation des réglages, d'autres familles de modèles et la prévision météo
- l'application aux données de `velib-data`, quand elles couvriront plusieurs semaines
