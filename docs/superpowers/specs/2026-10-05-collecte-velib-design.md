# Design de la collecte des données Vélib'

- Date : 5 octobre 2026
- Statut : validé pendant le brainstorming, en relecture
- Périmètre : sous-projet 1 sur 2. L'analyse des usages et le rapport auront leur propre design.

## Objectif

Constituer un historique de la disponibilité des 1 519 stations Vélib' Métropole, avec un relevé toutes les 5 minutes.
Cet historique servira à analyser les usages : rythmes de la journée et de la semaine, stations souvent vides ou pleines,
vélos mécaniques et électriques, flux entre quartiers.
Le dernier relevé alimentera aussi une carte en quasi temps réel, traitée dans le sous-projet 2.

## Critère de réussite

Après 24 heures de collecte, `scripts/heatmap_day.py` trace la heatmap d'une journée à partir des seules données publiées
dans `velib-data` :

- une ligne par station
- une colonne par tranche de 15 minutes, en heure de Paris, soit 96 colonnes
- une couleur par taux de remplissage, égal à vélos disponibles / (vélos disponibles + places libres)

## Contexte et contraintes

Les flux GBFS de Vélib' Métropole ne donnent que l'état actuel des stations. Aucun historique officiel n'existe.
Les archives tierces sont limitées : celle de lovasoa s'est arrêtée en avril 2021, celle de Kaggle couvre du 2 au
16 décembre 2025. Elles serviront à prototyper l'analyse dans le sous-projet 2.

Faits vérifiés le 5 octobre 2026 :

- les flux sont listés par `https://velib-metropole-opendata.smovengo.cloud/opendata/Velib_Metropole/gbfs.json`
- l'API refuse le User-Agent par défaut de Python (`Python-urllib`) avec une erreur 403, et accepte un User-Agent explicite
- un relevé de `station_status` pèse 453 Ko en JSON et 52 Ko en CSV compact
- quelques stations n'ont rien signalé depuis 2021
- le jeu équivalent publié par la Ville de Paris sur data.gouv.fr est sous licence ODbL
- les 1 519 stations ont une latitude et une longitude
- l'API ne renvoie pas d'en-tête CORS : une page web hébergée ailleurs ne peut pas la lire directement
- `raw.githubusercontent.com` autorise la lecture par n'importe quelle page web et garde les fichiers en cache 5 minutes

Contraintes de fonctionnement :

- GitHub Actions est gratuit et illimité seulement sur un dépôt public, d'où 2 dépôts publics
- GitHub désactive les tâches planifiées d'un dépôt public après 60 jours sans activité, ce que les commits de collecte évitent
- GitHub peut retarder ou sauter des tâches planifiées aux heures chargées, donc les relevés ne seront pas parfaitement réguliers

Contraintes d'identité :

- tout se fait sous le compte GitHub `tibzsecondaire`
- les commandes `gh` utilisent la configuration séparée `GH_CONFIG_DIR="$HOME/.config/gh-tibzsecondaire"`
- les push passent par l'alias SSH `github-tibz`
- les commits du dépôt local ont pour auteur `tibzsecondaire`, avec l'adresse noreply du compte

## Architecture

Le projet tient dans 2 dépôts du compte `tibzsecondaire`.

| Dépôt | Visibilité | Contenu |
|---|---|---|
| `velib` | public | package Python `velib`, tests, scripts, puis notebooks, rapport et carte |
| `velib-data` | public | les 2 tâches GitHub Actions et les données, sans code métier |

Les tâches de `velib-data` récupèrent le code de `velib` et lancent ses commandes.
Toute la logique est donc écrite et testée à un seul endroit.

Circulation des données :

```
API GBFS ──(toutes les 5 min)──▶ raw/station_status.csv, un commit par relevé
         ──(chaque nuit)──▶ daily/AAAA-MM-JJ.parquet + daily/index.csv
         ──(velib.dataset)──▶ table station × instant ──▶ heatmap, puis analyses
```

## Contenu du dépôt velib-data

```
velib-data/
├── .github/workflows/collect.yml     relevé toutes les 5 minutes
├── .github/workflows/compact.yml     regroupement quotidien
├── raw/station_status.csv            dernier relevé, réécrit à chaque commit
├── raw/snapshot_meta.json            heures et taille du dernier relevé
├── daily/AAAA-MM-JJ.parquet          une journée UTC, une ligne par station et par relevé
├── daily/index.csv                   bilan de chaque journée
├── stations/station_information.csv  dernières infos fixes des stations
├── .gitignore                        contient code/
├── LICENSE                           texte officiel de l'ODbL 1.0
└── README.md                         source, licence, formats, fréquence
```

## Formats des données

Toutes les heures sont stockées en UTC. La conversion en heure de Paris se fait à l'analyse, pour gérer les changements
d'heure.

`raw/station_status.csv` contient une ligne par station, triée par `station_id` :

| Colonne | Type | Source dans le flux |
|---|---|---|
| `station_id` | entier | `station_id` |
| `mechanical` | entier | `num_bikes_available_types`, clé `mechanical` (0 si absente) |
| `ebike` | entier | `num_bikes_available_types`, clé `ebike` (0 si absente) |
| `docks` | entier | `num_docks_available` |
| `is_installed` | 0 ou 1 | `is_installed` |
| `is_renting` | 0 ou 1 | `is_renting` |
| `is_returning` | 0 ou 1 | `is_returning` |
| `last_reported` | secondes depuis 1970 | `last_reported` |

Le message de chaque commit de relevé suit ce format, que le regroupement relit :

```
snapshot fetched_at=2026-10-05T12:07:31Z feed_updated_at=2026-10-05T12:06:55Z
```

`fetched_at` est l'heure du relevé. `feed_updated_at` vient du champ `lastUpdatedOther` du flux.

`raw/snapshot_meta.json` reprend ces 2 heures et le nombre de stations du dernier relevé :

```json
{"fetched_at": "2026-10-05T12:07:31Z", "feed_updated_at": "2026-10-05T12:06:55Z", "stations": 1519}
```

Il change à chaque relevé. La carte s'en sert pour afficher l'âge des données.

`daily/AAAA-MM-JJ.parquet` reprend les colonnes du CSV, plus `fetched_at` et `feed_updated_at`.
Les heures sont des dates UTC, les compteurs des entiers 16 bits, les statuts des booléens.
La compression est zstd. Le jour est celui de `fetched_at`, en UTC.

`daily/index.csv` contient une ligne par jour, avec ces colonnes :

- `date`
- `snapshots`, le nombre de relevés
- `first_fetch` et `last_fetch`, au format ISO 8601 en UTC
- `max_gap_minutes`, le plus grand écart entre 2 relevés consécutifs du jour
- `stations`, le nombre de stations distinctes
- `rows`, le nombre total de lignes

`stations/station_information.csv` contient `station_id`, `station_code`, `name`, `lat`, `lon` et `capacity`.
Il est réécrit chaque nuit. L'historique git garde les changements.

## Tâches GitHub Actions

Les 2 tâches partagent le groupe de `concurrency` `velib-data`, sans annulation : elles ne poussent jamais en même temps.
Elles ont seulement la permission `contents: write`. Leurs commits ont pour auteur `github-actions[bot]`.

`collect.yml` tourne avec `cron: "2-59/5 * * * *"`, soit à 2, 7, 12 minutes après l'heure et ainsi de suite, pour éviter
l'heure pile. On peut aussi la lancer à la main. Elle s'arrête au bout de 20 minutes.

1. Récupérer la dernière version de `main` de `velib-data`, avec un historique d'un seul commit.
2. Récupérer `velib` au tag `collector-v2` dans `code/`. Le dépôt est public, donc aucune clé n'est nécessaire.
3. Installer l'environnement avec uv, sans les dépendances de développement.
4. Pendant environ 12 minutes, à chaque marque de 5 minutes (2, 7, 12 minutes après l'heure et ainsi de suite) :
   lancer `velib-collect snapshot --output-dir raw`, qui écrit `station_status.csv` et `snapshot_meta.json` puis
   affiche le message de commit ; committer ; pousser. Comme `snapshot_meta.json` change à chaque relevé, chaque
   relevé produit un commit, même si les stations n'ont pas bougé.
5. Si GitHub refuse un push : repartir de la dernière version de `main`, y réappliquer les fichiers `raw/` du
   relevé, committer et réessayer, 3 fois au plus.

`compact.yml` tourne avec `cron: "15 0 * * *"`, soit vers 0 h 15 UTC. On peut la lancer à la main avec un paramètre `day`,
qui vaut la veille par défaut. Elle s'arrête au bout de 15 minutes.

1. Récupérer la dernière version de `main` de `velib-data`, puis l'historique depuis la veille du jour traité avec
   `git fetch --shallow-since`.
2. Récupérer et installer `velib` comme pour la collecte.
3. Lancer `velib-collect compact --day AAAA-MM-JJ`, qui relit les commits du jour, écrit le Parquet, met à jour
   `index.csv` et réécrit `station_information.csv`.
4. Committer avec le message `compact AAAA-MM-JJ` et pousser. Si GitHub refuse, réappliquer de la même façon les
   dossiers `daily/` et `stations/` sur la dernière version de `main`.

Relancer le regroupement d'un jour déjà traité remplace son Parquet et sa ligne d'index.
L'historique brut reste dans git, donc on peut toujours reconstruire une journée.

## Code dans velib

Le package gagne 3 modules, chacun testable seul :

- `velib.gbfs` lit les flux avec un User-Agent explicite et un délai maximal de 20 secondes. Il fait 3 essais au total,
  espacés de 2 puis 4 secondes, en cas d'erreur réseau ou serveur. Il contrôle le contenu : champs attendus, identifiants
  uniques, au moins 1 000 stations
- `velib.collect` porte les commandes `snapshot` et `compact`, exposées par le point d'entrée `velib-collect` de
  `pyproject.toml`
- `velib.dataset` télécharge une journée avec `load_day(day)` et les infos des stations avec `load_stations()`, depuis
  `https://raw.githubusercontent.com/tibzsecondaire/velib-data/main/`, avec un cache local dans `data/cache/`

Le script `scripts/heatmap_day.py` prend une date, appelle `velib.dataset` et écrit la heatmap en HTML interactif dans
`data/figures/`. Les stations y sont triées par taux de remplissage moyen sur la journée.

Dépendances :

- à l'exécution : `httpx` et `polars`, en plus de `python-dotenv`
- dans un nouveau groupe `analysis` : `plotly`, pour que la collecte n'installe pas les bibliothèques de graphiques

## Gestion des erreurs

| Situation | Comportement |
|---|---|
| L'API répond 403 | échec immédiat, sans nouvel essai. Le message évoque un User-Agent refusé |
| Délai dépassé, erreur réseau ou erreur 5xx | 3 essais, puis échec |
| Format inattendu, identifiants en double ou moins de 1 000 stations | échec, rien n'est committé |
| Stations inchangées depuis le relevé précédent | commit quand même, car `snapshot_meta.json` garde l'heure du relevé |
| Push refusé à cause d'une autre tâche | réapplication des fichiers sur la dernière version de `main`, 3 fois au plus |
| Relevés retardés ou sautés par GitHub | visibles dans `index.csv`. L'analyse regroupe par tranches de 15 minutes |
| Échec du regroupement | relance manuelle avec le paramètre `day` |
| Station muette depuis plus de 24 heures | conservée telle quelle. L'analyse la signale |

GitHub envoie un e-mail au compte `tibzsecondaire` à chaque échec d'une tâche planifiée.

## Sécurité

- le projet n'a aucun secret, car les 2 dépôts sont publics
- les tâches poussent dans `velib-data` avec le jeton que GitHub Actions fournit à chaque exécution, limité à
  `contents: write`

## Tests

Les tests vivent dans `velib`, avec pytest, et ne font aucun appel réseau :

- `velib.gbfs` : lecture de vraies réponses enregistrées dans `tests/fixtures/` et réduites à une vingtaine de stations,
  puis cas d'erreur simulés avec `httpx.MockTransport` (403, délai dépassé, format inattendu, trop peu de stations)
- `snapshot` : colonnes, tri et contenu du CSV, contenu de `snapshot_meta.json`, format du message de commit
- `compact` : dépôt git temporaire avec des commits fictifs de part et d'autre de minuit UTC, puis contrôle du Parquet
  (lignes, types, heures UTC) et de `index.csv` (nombre de relevés, plus grand écart)
- `velib.dataset` : chargement depuis un dossier local à la place de GitHub, et utilisation du cache

Les fichiers `test_dummy.py` du squelette disparaissent quand ces tests arrivent.

## Mise en service

1. Développer sur une branche de `velib`, puis fusionner quand les tests passent.
2. Poser le tag `collector-v1`. Pour changer le code de collecte plus tard, poser un nouveau tag et mettre à jour les 2 tâches.
3. Ajouter dans `velib-data` le README, la licence, le `.gitignore` et les 2 tâches.
4. Lancer un premier relevé à la main et vérifier le commit.
5. Après 24 heures, lancer ou attendre le regroupement, lire `index.csv` et tracer la heatmap.
6. Après une semaine, comparer la croissance du dépôt aux estimations.

## Volume attendu

Ces chiffres sont des estimations, à vérifier après une semaine :

- git ne stocke que les lignes modifiées d'un relevé à l'autre, soit environ 1 Mo par jour si 10 à 20% des stations
  changent en 5 minutes
- les fichiers Parquet ajoutent environ 1 Mo par jour
- le dépôt grossit donc de moins de 1 Go par an, le seuil que GitHub recommande de ne pas dépasser

Si la croissance mesurée dépasse 3 Mo par jour, on revoit la fréquence des relevés ou le stockage de l'historique ancien.

## Licences

Le code de `velib` est sous licence MIT.

Le README de `velib-data` cite la source : les flux GBFS de Vélib' Métropole, publiés en open data.
La base dérivée est publiée sous ODbL 1.0, comme le jeu de la Ville de Paris sur data.gouv.fr.
La page Vélib' bloque les accès automatisés, donc sa licence n'a pas pu être lue. Il faudra la vérifier à la main.

## Hors périmètre

Ce sous-projet ne couvre pas :

- l'analyse des usages et le rapport publié, traités dans le sous-projet 2
- l'utilisation des archives Kaggle et lovasoa
- une intégration continue sur `velib`
- un stockage externe, sauf si le volume dépasse le seuil ci-dessus
- la carte en quasi temps réel, traitée dans le sous-projet 2

Pour la carte, le sous-projet 2 part de ces contraintes connues :

- c'est une page statique, publiée avec le rapport sur le GitHub Pages de `velib`
- elle lit `raw/station_status.csv`, `raw/snapshot_meta.json` et `stations/station_information.csv` sur
  `raw.githubusercontent.com`, avec 5 à 15 minutes de décalage
- `velib` ne reçoit pas les commits de collecte, donc la limite de 10 publications par heure de GitHub Pages ne le gêne pas

Quand MkDocs sera configuré, il faudra exclure `docs/superpowers/` du site publié.

## Ajustements après la mise en service

- 5 octobre 2026 : un relevé resté en file d'attente récupérait `velib-data` tel qu'il était à sa création, et un
  `git pull --rebase` entre 2 relevés entre toujours en conflit sur `raw/`. Les tâches récupèrent donc la dernière
  version de `main` et réappliquent leurs fichiers quand un push est refusé.
- 6 octobre 2026 : GitHub n'exécute la planification « toutes les 5 minutes » qu'environ tous les quarts d'heure
  (écart médian mesuré de 14 minutes entre 0 h et 7 h UTC). Chaque passage fait donc plusieurs relevés, sur les
  marques de 5 minutes, pendant environ 12 minutes.
