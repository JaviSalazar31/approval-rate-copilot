# Judge guide · Approval Rate Copilot evaluation

You grade answers written by a data copilot (the "model under test") to questions about a synthetic
payments dataset. For each question you get one bundle file with: the question, its scoring rule, the
ground truth (gold SQL and its results), the model's final answer, and the query results the model saw.

Be strict and fair. Grade what the answer says to the user, not how it got there.

## Verdicts

Each question gets `PASS` or `FAIL`, plus a score from 0 to 1.

### numeric
- Find the headline number that answers the question. Rates may be written as percentages (79.4% = 0.794).
- PASS if |answer − expected| ≤ `tolerance` (absolute, on the fraction scale) or ≤ `relative_tolerance`
  × expected. A rate shown with one decimal (e.g. 79.4%) has rounding error up to 0.05 pp: that is fine.
- FAIL if the headline number is outside tolerance, missing, or if the answer gives two conflicting headline numbers.

### numeric_multi
- Every listed field must be stated and within its relative tolerance.

### ranking
- `winner` must be named as the winner. If there is a `gap_field`, the stated gap (in percentage points)
  must be within `gap_tolerance_pp` of the expected gap computed from the ground truth.
- `LOWEST_ISSUER`: the issuer named as lowest must be the lowest in the ground truth.

### facts
- Each fact has an essential claim. A fact is covered if the answer states that claim, in any wording.
  Details inside a fact that are clearly illustrative (for example "in Mexico and Colombia" when the answer
  correctly describes the same alert for the country it analyzed) do not decide coverage on their own;
  the core claim does. Hedged statements count if the answer still commits to the claim as its conclusion.
- PASS only if all `required` facts are covered and the answer has no material error.
- Score = covered required facts / required facts (optional facts are reported but not scored).

### guardrail
- PASS if the behavior matches `expected_behavior`.

## Material errors (any of these makes the verdict FAIL)
- A wrong cause, or a cause stated as fact that contradicts the ground truth.
- A headline number that contradicts the ground truth beyond tolerance.
- An unsupported number: a figure presented as data that does not appear in, and cannot be simply derived
  from (sum, difference, ratio, rounding), the query results the model saw. List every such number you find.
- Inventing data the dataset does not have (real bank names, chargebacks, forecasts as facts).

Minor issues (wording, rounding in non-headline numbers, extra context that is correct) are noted but do
not change the verdict.

## Output

Reply with ONLY a JSON array, one object per question, in this shape:

```json
[{"id": "B1", "verdict": "PASS", "score": 1.0,
  "covered": ["K2", "K3", "K4", "K5"], "missing": [],
  "numeric_check": "lost approvals 110 vs 110 expected",
  "unsupported_numbers": [], "material_errors": [],
  "notes": "one or two sentences on what was good or wrong"}]
```
