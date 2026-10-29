# Postmortem · INC-0814 (synthetic scenario) · Colombian debit approval drop (13–14 Aug 2026)

This postmortem follows a blameless format. The company is fictional and the data is synthetic: the incident was
planted in the data generator to test whether the pipeline, the detectors and the copilot can find and explain it.
All numbers come from the pipeline output (`fn_drop_impact`, `fn_cascade_health`, `gold_cascade_alerts_daily`).

## Summary

A routing change meant to save 40 bps sent all Colombian domestic debit traffic to Acquirer B. Final approval
for that segment fell from 86.5% to 75.6% for 22 hours. Two causes stacked. The routing change was the
trigger: B approves Colombian debit less often than A. A decline-code map released a week earlier was the
latent cause: it had stopped B's "do not honor" declines from being retried on another acquirer. The routing
change was rolled back after 22 hours; the map was fixed the next day.

## Impact

| | Value | Source |
|---|---|---|
| Segment | Colombia, domestic debit | |
| Window | 13 Aug 14:00 to 14 Aug 12:10 (Bogotá) | change log |
| Payments in the window | 1,004 | `fn_drop_impact` |
| Final approval | From 86.5% (14-day baseline) to 75.6% (−10.9 pp) | `fn_drop_impact` |
| First-attempt approval | From 84.6% to 70.7% | `fn_drop_impact` |
| Lost approvals | 110 (about 1 payment in 9) | `fn_drop_impact` |
| Lost approved volume | USD 6,354 | `fn_drop_impact` |
| Fees saved by the change in the same window | ≈ USD 180 | fees the window's approved volume would have paid on Acquirer A (40 bps more than B) minus the fees it paid |

Lost approved volume is an upper bound on lost sales: some shoppers retry with another card, and the
orchestrator loses only its margin on a lost sale. Even so, the trade was about USD 35 of lost approved sales
for every dollar of fees saved.

The map bug had a wider impact (6–15 Aug, all of Acquirer B's domestic traffic): 578 "do not honor" declines
were not retried. In the 28 days before the bug, 36% of these were recovered by a retry on another acquirer in
Colombia and 40% in Mexico, so about 220 approvals (≈ USD 16k of approved volume) were not recovered. About 50
of them fall inside the window above and are already part of its 110 lost approvals.

## Timeline

| UTC | Bogotá | Event |
|---|---|---|
| 1 Jun to 6 Aug | | Quarantine keeps rejecting Acquirer B responses with code R65, which is not in the decline-code map (722 rows) |
| 6 Aug 13:00 | 08:00 | CHG-0806: decline-code map v2.3 for Acquirer B released to handle R65. It also reclassifies R05 ("do not honor") from soft/retryable to hard/non-retryable |
| 7 Aug (daily batch) | | Cascade alert for 6 Aug fires for B / 05 in Mexico and Colombia: retry rate down from 100% to 9% on 6 Aug (5% in Colombia, 12% in Mexico), then 0% from 7 Aug. It fires again in every daily run through the 14 Aug data. *(In the planted scenario the alert has no owner, so nobody acts on it: this is the detection gap.)* |
| 13 Aug 19:00 | 14:00 | CHG-0813: routing v14 sends 100% of Colombian domestic debit to Acquirer B (cost optimization, −40 bps) |
| From 13 Aug 19:00 | From 14:00 | Hourly final approval for the segment drops from about 86% to between 58% and 83% in most hours |
| 14 Aug (daily batch) | | Anomaly flagged for CO debit on 13 Aug: 79.4% vs 86.7% (28-day detection baseline), z = −7.3 |
| 14 Aug 17:10 | 12:10 | CHG-0814: routing rolled back to v13. Approval returns to baseline |
| 15 Aug 13:00 | 08:00 | CHG-0815: map hotfix v2.3.1 reclassifies R05 as soft and retryable. Retries of B / 05 resume |

## Root cause

1. The latent cause was a map regression introduced on 6 Aug. The v2.3 release fixed a real gap (unmapped R65)
   and, in the same change, flipped R05 to hard/non-retryable. There was no regression test on the retryable flag
   of existing codes, and the release was not followed by a check of retry rates.
   An open question for the review is whether the flip was meant as a response to card-network retry rules.
   "Do not honor" is a generic response and is not one of Visa's "never approve" codes, so Visa allows a retry
   within its reattempt limits; on Mastercard it depends on the merchant advice code, which these feeds do not
   carry. If v2.3 was meant to cut reattempts, the right fix is a narrower rule (for example, no retry after a
   Mastercard "do not try again" advice code), not a plain revert. See ADR-08.
2. The trigger, on 13 Aug, was a routing change judged on fees instead of approved volume. Routing v14 moved a
   whole segment to the cheapest acquirer at once, without a ramp or an approval guardrail. B's first-attempt
   approval on Colombian debit is lower than A's, and with the map bug its "do not honor" declines had no second
   chance.

I estimated a rough split of the 10.9 pp drop; the copilot reports that it cannot make the split
from its functions. Had B's 137 "do not honor" declines in the window been retried and recovered at B's
Colombian rate before the bug (36%), about 50 more payments would have been approved: about 4.9 pp
(4.7–5.2 pp with B's Colombian rate since June or the Mexico-and-Colombia rate). B approving less
Colombian debit than A explains the remaining 6.0 pp (5.7–6.2).

## Detection: what worked and what didn't

- The cascade alert worked: it flagged the map bug in its first daily run (7 Aug), six days before the visible
  incident.
- The daily anomaly check flagged 13 Aug, its only flag in the 90 days. Those are the same days its thresholds
  were tuned on, so the flag shows fit; it does not prove the thresholds work on new data. The check also misses
  the three-hour issuer outage of 21 July, which only an hourly check would catch.
- The cascade alert had no owner, so nobody acted on it, and the map bug stayed live for nine days (6 to 15 Aug).
  The alert needs a named owner and a runbook entry.
- The daily cadence was too slow here: a daily detector reports a 14:00 problem the next morning. Hourly detection
  on the busiest segments could have cut the exposure from 22 hours to a few hours.

## How the copilot changes the response

Asked "Why did the approval rate for debit cards in Colombia drop on 13 August?", the copilot finds the
window, runs the diagnostic functions and returns the routing change, the decline-code shift at B, the
retries switched off under map v2.3, the impact in pp, approvals and USD, and the recommended actions. That
answer is evaluated in the eval set (question B1); see [evals/README.md](../evals/README.md).

## Action items

| # | Action | Type | Owner (fictional) |
|---|---|---|---|
| 1 | Regression test on decline type and retryable flag for every existing code before a map release | Prevent | Normalization team |
| 2 | Cascade alert routed to an on-call owner, with a runbook entry | Detect | Payments operations |
| 3 | Routing changes ramp from 10% to 50% to 100%, with an approved-volume guardrail at each step | Prevent | Routing team |
| 4 | Evaluate routing changes on cost per approved dollar, not fee bps alone | Process | Payments product |
| 5 | Hourly anomaly detection for the top segments | Detect | Data team |
| 6 | Retry policy written against card-network reattempt rules, and checked by the map release gate | Prevent | Payments product |
| 7 | Add this incident's questions to the copilot benchmark set (done: B1–B4, C3, C4) | Learn | Payments product |
