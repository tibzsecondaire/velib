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

## Archives

Two public archives cover earlier periods. `velib-archives import kaggle` and
`velib-archives import lovasoa` download them and convert them, on your computer, to the same
layout as velib-data, in `data/archives/`:

| Archive | Period | Snapshots | License |
|---|---|---|---|
| [velib_data](https://www.kaggle.com/datasets/adrienmorel97/velib-data), by adrienmorel97 on Kaggle | 2 to 16 December 2025 | every 5 minutes, with the weather | CC BY-SA 4.0 |
| [historique-velib-opendata](https://github.com/lovasoa/historique-velib-opendata), by lovasoa | 26 November 2020 to 9 April 2021 | every 15 minutes | GPL-3.0 repository, data from the Vélib' open data |

The archives give no free docks, so their fill rate is computed with the capacity instead. The
stations of the 2020–2021 archive are matched with today's stations by position, then by name.
That archive also has irregular snapshots, about 60 a day with gaps of an hour, so analyse it with
30-minute or hourly slots, for example with `scripts/heatmap_day.py --slot 30m`.
The converted archives are not republished: their licenses ask for attribution, and CC BY-SA
also asks derived data to keep the same license.

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
