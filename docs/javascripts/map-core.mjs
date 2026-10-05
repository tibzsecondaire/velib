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
