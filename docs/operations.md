# Operations

The snapshots also show what the operator does. Trucks move bikes between stations, mostly at
night, and a few hundred bikes stay stuck in their stations for days. These findings come from the
two weeks of the public
[velib_data archive](https://www.kaggle.com/datasets/adrienmorel97/velib-data) by adrienmorel97
(2 to 16 December 2025, every 5 minutes, CC BY-SA 4.0).

## Rebalancing trucks

A station that gains or loses at least 8 bikes in 5 minutes has almost certainly been served by a
truck. Riders move one bike at a time, and 99.9% of the 5-minute changes are 5 bikes or fewer.
Consecutive changes in the same direction count as one operation.

What the two weeks show:

- 1,768 operations in 15 days, about 120 a day
- about 880 bikes added and 340 removed a day
- 70% of the operations happen between 9pm and 5am, when riders make only 19% of the moves
- stations that receive bikes are 23% full just before, and stations that lose bikes are 72% full

The operator empties full stations in the centre and the inner west, and refills the outskirts,
the north and the east. It adds about 2.6 times more bikes in large batches than it removes: it
probably removes bikes in smaller batches, which this threshold misses. With a threshold of 10 or
12 bikes, there are 827 or 376 operations.

| Stations that receive the most bikes | Bikes added |
|---|---|
| Place d'Italie - Vincent Auriol | 148 |
| Porte d'Ivry | 126 |
| Marcel Yol - Jullien | 119 |
| Gare du Nord - Hôpital Lariboisière | 119 |
| Place d'Italie - Soeur Rosalie | 118 |

| Stations that lose the most bikes | Bikes removed |
|---|---|
| Gare Montparnasse - Vaugirard | 70 |
| Pau Casals - Neuve Tolbiac | 58 |
| Diane Arbus | 56 |
| Sainte-Elisabeth - Turbigo | 52 |
| Jean Macé - Faidherbe | 52 |

## Stuck bikes

A bike that never leaves a station, while other bikes of its type do, is probably broken or, for
an electric bike, discharged. A station is suspect on a day when at least 10 bikes of a type leave
it, but it keeps falling back to the same 1 or 2 bikes of that type for at least an hour in total.
Three such days in a row make an immobilisation.

| Type | Immobilisations | Stations | Stuck bikes per day |
|---|---|---|---|
| Electric | 770 | 641 | about 210 |
| Mechanical | 128 | 113 | about 40 |

Some electric bikes stayed for the whole 15 days, for example at Saint-Didier - Raymond Poincaré,
Sabot - Rennes and Square Louis XVI.

The rule ignores floors above 2 bikes. Large stations that always keep many mechanical bikes have a
surplus, not stuck bikes: their count only touches its daily minimum, while a stuck bike holds the
count at the same level for hours.

## Out-of-service docks

The live feed counts neither broken docks nor disabled bikes. Capacity minus bikes minus free docks
estimates them, so they can only be measured on
[velib-data](https://github.com/tibzsecondaire/velib-data), not on the archives. In the first hours
of collection, on the night of 5 to 6 October 2026:

- about 1,640 docks out of 49,913 were out of service at a time, 3.3%
- 57% of the stations had at least one
- a few stations were entirely out of service, such as Marignan - Champs-Élysées, with 40 docks
  out of 40

These figures will be updated as the collection grows.

## Reproduce the results

```bash
uv run velib-archives import kaggle
uv run --group db velib-ops report --source kaggle
uv run --group db velib-ops report --source velib-data
```

Each report, with the operations per day and hour, and a map of the stations, is written to
`data/ops/<source>/`. The same data can be queried in SQL with DuckDB, for example:

```bash
uv run --group db velib-db sql --source kaggle "SELECT count(*) AS snapshots FROM snapshots"
```
