# Dashboard setup · "Approval Health" (Databricks AI/BI)

The dashboard answers "is anything wrong?" at a glance and gives the analyst the context to ask the copilot
"why?". Datasets are the queries in `databricks/notebooks/03_dashboard_queries.sql`: each one becomes a
dataset in the dashboard (**Dashboards → Create dashboard → Data → Create from SQL**, paste the query,
rename the dataset as below). The queries use fully qualified names (`workspace.approval_copilot.*`), so
they run as they are in the dataset editor.

Use a SQL warehouse in UTC (the default).

To create it through the API instead, use `databricks/dashboard/approval_health.lvdash.json`, generated from
these queries and this page by `tools/build_deploy_payloads.py` (runbook, "Alternative: deploy through the
APIs"). Its counters write the filtered aggregations below with `CASE WHEN`, which gives the same result.

## Datasets

| Dataset | Used by |
|---|---|
| `ds_kpis` | KPI counters, last 7 days vs previous 7 (counts per period, country and card type) |
| `ds_daily_by_segment` | Daily trend by country × card type |
| `ds_hourly_by_acquirer` | Hourly drill-down |
| `ds_decline_reasons` | Decline reasons by acquirer |
| `ds_cascade_daily` | Retry (cascade) health |
| `ds_funnel` | Checkout funnel |
| `ds_cost_vs_approval` | Fee vs approval by acquirer |
| `ds_anomalies` | Flagged days |
| `ds_cascade_alerts` | Days when retries collapsed (cascade alert) |
| `ds_change_log` | Production changes |
| `ds_data_quality` | Rows rejected by the pipeline |

## Page 1 · Overview

The counters are custom calculations on `ds_kpis` (dataset → **Custom calculation**). Each one is a
filtered aggregation, so it can be split by period and still follow the country and card-type filters. For
the final approval of the last 7 days:

```sql
SUM(final_approved) FILTER (WHERE period = 'last_7_days')
  / SUM(payments_sent) FILTER (WHERE period = 'last_7_days')
```

Make the same calculation with `period = 'previous_7_days'` for the comparison. The other counters follow
the same pattern with the formulas below.

| Widget | Type | Dataset | Settings |
|---|---|---|---|
| Final approval | Counter | `ds_kpis` | Value: `SUM(final_approved) / SUM(payments_sent)` for the last 7 days (above); Comparison: the same for the previous 7 days; format percent, 1 decimal |
| First-attempt approval | Counter | `ds_kpis` | `SUM(first_attempt_approved) / SUM(payments_sent)`, last 7 vs previous 7 days |
| Approved volume rate | Counter | `ds_kpis` | `SUM(approved_usd) / SUM(attempted_usd)`, last 7 vs previous 7 days |
| Retry recovery rate | Counter | `ds_kpis` | `SUM(recovered_by_cascade) / SUM(first_attempt_failures)`: share of failed first attempts that a retry on a second acquirer turned into an approval |
| Effective fee | Counter | `ds_kpis` | `10000 * SUM(fees_usd) / SUM(approved_usd)` (bps) |
| Final approval by segment | Line | `ds_daily_by_segment` | x `business_date`, y `final_approval_rate`, color `segment`; y-axis from 70% |
| Abnormal days | Table | `ds_anomalies` | columns: business_date, country, card_type, final_approval_rate, baseline_rate, delta_pp, z_binomial |
| Production changes | Table | `ds_change_log` | all columns, newest first |
| Retry alerts | Table | `ds_cascade_alerts` | business_date, country, acquirer_id, first_reason_code, cascade_rate, baseline_cascade_rate, mapping_versions; newest first |

If the Comparison field of your counter only takes a date offset, show the previous 7 days as a second,
smaller counter next to each one.

Filters (global): `country`, `card_type`, date range on `business_date`. Each filter binds to the field of
the same name in every dataset that has it. `ds_kpis` has its own fixed 14-day window, so it takes the
country and card-type filters but not the date range; `ds_cost_vs_approval` covers the whole period;
`ds_change_log` and `ds_data_quality` are not filtered.

## Page 2 · Diagnose

| Widget | Type | Dataset | Settings |
|---|---|---|---|
| Hourly approval by acquirer | Line | `ds_hourly_by_acquirer` | x `hour_local_ts`, y `final_approval_rate`, color `acquirer_id`; tooltip `payments_sent` |
| Failure reasons by acquirer | Stacked bar | `ds_decline_reasons` | x `acquirer_id`, y sum `failed_attempts`, color `reason`; filter by date |
| Retryable vs not | Bar | `ds_decline_reasons` | x `decline_type`, y sum `failed_attempts`, color `retryable` |
| Cascade rate | Line | `ds_cascade_daily` | x `business_date` grouped by week, y custom calculation `SUM(cascaded) / SUM(failures)`, color `acquirer_id` |
| Retry recovery rate | Line | `ds_cascade_daily` | x `business_date` grouped by week, y custom calculation `SUM(recovered) / SUM(failures)`, color `acquirer_id`; placed under the cascade-rate chart |

Tip: set the date filter to 12–15 Aug and the country to CO to see the incident hour by hour.

## Page 3 · Funnel, cost and data quality

| Widget | Type | Dataset | Settings |
|---|---|---|---|
| Conversion | Line | `ds_funnel` | x `business_date`, y `conversion_rate`, color `country` |
| Risk blocks | Line | `ds_funnel` | x `business_date`, y `risk_block_rate`, color `country`; placed under the conversion chart with the same x axis (two charts, not one chart with two y axes) |
| Fee vs approval | Scatter | `ds_cost_vs_approval` | x `effective_fee_bps`, y `final_approval_rate`, size `payments_sent`, color `acquirer_id`, label `country`/`card_type` |
| Rejected rows | Table | `ds_data_quality` | all columns |

Note on the scatter: rows are grouped by the first acquirer (the routing decision); fees follow the acquirer
that finally approved. A segment routed first to a cheap acquirer whose declines are recovered elsewhere
therefore shows the true cost of that routing choice.

## Optional: subscription and alert

- Schedule the dashboard daily at 08:00 and subscribe the payments operations group.
- In Databricks SQL, create alerts on `gold_anomalies_daily` (`is_anomaly = true` for yesterday) and on
  `gold_cascade_alerts_daily` (`is_alert = true` for yesterday). The second one is the early warning that
  would have caught the 6 Aug mapping bug.
