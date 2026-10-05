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
