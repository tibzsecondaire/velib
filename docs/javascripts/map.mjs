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
