# Design du site et de la carte en quasi temps réel

- Date : 5 octobre 2026
- Statut : validé (structure du site approuvée ; le reste décidé en mode autonome, à la demande de l'auteur)
- Périmètre : sous-projet 2a. Les analyses de tendances (2b) et l'IA (3) auront leurs propres designs.

## Objectif

Publier un site en anglais qui aide à observer les tendances des Vélib'. Il commence avec une carte des 1 519 stations,
colorées selon leur taux de remplissage, mise à jour à partir du dernier relevé de `velib-data`.
Le site accueillera ensuite les analyses de tendances, puis des modèles d'IA.

## Critère de réussite

La page « Live map » de `https://tibzsecondaire.github.io/velib/` affiche les stations du dernier relevé, colorées
selon leur taux de remplissage, avec l'heure du relevé en heure de Paris. Les totaux affichés correspondent au
contenu de `raw/station_status.csv`.

## Contraintes vérifiées le 5 octobre 2026

- l'API Vélib' n'autorise pas la lecture depuis un navigateur sur un autre site ; `raw.githubusercontent.com`
  l'autorise (`access-control-allow-origin: *`) et garde les fichiers en cache 5 minutes
- MapLibre GL JS 6.12.0 (licence BSD-3) n'est publié qu'en modules : `dist/maplibre-gl.mjs`, qui exporte `Map`, `Popup`,
  `NavigationControl`, `AttributionControl` et `ScaleControl`, sans export par défaut
- quand son worker vient d'un autre site, MapLibre 6 démarre un worker local qui l'importe : le chargement depuis
  jsDelivr fonctionne
- OpenFreeMap fournit des styles vectoriels gratuits, sans clé ni limite, avec des données OpenStreetMap ; le style
  `positron` répond et autorise la lecture depuis n'importe quel site
- MkDocs 1.6.1 et Material 9.7.7 sont déjà dans le groupe de dépendances `doc`
- GitHub Pages ne limite pas les publications faites par une tâche GitHub Actions dédiée

## Structure du site

- MkDocs avec le thème Material, en anglais, titre « Vélib' trends »
- `mkdocs.yml` à la racine du dépôt, pages dans `docs/`
- 3 pages :
  - `index.md` (Home) : le but, ce que contient le site, les liens vers les 2 dépôts
  - `map.md` (Live map) : la carte en pleine largeur
  - `data.md` (Data) : sources, fichiers de `velib-data`, fréquence, limites connues, licence ODbL
- `exclude_docs` retire `superpowers/` du site publié
- la navigation instantanée de Material reste désactivée, car elle empêcherait le script de la carte de se lancer

## Carte

Bibliothèques :

- MapLibre GL JS 6.12.0, importé depuis `https://cdn.jsdelivr.net/npm/maplibre-gl@6.12.0/dist/`
- fond de carte OpenFreeMap `positron`, clair et neutre pour laisser ressortir les couleurs des stations
- aucune autre dépendance : la lecture des CSV est faite par une petite fonction testée

Données :

- la page lit `raw/station_status.csv`, `raw/snapshot_meta.json` et `stations/station_information.csv` depuis
  `https://raw.githubusercontent.com/tibzsecondaire/velib-data/main/`
- le paramètre d'adresse `?data=<url>` remplace cette source, pour tester avec des fichiers locaux ; il n'accepte que
  des adresses `http` ou `https`
- elle relit les fichiers toutes les 5 minutes, et quand l'onglet redevient visible
- elle joint les 2 CSV par `station_id` ; une station sans coordonnées est ignorée et comptée à part

Affichage :

- chaque station est un cercle dont la taille suit le zoom
- couleur : taux de remplissage = vélos / (vélos + places libres), de rouge (0 %) à jaune (50 %) puis vert (100 %),
  la même échelle que la heatmap
- gris : station hors service (`is_installed` ou `is_renting` à 0) ou sans vélo ni place
- un bandeau au-dessus de la carte donne l'heure du relevé en heure de Paris, le nombre de stations, de vélos (dont
  électriques) et de places libres
- si le relevé a plus de 20 minutes, le bandeau prévient que les données sont peut-être en retard
- un clic sur une station ouvre une bulle : nom, taux de remplissage, vélos mécaniques, vélos électriques, places
  libres, capacité, heure du dernier signal de la station
- une légende rappelle l'échelle de couleurs
- les noms de stations sont échappés avant d'entrer dans le HTML de la bulle

Erreurs :

- si un fichier ne se charge pas, le bandeau l'indique, la carte garde les données précédentes et réessaie au
  rafraîchissement suivant
- tant que le premier regroupement nocturne n'a pas publié `stations/station_information.csv`, le bandeau explique
  que les positions des stations ne sont pas encore disponibles

## Code

- `docs/javascripts/map-core.mjs` : fonctions pures, sans DOM ni MapLibre (lecture CSV, jointure, taux de
  remplissage, totaux, heure de Paris, ancienneté du relevé, échappement HTML, contenu de la bulle)
- `docs/javascripts/map.mjs` : branchement sur la page (carte, chargement, rafraîchissement, bandeau, légende, bulle)
- `docs/stylesheets/map.css` : mise en page de la carte, du bandeau et de la légende

## Tests

- `tests/js/map-core.test.mjs` teste toutes les fonctions de `map-core.mjs` avec le lanceur de tests intégré à Node
  (`node --test`), sans dépendance
- `make test-js` lance ces tests ; un hook pre-commit local les lance aussi quand un fichier `.mjs` change
- `tests/integration/test_site.py` construit le site avec `mkdocs build --strict` ; il est ignoré si MkDocs n'est pas
  installé
- la tâche de publication construit aussi le site en mode strict
- vérification visuelle : construire le site, le servir en local avec des données locales passées par `?data=`, et
  regarder la carte dans un navigateur

## Publication

- `.github/workflows/pages.yml` dans `velib` : sur un push dans `main` qui touche `docs/`, `mkdocs.yml` ou la tâche,
  ou à la main
- construction avec `uv run --only-group doc mkdocs build --strict`, puis `actions/upload-pages-artifact@v5` et
  `actions/deploy-pages@v5`
- permissions `contents: read`, `pages: write` et `id-token: write`, groupe de `concurrency` `pages`
- GitHub Pages configuré avec la source « GitHub Actions »

## Hors périmètre

- les analyses de tendances et leurs pages (sous-projet 2b), dont la heatmap, dont les libellés passeront en anglais
- une rediffusion de la journée sur la carte, qui demandera un format de données adapté au navigateur
- la prédiction de disponibilité par IA (sous-projet 3)
