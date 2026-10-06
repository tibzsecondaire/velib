# Design de l'import des archives Vélib'

- Date : 6 octobre 2026
- Statut : décidé en mode autonome, à la demande de l'auteur
- Périmètre : sous-projet 1b. Il complète la collecte avec deux archives publiques, pour analyser dès maintenant
  des journées complètes et disposer de plus de données pour l'IA.

## Objectif

Convertir les deux archives au format des données de `velib-data`, pour que `load_day`, `load_stations` et le script
de heatmap fonctionnent sans changement sur elles.

## Critère de réussite

`scripts/heatmap_day.py --source kaggle 2025-12-10` trace la heatmap complète du 10 décembre 2025, et
`--source lovasoa 2021-02-10` celle du 10 février 2021.

## Archives retenues

Faits vérifiés le 6 octobre 2026 :

| | Kaggle « velib_data » | lovasoa « historique-velib-opendata » |
|---|---|---|
| Adresse | `https://www.kaggle.com/api/v1/datasets/download/adrienmorel97/velib-data` | `https://github.com/lovasoa/historique-velib-opendata/releases/latest/download/stations.zip` |
| Accès | téléchargement anonyme, zip de 13 Mo | téléchargement anonyme, zip de 240 Mo |
| Contenu | `velib_concat.parquet`, 6 353 181 lignes | `historique_stations.csv`, 818 Mo, sans en-tête |
| Période | du 2 au 16 décembre 2025 | du 26 novembre 2020 au 9 avril 2021 |
| Fréquence | 5 minutes, 4 227 créneaux | 15 minutes, 7 866 relevés |
| Stations | 1 503, identifiants GBFS actuels (99,6 % existent encore) | 1 406 couples nom et coordonnées, sans identifiant |
| Licence | CC BY-SA 4.0 | dépôt sous GPL-3.0, données issues de l'open data Vélib' |

L'archive de 2017 (bfontaine) n'est pas retenue : elle vient de l'ancien système Vélib', remplacé en 2018.

## Conversion

Chaque archive devient un dossier local `data/archives/<nom>/` organisé comme `velib-data` :

- `daily/AAAA-MM-JJ.parquet`, au schéma du regroupement quotidien
- `daily/index.csv`, calculé avec les mêmes fonctions que le regroupement
- `stations/station_information.csv`, aux colonnes de `velib-data`
- pour Kaggle seulement, `weather.parquet` : `time_bin`, `temp_c`, `precip_mm`, `wind_mps`, une ligne par créneau

Correspondance des colonnes :

| Colonne | Kaggle | lovasoa |
|---|---|---|
| `fetched_at` | `ts_utc`, heure UTC | `date`, heure UTC |
| `feed_updated_at` | absente : vide | absente : vide |
| `station_id` | `station_id` | station actuelle la plus proche à moins de 100 mètres, sinon même nom, sinon identifiant négatif stable tiré du nom |
| `mechanical`, `ebike` | `mechanical`, `ebike` | `mechanical`, `electrical` |
| `docks` | `capacity - bikes`, au moins 0 | `capacity - mechanical - electrical`, au moins 0 |
| `is_installed`, `is_renting`, `is_returning` | `status == "OK"` | `operative` |
| `last_reported` | absente : vide | absente : vide |

Comme les archives ne donnent pas les places libres, le taux de remplissage y est en pratique vélos / capacité.
La documentation le signale.

## Code

- `src/velib/archives.py` : téléchargement, conversion des 2 archives (fonctions pures sur des tables polars),
  écriture du dossier, et commande `velib-archives import kaggle|lovasoa [--zip CHEMIN] [--out DOSSIER]`
- `load_day` et `load_stations` lisent directement une source locale, sans la recopier dans le cache
- `scripts/heatmap_day.py` gagne l'option `--source`, qui vaut `velib-data` par défaut, ou le nom d'une archive
  importée, ou un dossier

## Données publiées ou non

Les archives converties restent locales, dans `data/` qui n'est pas versionné. Les 2 licences demandent de citer la
source, et CC BY-SA impose aussi la même licence aux données dérivées : les republier dans `velib-data`, sous ODbL,
poserait un conflit. Chacun les régénère avec `velib-archives import`.

## Tests

- conversion Kaggle et lovasoa sur de petites tables construites à la main : colonnes, types, places libres,
  statuts, correspondance des stations, météo
- écriture du dossier : un fichier par jour UTC, index, stations
- lecture d'une source locale sans cache
- aucun appel réseau

## Hors périmètre

- la publication des archives converties
- les analyses qui comparent les périodes, traitées dans le sous-projet 2b
