# Impact and detection analysis

The copilot answers diagnostic questions fast. This page checks two of its answers with the methods I
would use before a finance or partner review, and adds an alert for large losses the current detectors
cannot see. Every number comes from the scripts in [`analysis/`](../analysis), run on the local pipeline
output of the reference dataset (seed 42) and, for the backtest, on three datasets generated with other seeds.

## 1. Cost of routing v14 against a control group

The copilot compares the incident window with the same segment's previous 14 days (`fn_drop_impact`).
Colombian domestic debit approved 10.9 points less, which cost 110 approvals and USD 6,354 of approved
volume. That comparison holds only if nothing else changed during the window. To test it, I measured
the same change against a control group: Colombian domestic credit, which routing v14 did not touch
but which shares the market, the hours and the days.

| Final approval | 7 days before | Window (22 hours) | Change |
|---|---|---|---|
| Colombian debit (moved to Acquirer B) | 86.6% | 75.6% | −11.0 pp |
| Colombian credit (control) | 86.2% | 87.4% | +1.2 pp |
| Difference-in-differences | | | **−12.2 pp** |

- 95% confidence interval: −15.2 to −9.4 points. Payments in the same hour share shocks, so the
  bootstrap resamples whole clock hours (5,000 resamples), separately before and during the window.
- Lost approvals: 122 (95 to 152). Lost approved volume: USD 6,251 (3,270 to 9,349). The volume estimate
  moves the other way from the count because the control's approval by value fell 0.5 points in the window
  while its approval by count rose 1.2; both moves are within noise.
- The copilot's before/after figure gets an interval from the same bootstrap: −10.9 points, from −14.2
  to −8.1. The two intervals overlap for most of their width.

Design choices and checks:

- The window runs from the change (13 August, 14:00 local time) to the rollback (14 August, 12:10).
- The 7-day pre-period starts after decline-code map v2.3 went live on 6 August. That change also hit
  the control group, because 30% of Colombian credit goes to Acquirer B. With 3 or 5 days of pre-period the
  estimate is −12.9 and −12.8 points; with 10 or 14 days, which reach back before the map change, −12.0 and
  −11.8.
- Placebo test: the same estimate on the 55 dates whose window and pre-period touch no change to Colombian
  traffic. They range from −3.8 to +3.6 points, with a standard deviation of 1.6, so the incident sits about
  7.6 placebo standard deviations from zero. Seven of the 55 placebo intervals exclude zero, against about
  three expected by chance, so the bootstrap interval runs a little narrow; an interval built from the placebo
  spread, about ±3.2 points, still leaves the estimate far from zero.
- Before the change, the daily gap between debit and credit moved between −2.2 and +3.8 points with no
  trend. The control rose 1.2 points during the window, about 1.2 standard errors at its volume, so it is most
  likely noise.

Both methods put the loss at 11 to 12 points. The copilot's number is good enough to act on during an
incident. For a partner or finance review I would bring the difference-in-differences estimate, because it
nets out shocks that hit Colombian debit and credit alike.

## 2. Mix and rate effects

`fn_approval_drivers` splits a change in approval into a mix effect (traffic moved toward segments
that approve less) and a rate effect (segments approved less than before). For the incident window
against the previous 14 days:

| Dimension | Rate effect | Mix effect |
|---|---|---|
| Issuer | −10.92 pp | −0.02 pp |
| Merchant segment | −11.00 pp | +0.05 pp |
| Card brand | −10.94 pp | 0.00 pp |
| 3DS authenticated or not | −10.90 pp | −0.04 pp |

Each row adds up to the total change of −10.95 points, within rounding. Mix effects stay under 0.1 points
in every dimension, and every issuer, brand, merchant segment and 3DS group approved less in the window: the
same customers were paying, and the way their payments were processed changed, which is what the routing
change altered. The acquirer dimension is left out on purpose: Acquirer B carried no Colombian debit in the
baseline, so the function books all of its change as rate.

## 3. An alert on approved payments

The approval-rate detector (`gold_anomalies_daily`) and the cascade alert both watch ratios of payments
that reached an acquirer. A loss before that point, such as risk blocks, 3DS abandonment or traffic that
never arrives, does not move those ratios. A count of approved payments sees every kind of loss, but counts
move with the calendar: in this data Sundays run about 30% below Fridays, and paydays 12 to 18% above a
normal day.

[`monitoring/volume_forecast.py`](../monitoring/volume_forecast.py) forecasts each day's approved payments
per country and card type from the previous 56 days, with a trend, weekday terms and a payday term (the
quincenas in Mexico and Colombia, the 5th to the 7th in Brazil). It flags a day that falls more than 3.09
prediction standard errors below the forecast and at least 8% under it. Days that alerted are left out of
later fits, so an outage does not lower the baseline, and a segment-day with no traffic is scored as zero.
The Databricks job runs it after the pipeline and writes `gold_volume_alerts_daily`.

### Backtest

I compared it with two simpler baselines: a trailing 28-day mean (a z-score) and the mean of the previous
four same weekdays (a percent drop). On the reference data (seed 42) I set each baseline's threshold so that
it raises as many false alerts as the forecast, two, which makes the comparison one of detection at the same
false-alert rate. Then I ran all three, unchanged, on three datasets generated with seeds 7, 11 and 23.
Those datasets come from the same generator, whose daily volume follows the same weekday, payday and trend
terms the forecast fits, so they test new noise; holidays, promotions and real outages are not in them.

None of the planted incidents moves daily counts enough to test detection, so every clean day is cut by 10%
to 30% in turn and scored again. Planted events, including the days of the decline-code map bug, are left
out of both the false-alert count and the cuts.

| Out of sample: 762 segment-days | Forecast | Same weekday | Trailing mean |
|---|---|---|---|
| False alerts | 2 | 0 | 4 |
| Days caught after a 10% cut | 15.7% | 4.9% | 6.7% |
| Days caught after a 15% cut | 49.1% | 21.5% | 13.9% |
| Days caught after a 20% cut | 81.5% | 53.9% | 23.2% |
| Days caught after a 30% cut | 100% | 94.1% | 66.4% |

- At the same false-alert rate on the reference data, the forecast caught four out of five days cut by 20%,
  against about half for the same-weekday mean and under a quarter for the trailing mean. The same-weekday
  mean handles weekends but not paydays, and averages only four points; the trailing mean has to stretch its
  band over both.
- On the reference data itself the forecast caught 54.7% of 15% cuts and 89.8% of 20% cuts.
- The lower bound sits about 15% under the forecast. Most of that width is day-to-day noise of about 4.5%
  at these volumes; fitting on 56 days adds about a tenth, so a year of history would move the bound only
  to about 13%.
- In the reference data the alert fires twice, on 22 August (Mexican debit) and 26 August (Brazilian
  credit), and both are noise. On 13 August, Colombian debit was 8.4% below its forecast (z = −1.7),
  inside the band. The approval-rate detector flagged that day, with final approval 7.3 points below its
  baseline (z = −7.3), so it remains the alert for authorization problems; the forecast adds cover for large
  losses before the acquirer.

## 4. Reproduce

```bash
python local/run_local.py                 # pipeline output for seed 42
python analysis/incident_impact.py        # section 1 (pandas only)
python analysis/mix_rate_split.py         # section 2 (runs fn_approval_drivers on local Spark)
python analysis/detector_backtest.py --seeds-dir DIR   # section 3; how to build DIR is in the script
```

Results: [`incident_impact.json`](../analysis/results/incident_impact.json),
[`mix_rate_split.json`](../analysis/results/mix_rate_split.json),
[`detector_backtest.json`](../analysis/results/detector_backtest.json).
`tests/test_analysis.py` checks that the before/after arithmetic reproduces the `fn_drop_impact` figures
from the evaluation (−10.95 points, 110 approvals, USD 6,354) and that every placebo estimate stays within 5
points of zero.
