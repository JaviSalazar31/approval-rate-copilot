"""
Single source of truth for the pipeline: table order, layer, comments, grain and
data-quality expectations. Used by:
  - local/run_local.py                 (runs the SQL bodies on local Spark)
  - tools/build_databricks.py          (generates the Databricks pipeline and notebook SQL)
  - tools/build_docs.py                (data dictionary, copilot context, column descriptions)
"""

SOURCES = {
    # placeholder -> (kind, relative path under the data root)
    "orchestrator": ("raw", "raw/orchestrator"),
    "acquirer_a": ("raw", "raw/acquirer_a"),
    "acquirer_b": ("raw", "raw/acquirer_b"),
    "acquirer_c": ("raw", "raw/acquirer_c"),
    "merchants": ("reference", "reference/merchants.csv"),
    "issuers": ("reference", "reference/issuers.csv"),
    "acquirers": ("reference", "reference/acquirers.csv"),
    "routing_rules": ("reference", "reference/routing_rules.csv"),
    "decline_code_map": ("reference", "reference/decline_code_map.csv"),
    "change_log": ("reference", "reference/change_log.csv"),
    "fx_rates": ("reference", "reference/fx_rates.csv"),
}

# Expectations on silver_auth_attempts (rows failing any rule are dropped there and
# kept in silver_dq_quarantine with the rule name).
AUTH_EXPECTATIONS = [
    ("has_payment_id", "payment_id IS NOT NULL"),
    ("valid_timestamp", "attempt_ts_utc IS NOT NULL"),
    ("valid_amount", "coalesce(amount > 0, false)"),
    ("mapped_code", "canonical_status IS NOT NULL"),
]

TABLES = [
    # name, sql file, kind, comment, expectations (name, condition, action)
    ("bronze_orchestrator", "bronze/bronze_orchestrator.sql", "streaming",
     "Raw payment intents from the orchestrator, as received.", []),
    ("bronze_acquirer_a", "bronze/bronze_acquirer_a.sql", "streaming",
     "Raw authorization responses from Acquirer A (ISO-8601 UTC, decimal amounts).", []),
    ("bronze_acquirer_b", "bronze/bronze_acquirer_b.sql", "streaming",
     "Raw authorization responses from Acquirer B (local time + offset, minor units, R-codes).", []),
    ("bronze_acquirer_c", "bronze/bronze_acquirer_c.sql", "streaming",
     "Raw authorization responses from Acquirer C (epoch millis, comma decimals, Portuguese labels).", []),
    ("ref_merchants", "ref/ref_merchants.sql", "mv", "Merchant reference data (fictional).", []),
    ("ref_issuers", "ref/ref_issuers.sql", "mv", "Card issuer reference data (fictional issuers).", []),
    ("ref_acquirers", "ref/ref_acquirers.sql", "mv", "Acquirers and their fees in basis points (synthetic).", []),
    ("ref_routing_rules", "ref/ref_routing_rules.sql", "mv", "Versioned routing rules: traffic share per acquirer.", []),
    ("ref_decline_code_map", "ref/ref_decline_code_map.sql", "mv",
     "Versioned map from raw acquirer status/code to canonical status, decline type and retryability.", []),
    ("ref_change_log", "ref/ref_change_log.sql", "mv", "Production changes: releases, routing, risk and mapping updates.", []),
    ("ref_fx_rates", "ref/ref_fx_rates.sql", "mv", "Fixed synthetic FX rates to USD.", []),
    ("ref_country_tz", "ref/ref_country_tz.sql", "mv", "Country time zones and currencies.", []),
    ("silver_payment_intents", "silver/silver_payment_intents.sql", "mv",
     "Payment intents from the orchestrator, typed and cleaned.",
     [("has_merchant", "merchant_id IS NOT NULL", "warn")]),
    ("silver_auth_attempts_staged", "silver/silver_auth_attempts_staged.sql", "mv",
     "All authorization attempts harmonized across acquirers, deduplicated and mapped (before quality rules).", []),
    ("silver_auth_attempts", "silver/silver_auth_attempts.sql", "mv",
     "Authorization attempts that pass the data-quality rules.",
     [(n, c, "drop") for n, c in AUTH_EXPECTATIONS]),
    ("silver_dq_quarantine", "silver/silver_dq_quarantine.sql", "mv",
     "Attempts rejected by the data-quality rules, with the rule they failed.", []),
    ("silver_payments", "silver/silver_payments.sql", "mv",
     "One row per payment with first attempt, cascade attempt and final outcome.", []),
    ("gold_approval_daily", "gold/gold_approval_daily.sql", "mv",
     "Daily approval KPIs by country, card type and first acquirer.", []),
    ("gold_approval_hourly", "gold/gold_approval_hourly.sql", "mv",
     "Hourly approval by country, card type and first acquirer, in local time.", []),
    ("gold_decline_reasons_daily", "gold/gold_decline_reasons_daily.sql", "mv",
     "Daily failed attempts by canonical reason and how the mapping classified them. Includes cross-border "
     "payments (there is no cross-border flag); fn_decline_shift covers domestic payments only.", []),
    ("gold_cascade_daily", "gold/gold_cascade_daily.sql", "mv",
     "Daily cascade health: failed first attempts, retries on another acquirer and recoveries."
     " Includes cross-border payments (there is no cross-border flag); fn_cascade_health covers domestic "
     "payments only.", []),
    ("gold_issuer_daily", "gold/gold_issuer_daily.sql", "mv",
     "Daily approval by issuer. Includes cross-border payments (there is no cross-border flag).", []),
    ("gold_funnel_daily", "gold/gold_funnel_daily.sql", "mv",
     "Daily payment funnel: intents, risk blocks, 3DS abandonment, sent, approved. Includes cross-border payments (there is no cross-border flag).", []),
    ("gold_anomalies_daily", "gold/gold_anomalies_daily.sql", "mv",
     "Daily domestic final approval per country and card type against its trailing 28-day baseline. Holds "
     "every day; is_anomaly marks the days that fall well below the baseline (volume-aware).", []),
    ("gold_cascade_alerts_daily", "gold/gold_cascade_alerts_daily.sql", "mv",
     "Daily cascade (retry) rate per country, first acquirer and first-attempt reason against its healthy "
     "history (the days with a cascade rate of at least 50% among the previous 56 days with failures). Holds "
     "every day; is_alert marks the days when the rate collapsed. Includes cross-border payments (there is "
     "no cross-border flag); fn_cascade_health covers domestic payments only.", []),
    ("gold_change_log", "gold/gold_change_log.sql", "mv", "Change log with local times per country.", []),
    ("gold_dq_summary", "gold/gold_dq_summary.sql", "mv", "Rows rejected by data-quality rules, by source and rule.", []),
]

# One row per ...: appended to the table comments ("Grain: ...") so Unity Catalog, the data dictionary
# and the copilot context describe each table the same way.
GRAIN = {
    "gold_approval_daily": "one row per local date × country × card type × first acquirer × cross-border flag",
    "gold_approval_hourly": "one row per local hour × country × card type × first acquirer × cross-border flag",
    "gold_decline_reasons_daily": "one row per local date × country × card type × acquirer × attempt number × reason "
                                  "× decline-code map version",
    "gold_cascade_daily": "one row per local date × country × card type × first acquirer × first-attempt reason "
                          "× decline-code map version",
    "gold_cascade_alerts_daily": "one row per local date × country × first acquirer × first-attempt reason",
    "gold_issuer_daily": "one row per local date × country × issuer × card type × first acquirer",
    "gold_funnel_daily": "one row per local date × country × card type",
    "gold_anomalies_daily": "one row per local date × country × card type (domestic traffic)",
    "gold_change_log": "one row per production change",
    "silver_payments": "one row per payment intent",
    "silver_payment_intents": "one row per payment intent",
    "silver_auth_attempts": "one row per authorization attempt (after de-duplication and quality rules)",
    "silver_dq_quarantine": "one row per rejected authorization attempt",
    "gold_dq_summary": "one row per source system × failed rule × raw status × raw code",
    "ref_merchants": "one row per merchant",
    "ref_issuers": "one row per issuer",
    "ref_acquirers": "one row per acquirer",
    "ref_routing_rules": "one row per routing version × validity period × country × card type × acquirer",
    "ref_decline_code_map": "one row per mapping version × source system × raw status × raw code",
    "ref_change_log": "one row per production change",
    "ref_fx_rates": "one row per currency",
    "ref_country_tz": "one row per country",
}


def table_comment(name, comment):
    """Table comment as written to Unity Catalog: description plus grain, when the grain is known."""
    return f"{comment} Grain: {GRAIN[name]}." if name in GRAIN else comment
