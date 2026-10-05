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
