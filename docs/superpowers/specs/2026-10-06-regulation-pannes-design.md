# Régulation et pannes : base DuckDB et deux détections

Date : 6 octobre 2026. Piste choisie par l'utilisateur parmi les usages d'une base de données, conçue en
mode automatique.

## Objectif

Répondre à deux questions avec les relevés de 5 minutes, sans identifiant de vélo :

1. Où et quand l'opérateur ajoute-t-il ou retire-t-il des vélos par camion ?
2. Combien de vélos et de places sont hors service, et où ?

Et donner une base de données SQL pour explorer toutes les données, sans serveur.

`velib-ops report --source kaggle` produit un rapport et une carte qui répondent aux deux questions. Les
interventions détectées doivent tomber nettement plus la nuit que l'activité des usagers.

## Données, vérifiées le 6 octobre 2026

- Kaggle : 6 269 013 pas entre deux relevés consécutifs d'une station espacés de 7 minutes au plus.
- Variation du nombre de vélos sur un pas : médiane 0, 99e centile 3, 99,9e centile 5. 1 805 pas de
  8 vélos ou plus, 830 de 10 ou plus, 376 de 12 ou plus.
- Les pas de 10 vélos ou plus tombent surtout la nuit : 3 h pèse 1,0 % de l'activité et 14,0 % de ces pas,
  19 h pèse 7,3 % de l'activité et 0,1 % de ces pas.
- Électriques : dans 52 % des journées où une station voit partir au moins 10 électriques, elle n'en descend
  jamais sous 1. Ces journées s'enchaînent : 51 stations le sont les 14 jours complets, et 632 des 686 séries
  de 5 jours ou plus gardent exactement 1 électrique.
- Mécaniques : 49 % des journées actives, avec des planchers plus variés (1 à 6 vélos), en partie un simple
  surplus de vélos.
- Temps passé au plancher : avec un plancher de 1 ou 2 vélos, la station y reste 145 minutes par jour en
  médiane pour les électriques et 80 pour les mécaniques. Avec un plancher de 6 vélos ou plus, 55 et
  25 minutes, et le plancher vaut 67 % du niveau médian de la journée : c'est un surplus, pas des vélos
  bloqués.
- Le flux en direct ne publie ni vélos ni bornes hors service. Capacité − vélos − bornes libres est positif
  dans 56 % des stations du dernier relevé : 1 510 places sur 49 913 (3,0 %). 17 stations donnent une
  valeur négative, ramenée à 0.
- Les archives n'ont pas de vraies bornes libres (estimées par capacité − vélos) : les places hors service ne
  se mesurent que sur velib-data.
- DuckDB 1.5 lit les 6,3 millions de relevés Kaggle en 0,5 seconde. Polars lit ses résultats par l'interface
  Arrow, sans pyarrow.

## Conception

### Base de données : `velib.db`

- DuckDB en mémoire, sur les fichiers Parquet existants : rien à héberger, rien à recopier.
- `connect(source)` crée trois vues :
  - `snapshots` : toutes les lignes de `daily/*.parquet` ;
  - `stations` : `stations/station_information.csv` ;
  - `steps` : chaque relevé avec l'heure de Paris, les minutes depuis le relevé précédent de la station et
    les variations de vélos, de mécaniques et d'électriques.
- Source velib-data : `dataset.local_copy` télécharge dans `data/cache` les jours de l'index absents du
  cache et le fichier des stations, puis les vues lisent cette copie.
- `velib-db sql --source kaggle "SELECT …"` affiche le résultat d'une requête, et `--csv` l'enregistre.

### Camions de régulation : `velib.ops.regulation`

- Un pas est une intervention quand le nombre de vélos change d'au moins 8 entre deux relevés espacés de
  7 minutes au plus. Le seuil est au 99,97e centile des pas.
- Les pas d'intervention consécutifs de même sens d'une station forment une seule intervention.
- Sortie : station, premier et dernier relevé de l'intervention (heure de Paris), vélos déplacés (positif
  pour un ajout), vélos avant et après, capacité.
- Résumé : interventions et vélos ajoutés ou retirés par jour, part des interventions par heure comparée à
  la part de l'activité, stations les plus réapprovisionnées et les plus vidées, remplissage avant les ajouts
  et avant les retraits, nombre d'interventions aux seuils 8, 10 et 12.

### Pannes : `velib.ops.breakdowns`

- Vélos immobilisés, par station, jour de Paris et type de vélo :
  - le plancher est le minimum du type sur la journée ;
  - une journée compte si elle a au moins 200 relevés et au moins 10 départs du type, et si son plancher
    vaut 1 ou 2 vélos et dure au moins 12 relevés, une heure de relevés de 5 minutes ;
  - au moins 3 journées consécutives forment une immobilisation, dont le nombre de vélos est le plus petit
    plancher de la série.
  - Première version sans plafond ni durée : les plus longues « immobilisations » comptaient 16 à 31
    mécaniques dans de grandes stations qui ne se vident jamais. Le plafond de 2 vélos et l'heure au
    plancher écartent ce surplus.
- Places hors service, sur velib-data seulement : capacité − vélos − bornes libres, ramené à 0. Moyenne par
  station et part du temps avec au moins une place hors service. Une source sans `feed_updated_at`
  (les archives) n'a pas de vraies bornes libres et saute cette partie.

### Rapport : `velib-ops report --source kaggle`

- `data/ops/<source>/report.md` : les tableaux des deux analyses.
- `data/ops/<source>/map.html` : carte MapLibre autonome, en français, avec trois vues : interventions
  (cercle proportionnel aux vélos déplacés, bleu si la station reçoit plus qu'elle ne perd, orange sinon),
  vélos immobilisés, places hors service quand la source les a.

### Dépendance

- `duckdb` dans un nouveau groupe `db` : `uv run --group db velib-ops report --source kaggle`. La collecte,
  figée sur l'étiquette `collector-v2`, n'est pas touchée.

## Tests

- Fixtures minuscules en Parquet et CSV dans un dossier temporaire.
- `connect` : les trois vues, les variations et les écarts de `steps`, l'heure de Paris.
- `local_copy` : téléchargement des jours manquants une seule fois, avec un transport HTTP simulé.
- Interventions : seuil, écart maximal, sens, fusion des pas consécutifs.
- Immobilisations : plancher, journées actives, séries d'au moins 3 jours, type de vélo.
- Places hors service : calcul, valeur négative ramenée à 0, absence sur les archives.
- Rapport et carte : contenu du Markdown, données de la carte embarquées sans risque.
- Vérification visuelle de la carte dans WebKit, puis outil supprimé.

## Hors périmètre

- Les vrais trajets : le flux ne donne pas d'identifiant de vélo.
- La confirmation des interventions par l'opérateur.
- La météo, les grèves et les vacances (piste « causes extérieures »).
- La publication de la carte Kaggle : l'archive n'est pas republiée (licence CC BY-SA).

## Résultats du 6 octobre 2026

Kaggle, du 2 au 16 décembre 2025 :

- 1 768 interventions en 15 jours, environ 120 par jour : 12 923 vélos ajoutés et 5 022 retirés. Aux seuils
  10 et 12 : 827 et 376 interventions.
- 70 % des interventions ont lieu entre 21 h et 5 h, contre 19 % de l'activité : le critère de réussite est
  atteint.
- Juste avant une intervention, les stations qui reçoivent des vélos sont remplies à 23 %, celles qui en
  perdent à 72 %.
- L'opérateur vide le centre et l'ouest proche, et remplit la périphérie, le nord et l'est. Il ajoute
  2,6 fois plus de vélos par gros lots qu'il n'en retire : les retraits se font sans doute par petits lots,
  que le seuil ne voit pas.
- Vélos immobilisés : 770 immobilisations d'électriques dans 641 stations, environ 210 électriques bloqués
  par jour ; 128 de mécaniques dans 113 stations, environ 40 par jour. Certains électriques restent les
  15 jours.

velib-data, nuit du 5 au 6 octobre 2026, 2 h 30 compactées :

- environ 1 640 places hors service en moyenne sur 49 913 (3,3 %), dans 57 % des stations ; quelques
  stations entièrement hors service, comme Marignan - Champs-Élysées (40 places sur 40).
