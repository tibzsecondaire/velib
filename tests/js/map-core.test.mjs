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
