# Metrics

The definitions below are also written into the Genie instructions, the data dictionary and the Unity Catalog
column comments, so the dashboard, the copilot and a human analyst get the same number for the same question.

## Metric tree

```
Approved volume rate (north star) = approved USD / attempted USD
│
├── Final approval rate = final approved / payments sent
│   ├── First-attempt approval rate = approved on the first acquirer / payments sent
│   │   ├── by acquirer (routing decision)       ← routing changes move this
│   │   ├── by issuer                            ← issuer outages move this
│   │   └── by decline reason (soft / hard / technical)
│   └── Cascade recovery (pp) = recovered on a second acquirer / payments sent
│       ├── cascade rate = retried / failed first attempts      ← mapping bugs move this
│       └── retry recovery rate = recovered / failed first attempts
│
├── Ticket mix (approved USD per approval)
│
└── Upstream of authorization (not in the approval rate, but in conversion)
    ├── Risk block rate = blocked by risk / intents             ← risk-rule changes move this
    └── 3DS abandonment = abandoned in the challenge / intents

Guardrails: effective fee (bps of approved USD) · p95 authorization latency · data-quality rejects
```

## Definitions

| Metric | Formula | Grain / filter | Where |
|---|---|---|---|
| Payments sent | payments with at least one valid acquirer attempt | per payment | `silver_payments.sent_to_acquirer`, `gold_*.payments_sent` |
| First-attempt approval rate | `first_attempt_approved / payments_sent` | domestic unless stated | `gold_approval_daily` |
| Final approval rate | `final_approved / payments_sent` | domestic unless stated | `gold_approval_daily`, `gold_approval_hourly` |
| Cascade recovery | `recovered_by_cascade / payments_sent` (pp) | | `gold_approval_daily` |
| Cascade rate | `cascaded / first_attempt_failures` | per acquirer × reason | `gold_cascade_daily`, `gold_cascade_alerts_daily` |
| Retry recovery rate | `recovered / first_attempt_failures` (equals recovered / retried when every failure is retried) | per acquirer × reason | `gold_cascade_daily`, `fn_cascade_health` |
| Approved volume rate | `approved_usd / attempted_usd` | | `gold_approval_daily` |
| Conversion rate | `approved / payment_intents` | includes risk blocks and 3DS abandonment | `gold_funnel_daily` |
| Risk block rate | `blocked_by_risk / payment_intents` | blocks plus review timeouts | `gold_funnel_daily` |
| Effective fee | `fees_usd / approved_usd × 10,000` (bps) | | `gold_approval_daily` |
| p95 latency | 95th percentile of first-attempt latency | per acquirer | `gold_approval_daily.p95_latency_ms`, `silver_payments.first_latency_ms` |
| Lost approvals | `window_payments × baseline_final_rate − window_approvals` | vs a baseline period | `fn_drop_impact` |
| Lost approved volume | `window_attempted_usd × baseline_volume_rate − window_approved_usd` | | `fn_drop_impact` |

## Definition choices (and why)

- Approval is counted per payment: a payment retried on a second acquirer is one payment. Attempt-level rates
  double-count failures and make cascading look worse than it is.
- "Approval rate" without a qualifier means final approval, which is what the merchant experiences. First-attempt
  approval is the routing KPI. In the industry the same ratio is often called the authorization rate.
- Outcomes are attributed to the first acquirer chosen by routing, including approvals recovered on a
  second acquirer, because routing is the decision the team controls. `approving_acquirer_id` is kept for
  partner scorecards.
- Reversals count as authorized, since the issuer approved the authorization; a later void is a separate event.
- Domestic traffic is the default. Cross-border cards always go to Acquirer A and approve less for reasons
  unrelated to routing; mixing them in makes acquirer comparisons unfair.
- Business dates are local: each country's date and hour are computed in its own time zone (MX UTC-6,
  CO UTC-5, BR UTC-3; none observes daylight saving time in 2026), because operations teams think in local days.
- Baselines are explicit. Impact is always "versus the previous N days" (14 days by default for
  diagnosis; 28 days for the anomaly check; the healthy days among the previous 56 days with failures for
  the cascade alert), and the copilot states which baseline it used.

## Pitfalls the copilot is instructed to avoid

1. Averaging daily rates. Recompute from counts: `sum(final_approved) / sum(payments_sent)`.
2. Summing or averaging p95 latencies across rows. Recompute the percentile from `silver_payments`.
3. Comparing acquirers with different card-type or country mix. Compare within the same segment.
4. Reading a drop without its volume. A 5 pp drop on 40 payments is noise; the anomaly check uses a
   binomial z-score for that reason.
5. Explaining a conversion drop with authorization data only. Risk blocks and 3DS abandonment happen
   before the acquirer (the 18–19 June case in Brazil).
6. Blaming the acquirer for an issuer-side failure. If one issuer fails on every acquirer, retrying
   elsewhere will not help (the 21 July case in Brazil).
7. Treating lost approved volume as lost revenue. It is an upper bound on lost sales: a declined shopper
   often tries again with another card or method, and the orchestrator loses only its margin on a lost
   sale. In production, approval should be tracked per order (checkout) as well as per payment. This
   dataset has one payment per order, so it cannot show that effect.
8. Mixing cross-border and domestic retries. `gold_cascade_daily` and `gold_cascade_alerts_daily` include
   cross-border failures, while `fn_cascade_health` covers domestic traffic only, so their cascade rates for
   the same acquirer differ by 2–4 pp. Compare like with like.

## Detection rules

| Detector | Rule | Backtest on the same 90 days used to set the thresholds (in-sample) |
|---|---|---|
| Daily approval anomaly (`gold_anomalies_daily`) | domestic final approval ≥ 3 pp below the trailing 28-day mean **and** z vs history ≤ −3 **and** binomial z ≤ −4 | 1 flag in 540 country × card-type days: CO debit, 13 Aug |
| Cascade alert (`gold_cascade_alerts_daily`) | baseline cascade rate ≥ 50% on ≥ 50 failures, measured on the healthy days (cascade rate ≥ 50%) of the previous 56 days with failures; ≥ 5 failures today; and today's cascade rate at least 50 pp below the baseline. Collapsed days are left out of the baseline, so a long outage does not lower it; the alert keeps firing while the previous 56 days with failures still hold healthy days with at least 50 failures (about eight weeks at the volumes of B / 05), then stops | 18 alerts: Acquirer B, code 05, MX and CO, 6–14 Aug |

The 21 July issuer outage in Brazil (three hours, one issuer) is visible in the hourly and issuer views but
does not trip the daily detector. Hourly detection is in the roadmap. Because the thresholds were tuned on
these 90 days, the backtest shows the rules fit this data; a fair test needs data they were not tuned on.
