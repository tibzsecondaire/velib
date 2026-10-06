# Forecasting

Can we forecast how many bikes a station will have in 15 minutes to 3 hours? A first model,
trained on the two weeks of the public
[velib_data archive](https://www.kaggle.com/datasets/adrienmorel97/velib-data) by adrienmorel97
(2 to 16 December 2025, every 5 minutes, CC BY-SA 4.0), gives this answer: barely at short
horizons, clearly beyond one hour.

## Method

- target: bikes available at a station 15, 30, 60, 120 or 180 minutes later
- training period: 2 to 12 December 2025
- test period: 13 to 16 December 2025, a weekend and 2 weekdays that the model never saw
- model: gradient-boosted trees from scikit-learn, one per horizon, which forecast the change in
  bikes from the current state, the last hour, the time of day, the usual pattern of the station
  and the weather
- references: persistence (no change), the usual level of the station at that hour, and
  persistence corrected by the usual change at that hour

## Results

Mean absolute error over the 4 test days, in bikes. Lower is better.

| Horizon | Persistence | Usual level | Corrected persistence | Model | Gain over persistence |
|---|---|---|---|---|---|
| 15 minutes | 0.83 | 5.04 | 0.98 | 0.83 | 0% |
| 30 minutes | 1.23 | 5.04 | 1.40 | 1.21 | 2% |
| 1 hour | 1.75 | 5.04 | 1.85 | 1.67 | 5% |
| 2 hours | 2.63 | 5.03 | 2.56 | 2.29 | 13% |
| 3 hours | 3.36 | 5.02 | 3.05 | 2.70 | 20% |

What this shows:

- Up to 30 minutes, "no change" is almost impossible to beat. Half of the stations do not change
  in 15 minutes, and nothing in the data tells which way the others will move.
- Beyond one hour, the model is the best method. On weekdays its error at 3 hours is about a third
  lower than persistence: 2.55 against 3.75 bikes on Monday 15 December.
- At weekends the model barely beats persistence. The training period contained only one weekend.
- The usual pattern of the station matters most. Over two weeks of mild weather, the weather adds
  almost nothing.
- Beyond one hour, the model is worse than persistence at warning that a station is about to be
  empty. That needs a dedicated model.

## Reproduce the results

```bash
uv run velib-archives import kaggle
uv run --group ml velib-forecast evaluate --source kaggle
```

The full report, with the root mean square error, the detection of empty stations, the error of
each test day and the most useful features, is written to `data/forecast/kaggle/report.md`.
