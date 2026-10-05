# Plan d'implémentation du site et de la carte

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publier sur GitHub Pages un site MkDocs en anglais avec une carte MapLibre des stations Vélib', colorées
selon leur taux de remplissage d'après le dernier relevé de `velib-data`.

**Architecture:** Le site vit dans `docs/` et `mkdocs.yml`. La carte est une page MkDocs qui charge MapLibre depuis
jsDelivr et deux modules : `map-core.mjs` (fonctions pures, testées avec `node --test`) et `map.mjs` (branchement sur
la page). Une tâche GitHub Actions construit le site en mode strict et le publie.

**Tech Stack:** MkDocs 1.6.1, Material 9.7.7, MapLibre GL JS 6.12.0, OpenFreeMap `positron`, Node 26 (`node:test`),
GitHub Actions (`actions/upload-pages-artifact@v5`, `actions/deploy-pages@v5`).

**Spec:** `docs/superpowers/specs/2026-10-05-site-carte-design.md`

## Global Constraints

- site en anglais ; titre « Vélib' trends » ; adresse `https://tibzsecondaire.github.io/velib/`
- données lues depuis `https://raw.githubusercontent.com/tibzsecondaire/velib-data/main/`, remplaçables par `?data=`
  (adresses `http` ou `https` seulement)
- rafraîchissement toutes les 5 minutes et au retour sur l'onglet ; relevé « en retard » au-delà de 20 minutes
- couleurs : 0 → `#d7191c`, 0,5 → `#fee08b`, 1 → `#1a9641`, indisponible → `#9e9e9e`
- noms de stations toujours échappés avant d'entrer dans du HTML
- `docs/superpowers/` exclu du site ; navigation instantanée de Material désactivée
- mêmes règles que le plan de la collecte : identité `tibzsecondaire`, `gh` via
  `GH_CONFIG_DIR="$HOME/.config/gh-tibzsecondaire"`, contrôle `.git/info/private-identifiers` avant chaque push,
  Conventional Commits terminés par `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`
- chaque fichier de code de ce plan est précédé d'un commentaire `<!-- file: chemin -->` ; les fichiers sont extraits
  du plan par le script de l'étape 1 de la tâche 1, pour que plan et code restent identiques

---

### Task 1: Fonctions pures de la carte

**Files:**
- Create: `tests/js/map-core.test.mjs`, `docs/javascripts/map-core.mjs`
- Modify: `Makefile` (cible `test-js`), `.pre-commit-config.yaml` (hook local `node-tests`)

**Interfaces:**
- Produces (`docs/javascripts/map-core.mjs`) : `DEFAULT_DATA_URL`, `STALE_AFTER_MINUTES`, `RATE_COLORS`,
  `UNAVAILABLE_COLOR`, `parseCsv(text) -> string[][]`, `csvRecords(text) -> object[]`, `fillRate(counts) -> number|null`,
  `buildStations(statusText, informationText) -> {features, missing}`, `summarize(features) -> {stations, bikes, ebikes, docks}`,
  `parisTime(date) -> "HH:MM"`, `isStale(isoTime, now, maxMinutes?) -> boolean`, `escapeHtml(text) -> string`,
  `popupHtml(properties) -> string`, `bannerText(meta, totals, missing) -> string`, `dataBaseUrl(search, fallback?) -> string`,
  `circleColorExpression() -> array`

- [ ] **Step 1: Créer la branche et le script d'extraction**

```bash
git switch -c feat/site-map
```

Le script suivant écrit chaque fichier dont le chemin est donné, à partir du bloc de code qui suit son marqueur :

```bash
python3 - "$@" <<'PY'
import re
import sys
from pathlib import Path

plan = Path("docs/superpowers/plans/2026-10-05-site-carte.md").read_text(encoding="utf-8")
pattern = r"<!-- file: (\S+) -->\n(````?)\w*\n(.*?)\n\2\n"
blocks = {name: body for name, _fence, body in re.findall(pattern, plan, flags=re.S)}
for name in sys.argv[1:]:
    path = Path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(blocks[name] + "\n", encoding="utf-8")
    print("wrote", name)
PY
```

- [ ] **Step 2: Écrire les tests qui échouent**

<!-- file: tests/js/map-core.test.mjs -->
```javascript
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  DEFAULT_DATA_URL,
  bannerText,
  buildStations,
  circleColorExpression,
  csvRecords,
  dataBaseUrl,
  escapeHtml,
  fillRate,
  isStale,
  parisTime,
  parseCsv,
  popupHtml,
  summarize,
} from "../../docs/javascripts/map-core.mjs";

const STATUS = [
  "station_id,mechanical,ebike,docks,is_installed,is_renting,is_returning,last_reported",
  "1,3,1,4,1,1,1,1791230000",
  "2,0,0,0,1,1,1,1791230000",
  "3,2,0,8,1,0,1,1791230000",
  "4,1,1,1,1,1,1,1791230000",
].join("\n");

const INFORMATION = [
  "station_id,station_code,name,lat,lon,capacity",
  '1,16107,"Godard, Victor Hugo",48.865983,2.275725,35',
  "2,00002,Empty,48.85,2.35,20",
  "3,00003,Closed,48.86,2.36,10",
].join("\n");

test("parseCsv handles quotes, doubled quotes, CRLF and blank lines", () => {
  assert.deepEqual(parseCsv('a,b\r\n"x, y","say ""hi"""\n\n'), [
    ["a", "b"],
    ["x, y", 'say "hi"'],
  ]);
});

test("csvRecords keys each row by the header", () => {
  assert.deepEqual(csvRecords("id,name\n1,A\n"), [{ id: "1", name: "A" }]);
  assert.deepEqual(csvRecords(""), []);
});

test("fillRate is bikes over bikes plus docks, null when closed or empty", () => {
  const open = { isInstalled: true, isRenting: true };
  assert.equal(fillRate({ ...open, mechanical: 3, ebike: 1, docks: 4 }), 0.5);
  assert.equal(fillRate({ ...open, mechanical: 0, ebike: 0, docks: 0 }), null);
  assert.equal(fillRate({ mechanical: 2, ebike: 0, docks: 8, isInstalled: true, isRenting: false }), null);
});

test("buildStations joins by station_id and counts stations without position", () => {
  const { features, missing } = buildStations(STATUS, INFORMATION);
  assert.equal(missing, 1);
  assert.deepEqual(
    features.map((feature) => feature.properties.id),
    [1, 2, 3],
  );
  assert.deepEqual(features[0].geometry.coordinates, [2.275725, 48.865983]);
  assert.equal(features[0].properties.name, "Godard, Victor Hugo");
  assert.equal(features[0].properties.rate, 0.5);
  assert.equal(features[1].properties.rate, -1);
  assert.equal(features[2].properties.available, false);
});

test("summarize adds up stations, bikes, e-bikes and docks", () => {
  const { features } = buildStations(STATUS, INFORMATION);
  assert.deepEqual(summarize(features), { stations: 3, bikes: 6, ebikes: 1, docks: 12 });
});

test("parisTime shows the Paris wall clock in summer and winter", () => {
  assert.equal(parisTime(new Date("2026-10-05T21:05:00Z")), "23:05");
  assert.equal(parisTime(new Date("2026-12-05T21:05:00Z")), "22:05");
});

test("isStale flags snapshots older than 20 minutes", () => {
  const now = new Date("2026-10-05T21:30:00Z");
  assert.equal(isStale("2026-10-05T21:15:00Z", now), false);
  assert.equal(isStale("2026-10-05T21:05:00Z", now), true);
});

test("escapeHtml escapes the five HTML special characters", () => {
  assert.equal(
    escapeHtml(`<a href="x">Tom & Jerry's</a>`),
    "&lt;a href=&quot;x&quot;&gt;Tom &amp; Jerry&#39;s&lt;/a&gt;",
  );
});

test("popupHtml escapes the station name and shows the fill rate", () => {
  const html = popupHtml({
    name: "<b>A</b>",
    available: true,
    rate: 0.25,
    mechanical: 1,
    ebike: 0,
    docks: 3,
    capacity: 4,
    lastReported: 1791230000,
  });
  assert.match(html, /&lt;b&gt;A&lt;\/b&gt;/);
  assert.match(html, /25% full/);
  assert.doesNotMatch(html, /<b>A<\/b>/);
});

test("bannerText summarises the snapshot in Paris time", () => {
  const totals = { stations: 1519, bikes: 18841, ebikes: 7256, docks: 29724 };
  assert.equal(
    bannerText({ fetched_at: "2026-10-05T21:05:00Z" }, totals, 0),
    "Snapshot of 23:05 (Paris time) · 1,519 stations · 18,841 bikes, including 7,256 e-bikes · 29,724 free docks",
  );
  assert.match(bannerText({ fetched_at: "2026-10-05T21:05:00Z" }, totals, 2), /2 stations without position$/);
});

test("dataBaseUrl accepts http and https overrides only", () => {
  assert.equal(dataBaseUrl(""), DEFAULT_DATA_URL);
  assert.equal(dataBaseUrl("?data=http://localhost:8000/testdata"), "http://localhost:8000/testdata/");
  assert.equal(dataBaseUrl("?data=javascript:alert(1)"), DEFAULT_DATA_URL);
  assert.equal(dataBaseUrl("?data=not a url"), DEFAULT_DATA_URL);
});

test("circleColorExpression greys out unavailable stations", () => {
  const expression = circleColorExpression();
  assert.equal(expression[0], "case");
  assert.deepEqual(expression[1], ["<", ["get", "rate"], 0]);
  assert.equal(expression[2], "#9e9e9e");
  assert.deepEqual(expression[3].slice(0, 3), ["interpolate", ["linear"], ["get", "rate"]]);
});
```

- [ ] **Step 3: Vérifier que les tests échouent**

Run: `node --test "tests/js/*.test.mjs"`
Expected: FAIL avec `ERR_MODULE_NOT_FOUND` pour `docs/javascripts/map-core.mjs`.

- [ ] **Step 4: Écrire les fonctions pures**

<!-- file: docs/javascripts/map-core.mjs -->
```javascript
// Pure helpers for the live map: no DOM and no MapLibre, so that `node --test` can check them.

export const DEFAULT_DATA_URL = "https://raw.githubusercontent.com/tibzsecondaire/velib-data/main/";
export const STALE_AFTER_MINUTES = 20;
export const RATE_COLORS = [
  [0, "#d7191c"],
  [0.5, "#fee08b"],
  [1, "#1a9641"],
];
export const UNAVAILABLE_COLOR = "#9e9e9e";

const PARIS_CLOCK = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Europe/Paris",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});
const NUMBER_FORMAT = new Intl.NumberFormat("en-US");
const HTML_ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

/** Parses CSV text with quoted fields, doubled quotes and LF or CRLF line ends; skips blank lines. */
export function parseCsv(text) {
  const rows = [];
  let row = [];
  let field = "";
  let inQuotes = false;
  const endRow = () => {
    row.push(field);
    if (row.length > 1 || row[0] !== "") {
      rows.push(row);
    }
    row = [];
    field = "";
  };
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (inQuotes) {
      if (char === '"' && text[index + 1] === '"') {
        field += '"';
        index += 1;
      } else if (char === '"') {
        inQuotes = false;
      } else {
        field += char;
      }
    } else if (char === '"') {
      inQuotes = true;
    } else if (char === ",") {
      row.push(field);
      field = "";
    } else if (char === "\n" || char === "\r") {
      if (char === "\r" && text[index + 1] === "\n") {
        index += 1;
      }
      endRow();
    } else {
      field += char;
    }
  }
  if (field !== "" || row.length > 0) {
    endRow();
  }
  return rows;
}

/** Turns CSV text with a header line into objects keyed by column name. */
export function csvRecords(text) {
  const [header, ...rows] = parseCsv(text);
  if (!header) {
    return [];
  }
  return rows.map((row) => Object.fromEntries(header.map((name, index) => [name, row[index] ?? ""])));
}

/** Fill rate of a station: bikes / (bikes + free docks), or null when closed or without bikes and docks. */
export function fillRate({ mechanical, ebike, docks, isInstalled, isRenting }) {
  const bikes = mechanical + ebike;
  const total = bikes + docks;
  if (!isInstalled || !isRenting || total === 0) {
    return null;
  }
  return bikes / total;
}

/** Joins the station status and information CSV files into GeoJSON point features. */
export function buildStations(statusText, informationText) {
  const information = new Map(csvRecords(informationText).map((record) => [record.station_id, record]));
  const features = [];
  let missing = 0;
  for (const status of csvRecords(statusText)) {
    const info = information.get(status.station_id);
    const lat = info && info.lat !== "" ? Number(info.lat) : Number.NaN;
    const lon = info && info.lon !== "" ? Number(info.lon) : Number.NaN;
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
      missing += 1;
      continue;
    }
    const counts = {
      mechanical: Number(status.mechanical),
      ebike: Number(status.ebike),
      docks: Number(status.docks),
      isInstalled: status.is_installed === "1",
      isRenting: status.is_renting === "1",
    };
    const rate = fillRate(counts);
    features.push({
      type: "Feature",
      geometry: { type: "Point", coordinates: [lon, lat] },
      properties: {
        id: Number(status.station_id),
        name: info.name,
        capacity: Number(info.capacity),
        mechanical: counts.mechanical,
        ebike: counts.ebike,
        docks: counts.docks,
        available: rate !== null,
        rate: rate === null ? -1 : rate,
        lastReported: Number(status.last_reported),
      },
    });
  }
  return { features, missing };
}

/** Adds up the stations, bikes, e-bikes and free docks of the features. */
export function summarize(features) {
  return features.reduce(
    (totals, { properties }) => ({
      stations: totals.stations + 1,
      bikes: totals.bikes + properties.mechanical + properties.ebike,
      ebikes: totals.ebikes + properties.ebike,
      docks: totals.docks + properties.docks,
    }),
    { stations: 0, bikes: 0, ebikes: 0, docks: 0 },
  );
}

/** Formats a date as HH:MM on the Paris wall clock. */
export function parisTime(date) {
  return PARIS_CLOCK.format(date);
}

/** Tells whether an ISO 8601 time is more than maxMinutes before now. */
export function isStale(isoTime, now, maxMinutes = STALE_AFTER_MINUTES) {
  return (now.getTime() - Date.parse(isoTime)) / 60000 > maxMinutes;
}

/** Escapes the five HTML special characters. */
export function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (char) => HTML_ESCAPES[char]);
}

/** HTML content of the popup of one station, with the name escaped. */
export function popupHtml(properties) {
  const rate = properties.available ? `${Math.round(properties.rate * 100)}% full` : "Not available";
  const lastReport = parisTime(new Date(properties.lastReported * 1000));
  return [
    `<strong>${escapeHtml(properties.name)}</strong>`,
    `<div>${rate}</div>`,
    `<div>${properties.mechanical} mechanical · ${properties.ebike} e-bikes · ${properties.docks} free docks</div>`,
    `<div>Capacity ${properties.capacity} · last report ${lastReport} (Paris time)</div>`,
  ].join("");
}

/** One-line summary of a snapshot for the banner above the map. */
export function bannerText(meta, totals, missing) {
  const count = (value) => NUMBER_FORMAT.format(value);
  const parts = [
    `Snapshot of ${parisTime(new Date(meta.fetched_at))} (Paris time)`,
    `${count(totals.stations)} stations`,
    `${count(totals.bikes)} bikes, including ${count(totals.ebikes)} e-bikes`,
    `${count(totals.docks)} free docks`,
  ];
  if (missing > 0) {
    parts.push(`${count(missing)} stations without position`);
  }
  return parts.join(" · ");
}

/** Base URL of the data files: the ?data= parameter when it is an http(s) URL, otherwise the fallback. */
export function dataBaseUrl(search, fallback = DEFAULT_DATA_URL) {
  const value = new URLSearchParams(search).get("data");
  if (!value) {
    return fallback;
  }
  try {
    const url = new URL(value);
    if (url.protocol !== "http:" && url.protocol !== "https:") {
      return fallback;
    }
    return url.href.endsWith("/") ? url.href : `${url.href}/`;
  } catch {
    return fallback;
  }
}

/** MapLibre expression that colours stations by fill rate and greys out unavailable ones. */
export function circleColorExpression() {
  return [
    "case",
    ["<", ["get", "rate"], 0],
    UNAVAILABLE_COLOR,
    ["interpolate", ["linear"], ["get", "rate"], ...RATE_COLORS.flat()],
  ];
}
```

- [ ] **Step 5: Vérifier que les tests passent**

Run: `node --test "tests/js/*.test.mjs"`
Expected: PASS (12 tests).

- [ ] **Step 6: Brancher les tests JavaScript sur `make` et pre-commit**

Dans `Makefile` : ajouter `test-js` à la ligne `.PHONY`, ajouter la ligne d'aide
`@echo "  test-js           Map JavaScript tests (node --test)"` après celle de `test-integration`, faire lancer aussi
`node --test "tests/js/*.test.mjs"` par la cible `test`, et ajouter la cible :

```make
test-js:
	node --test "tests/js/*.test.mjs"
```

Dans `.pre-commit-config.yaml`, ajouter au dépôt `local`, après le hook `mypy` :

```yaml
      - id: node-tests
        name: node tests (map)
        entry: node --test tests/js/*.test.mjs
        language: system
        files: \.mjs$
        pass_filenames: false
```

- [ ] **Step 7: Committer**

```bash
git add tests/js docs/javascripts/map-core.mjs Makefile .pre-commit-config.yaml
uv run pre-commit run --all-files
git commit -q -m "feat: add pure helpers for the live map" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 2: Site MkDocs et page de la carte

**Files:**
- Create: `mkdocs.yml`, `docs/map.md`, `docs/data.md`, `docs/javascripts/map.mjs`, `docs/stylesheets/map.css`,
  `tests/integration/test_site.py`
- Modify: `docs/index.md` (page d'accueil), `README.md` (lien vers le site)

**Interfaces:**
- Consumes: toutes les fonctions de `map-core.mjs`
- Produces: un site construit dans `site/` par `uv run --only-group doc mkdocs build --strict`

- [ ] **Step 1: Écrire le test qui échoue**

<!-- file: tests/integration/test_site.py -->
```python
"""Builds the documentation site in strict mode."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]


def test_site_builds_in_strict_mode(tmp_path: Path) -> None:
    pytest.importorskip("mkdocs")
    pytest.importorskip("material")
    site = tmp_path / "site"
    result = subprocess.run(
        [sys.executable, "-m", "mkdocs", "build", "--strict", "--site-dir", str(site)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (site / "map" / "index.html").is_file()
    assert (site / "javascripts" / "map-core.mjs").is_file()
    assert (site / "javascripts" / "map.mjs").is_file()
    assert not (site / "superpowers").exists()
```

Run: `uv run pytest tests/integration/test_site.py -q`
Expected: FAIL, car `mkdocs.yml` n'existe pas encore (`Config file 'mkdocs.yml' does not exist`).

- [ ] **Step 2: Écrire la configuration et les pages**

<!-- file: mkdocs.yml -->
```yaml
site_name: Vélib' trends
site_description: Observe the availability of the Vélib' Métropole bike-sharing stations in Paris.
site_url: https://tibzsecondaire.github.io/velib/
repo_url: https://github.com/tibzsecondaire/velib
repo_name: tibzsecondaire/velib
copyright: Code under the MIT license · data under the ODbL 1.0

theme:
  name: material
  language: en
  features:
    - navigation.tabs

nav:
  - Home: index.md
  - Live map: map.md
  - Data: data.md

exclude_docs: |
  superpowers/

extra_css:
  - stylesheets/map.css

markdown_extensions:
  - attr_list
  - md_in_html
  - tables
```

<!-- file: docs/index.md -->
````markdown
# Vélib' trends

Observe how the Vélib' Métropole bike-sharing stations of Paris fill up and empty out.

Every 5 minutes, a GitHub Actions job records the status of every station from the official open
data feeds. The history is published in
[velib-data](https://github.com/tibzsecondaire/velib-data), and the code lives in
[velib](https://github.com/tibzsecondaire/velib).

## What you can find here

- [Live map](map.md): every station, coloured by how full it is in the latest snapshot.
- [Data](data.md): where the data comes from, what the files contain and how to load them.

Analyses of the trends will follow once a few weeks of history have been collected, then
experiments with machine learning, such as predicting the availability of bikes.
````

<!-- file: docs/map.md -->
````markdown
---
title: Live map
hide:
  - navigation
  - toc
---

# Live map

Each dot is a Vélib' station, coloured by its fill rate: the share of bikes among bikes and free
docks. Grey dots are stations out of service. The map shows the latest snapshot published in
[velib-data](https://github.com/tibzsecondaire/velib-data), taken every 5 minutes, and refreshes
on its own. Select a station to see its details.

<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/maplibre-gl@6.12.0/dist/maplibre-gl.css">
<div id="velib-banner" class="velib-banner" role="status" aria-live="polite">Loading the latest snapshot…</div>
<div class="velib-map-wrapper">
  <div id="velib-map" class="velib-map" aria-label="Map of the Vélib' stations coloured by fill rate"></div>
  <div id="velib-legend" class="velib-legend"></div>
</div>
<script type="module" src="../javascripts/map.mjs"></script>
````

<!-- file: docs/data.md -->
````markdown
# Data

## Source and license

The data comes from the official Vélib' Métropole GBFS feeds
(<https://velib-metropole-opendata.smovengo.cloud/opendata/Velib_Metropole/gbfs.json>), published
as open data. The equivalent dataset published by the City of Paris on data.gouv.fr is under the
ODbL. The collected history, published in
[velib-data](https://github.com/tibzsecondaire/velib-data), is available under the
[Open Database License (ODbL) 1.0](https://github.com/tibzsecondaire/velib-data/blob/main/LICENSE).

## Files

| Path | Content | Updated |
|---|---|---|
| `raw/station_status.csv` | latest snapshot, one row per station | every 5 minutes |
| `raw/snapshot_meta.json` | time of the latest snapshot and number of stations | every 5 minutes |
| `daily/YYYY-MM-DD.parquet` | one UTC day, one row per station and per snapshot | every night |
| `daily/index.csv` | one row per day: snapshots, first and last fetch, largest gap | every night |
| `stations/station_information.csv` | code, name, latitude, longitude and capacity of each station | every night |

All times are UTC. The status file has the columns `station_id`, `mechanical` and `ebike`
(available bikes of each type), `docks` (free docks), `is_installed`, `is_renting` and
`is_returning` (0 or 1), and `last_reported` (Unix time of the last report of the station).

## Caveats

- GitHub can delay or skip scheduled jobs, so snapshots are not exactly 5 minutes apart. The
  daily index gives the largest gap of each day.
- Some stations have not reported for a long time. Check `last_reported`.

## Loading a day with polars

```python
import polars as pl

url = "https://raw.githubusercontent.com/tibzsecondaire/velib-data/main/daily/2026-10-06.parquet"
day = pl.read_parquet(url)
```
````

<!-- file: docs/stylesheets/map.css -->
```css
.velib-banner {
  margin: 0.5rem 0;
  padding: 0.5rem 0.75rem;
  border-radius: 0.2rem;
  background: var(--md-code-bg-color);
  font-size: 0.75rem;
}

.velib-banner--warning {
  background: #fff3cd;
  color: #664d03;
}

.velib-map-wrapper {
  position: relative;
}

.velib-map {
  height: calc(100vh - 18rem);
  min-height: 420px;
  border-radius: 0.2rem;
}

.velib-legend {
  position: absolute;
  left: 0.75rem;
  bottom: 1.75rem;
  padding: 0.5rem 0.75rem;
  border-radius: 0.2rem;
  background: rgb(255 255 255 / 90%);
  box-shadow: 0 1px 4px rgb(0 0 0 / 20%);
  color: #222;
  font-size: 0.65rem;
  line-height: 1.4;
}

.velib-legend__bar {
  width: 9rem;
  height: 0.5rem;
  margin: 0.25rem 0;
  border-radius: 0.2rem;
}

.velib-legend__labels {
  display: flex;
  justify-content: space-between;
}

.velib-legend__dot {
  display: inline-block;
  width: 0.6rem;
  height: 0.6rem;
  border-radius: 50%;
  vertical-align: middle;
}

.velib-map .maplibregl-popup-content {
  color: #222;
  font-size: 0.7rem;
}
```

<!-- file: docs/javascripts/map.mjs -->
```javascript
// Live map of the Vélib' stations, coloured by fill rate. The pure helpers live in map-core.mjs.

import {
  Map as MapLibreMap,
  NavigationControl,
  Popup,
} from "https://cdn.jsdelivr.net/npm/maplibre-gl@6.12.0/dist/maplibre-gl.mjs";

import {
  RATE_COLORS,
  STALE_AFTER_MINUTES,
  UNAVAILABLE_COLOR,
  bannerText,
  buildStations,
  circleColorExpression,
  dataBaseUrl,
  isStale,
  popupHtml,
  summarize,
} from "./map-core.mjs";

const REFRESH_MS = 5 * 60 * 1000;
const STYLE_URL = "https://tiles.openfreemap.org/styles/positron";
const PARIS = [2.3488, 48.8534];

const banner = document.getElementById("velib-banner");
const legend = document.getElementById("velib-legend");
const dataUrl = dataBaseUrl(window.location.search);
let lastLoad = 0;

function setBanner(text, isWarning = false) {
  banner.textContent = text;
  banner.classList.toggle("velib-banner--warning", isWarning);
}

async function fetchText(path) {
  const response = await fetch(new URL(path, dataUrl));
  if (!response.ok) {
    throw new Error(`${path}: HTTP ${response.status}`);
  }
  return response.text();
}

function renderLegend() {
  const stops = RATE_COLORS.map(([value, color]) => `${color} ${value * 100}%`).join(", ");
  legend.innerHTML = [
    "<div><strong>Fill rate</strong></div>",
    `<div class="velib-legend__bar" style="background: linear-gradient(to right, ${stops})"></div>`,
    '<div class="velib-legend__labels"><span>empty</span><span>half</span><span>full</span></div>',
    `<div><span class="velib-legend__dot" style="background: ${UNAVAILABLE_COLOR}"></span> out of service</div>`,
  ].join("");
}

async function loadData(map) {
  lastLoad = Date.now();
  try {
    const [statusText, metaText, informationText] = await Promise.all([
      fetchText("raw/station_status.csv"),
      fetchText("raw/snapshot_meta.json"),
      fetchText("stations/station_information.csv").catch((error) => {
        if (error.message.endsWith("HTTP 404")) {
          return null;
        }
        throw error;
      }),
    ]);
    if (informationText === null) {
      setBanner("Station positions are not published yet: they appear after the first nightly compaction.", true);
      return;
    }
    const meta = JSON.parse(metaText);
    const { features, missing } = buildStations(statusText, informationText);
    map.getSource("stations").setData({ type: "FeatureCollection", features });
    const text = bannerText(meta, summarize(features), missing);
    if (isStale(meta.fetched_at, new Date())) {
      setBanner(`${text} · the data may be delayed: older than ${STALE_AFTER_MINUTES} minutes`, true);
    } else {
      setBanner(text);
    }
  } catch (error) {
    setBanner(`Could not load the latest data (${error.message}). Retrying in 5 minutes.`, true);
  }
}

function addStations(map) {
  map.addSource("stations", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  map.addLayer({
    id: "stations",
    type: "circle",
    source: "stations",
    paint: {
      "circle-color": circleColorExpression(),
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 2.5, 13, 5, 16, 10],
      "circle-stroke-color": "#ffffff",
      "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 10, 0.3, 16, 1.5],
      "circle-opacity": 0.9,
    },
  });
  map.on("click", "stations", (event) => {
    const feature = event.features[0];
    new Popup({ maxWidth: "280px" })
      .setLngLat(feature.geometry.coordinates)
      .setHTML(popupHtml(feature.properties))
      .addTo(map);
  });
  map.on("mouseenter", "stations", () => {
    map.getCanvas().style.cursor = "pointer";
  });
  map.on("mouseleave", "stations", () => {
    map.getCanvas().style.cursor = "";
  });
}

renderLegend();
const map = new MapLibreMap({
  container: "velib-map",
  style: STYLE_URL,
  center: PARIS,
  zoom: 11.5,
  attributionControl: { compact: true },
});
map.addControl(new NavigationControl({ showCompass: false }), "top-right");
map.on("load", () => {
  addStations(map);
  loadData(map);
  setInterval(() => loadData(map), REFRESH_MS);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && Date.now() - lastLoad > REFRESH_MS) {
      loadData(map);
    }
  });
});
```

Dans `README.md`, juste après la première ligne de description, ajouter :

```markdown
Website, with a live map of the stations: <https://tibzsecondaire.github.io/velib/>.
```

- [ ] **Step 3: Vérifier que le site se construit**

Run: `uv run pytest tests/integration/test_site.py -q` puis `node --test "tests/js/*.test.mjs"`
Expected: PASS ; le site contient `map/index.html` et les 2 modules, sans `superpowers/`.

- [ ] **Step 4: Regarder la carte en local avec un vrai relevé**

Préparer dans un dossier temporaire un site construit et, à côté, un dossier `testdata/` avec un vrai relevé et les
positions des stations, servis par la même origine :

```bash
SITE="${TMPDIR:-/tmp}/velib-site"
uv run --only-group doc mkdocs build --strict --site-dir "$SITE"
uv run velib-collect snapshot --output-dir "$SITE/testdata/raw"
uv run python -c "from pathlib import Path; from velib import gbfs; from velib.collect.compact import write_station_information; c = gbfs.make_client(); write_station_information(gbfs.fetch_station_information(c), Path('$SITE/testdata/stations/station_information.csv'))"
uv run python -m http.server 8765 --directory "$SITE"
```

Ouvrir `http://localhost:8765/map/?data=http://localhost:8765/testdata/`.
Expected: environ 1 519 points colorés sur le fond OpenFreeMap, le bandeau avec l'heure du relevé et les totaux, une
bulle au clic, la légende en bas à gauche.

- [ ] **Step 5: Committer**

```bash
git add mkdocs.yml docs README.md tests/integration/test_site.py
uv run pre-commit run --all-files
git commit -q -m "feat: add the website with a live map of the stations" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

### Task 3: Publication sur GitHub Pages

**Files:**
- Create: `.github/workflows/pages.yml`

**Interfaces:**
- Consumes: `mkdocs.yml` et `docs/`
- Produces: le site publié sur `https://tibzsecondaire.github.io/velib/`

- [ ] **Step 1: Écrire la tâche de publication**

<!-- file: .github/workflows/pages.yml -->
```yaml
name: pages

on:
  push:
    branches: [main]
    paths:
      - "docs/**"
      - "mkdocs.yml"
      - ".github/workflows/pages.yml"
  workflow_dispatch:

permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: pages
  cancel-in-progress: true

jobs:
  build:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - name: Check out velib
        uses: actions/checkout@v7

      - name: Install uv
        uses: astral-sh/setup-uv@v10.2.0
        with:
          enable-cache: true

      - name: Build the site
        run: uv run --only-group doc mkdocs build --strict --site-dir site

      - name: Upload the site
        uses: actions/upload-pages-artifact@v5
        with:
          path: site

  deploy:
    needs: build
    runs-on: ubuntu-latest
    timeout-minutes: 10
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - name: Deploy to GitHub Pages
        id: deployment
        uses: actions/deploy-pages@v5
```

- [ ] **Step 2: Activer GitHub Pages avec la source « GitHub Actions »**

```bash
export GH_CONFIG_DIR="$HOME/.config/gh-tibzsecondaire"
test "$(gh api user --jq .login)" = "tibzsecondaire"
gh api -X POST repos/tibzsecondaire/velib/pages -f build_type=workflow
gh api repos/tibzsecondaire/velib/pages --jq '"\(.build_type) \(.html_url)"'
```

Expected: `workflow https://tibzsecondaire.github.io/velib/`.

- [ ] **Step 3: Fusionner, vérifier et pousser**

```bash
git add .github/workflows/pages.yml
uv run pre-commit run --all-files
git commit -q -m "ci: publish the website on github pages" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
git switch main
git merge --ff-only feat/site-map
make lint
make test
git log --format='%an <%ae> | %cn <%ce>' origin/main..main | sort -u
git log -p origin/main..main | grep -ciEf .git/info/private-identifiers
git push origin main
```

Expected: hooks et tests au vert, une seule identité `tibzsecondaire`, compteur à `0`.

- [ ] **Step 4: Vérifier la publication**

Quand GitHub Actions fonctionne :

```bash
gh run list --repo tibzsecondaire/velib --workflow pages.yml --limit 1
curl -s -o /dev/null -w "%{http_code}\n" https://tibzsecondaire.github.io/velib/map/
```

Expected: run réussi, réponse `200`. La carte affiche les stations dès que `velib-data` contient
`raw/station_status.csv` et `stations/station_information.csv`.
