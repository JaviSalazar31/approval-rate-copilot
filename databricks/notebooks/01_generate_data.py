# Databricks notebook source
# MAGIC %md
# MAGIC # 01 · Generate the synthetic data
# MAGIC Writes 90 days of payments for a **fictional** LATAM payment orchestrator (Mexico, Colombia, Brazil)
# MAGIC into the landing volume: one folder per source system, one CSV per day, plus reference tables.
# MAGIC
# MAGIC About 660k payment intents and 700k authorization attempts. Takes a few minutes on serverless.
# MAGIC Everything is synthetic; parameters are illustrative, not market statistics.
# MAGIC
# MAGIC Use serverless environment version 4 or later (Environment side panel). With its versions
# MAGIC (numpy 2.1.3, pandas 2.2.3) the generator writes files byte-identical to the reference run.
# MAGIC
# MAGIC The next cell is a copy of `data_generator/generate_data.py` (made by `tools/build_databricks.py`),
# MAGIC so this notebook also works when imported on its own.

# COMMAND ----------

"""
Synthetic data generator for the Approval Rate Copilot case study.

Simulates 90 days of card payments for a FICTIONAL LATAM payment orchestrator
that routes traffic to three acquirers (A, B, C) in Mexico, Colombia and Brazil.

Everything here is synthetic. Parameters were chosen to make the case plausible;
they are not market statistics and do not describe any real company.

Outputs (CSV):
  raw/orchestrator/<date>.csv      payment intents logged by the orchestrator
  raw/acquirer_a/<date>.csv        auth responses, Acquirer A format (ISO-8601 UTC, decimal amounts)
  raw/acquirer_b/<date>.csv        auth responses, Acquirer B format (local time + offset, minor units, R-codes)
  raw/acquirer_c/<date>.csv        auth responses, Acquirer C format (epoch millis, comma decimals, Portuguese labels)
  reference/*.csv                  merchants, issuers, acquirers, routing rules, decline-code map, change log, FX

Usage (from the repository root):
  python data_generator/generate_data.py --out data/full --seed 42
"""

import argparse
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------------------
# Calendar and scale
# --------------------------------------------------------------------------------------
START_DATE = datetime(2026, 6, 1)          # first local business day
N_DAYS = 90                                # through 2026-08-29

COUNTRIES = {
    # utc_offset: hours (none of the three countries observes DST in 2026)
    "MX": dict(utc_offset=-6, currency="MXN", fx=18.20, minor=100, base_daily=2600,
               credit_share=0.55, installments_share=0.15, three_ds_share=0.30, cross_border=0.04),
    "CO": dict(utc_offset=-5, currency="COP", fx=4050.0, minor=100, base_daily=2400,
               credit_share=0.50, installments_share=0.25, three_ds_share=0.25, cross_border=0.06),
    "BR": dict(utc_offset=-3, currency="BRL", fx=5.45, minor=100, base_daily=2200,
               credit_share=0.70, installments_share=0.40, three_ds_share=0.45, cross_border=0.04),
}

ACQUIRERS_BY_COUNTRY = {"MX": ["A", "B", "C"], "CO": ["A", "B"], "BR": ["A", "C"]}

# Fees charged on approved volume (basis points). Synthetic.
ACQUIRER_FEES_BPS = {"A": 290, "B": 250, "C": 270}

# Median auth latency (ms) by acquirer
LATENCY_MEDIAN_MS = {"A": 620, "B": 880, "C": 700}

# --------------------------------------------------------------------------------------
# Approval model (first attempt). Values are probabilities / percentage points.
# --------------------------------------------------------------------------------------
BASE_APPROVAL = {
    ("MX", "credit"): 0.86, ("MX", "debit"): 0.79,
    ("CO", "credit"): 0.84, ("CO", "debit"): 0.85,
    ("BR", "credit"): 0.88, ("BR", "debit"): 0.76,
}

ACQUIRER_EFFECT = {
    ("MX", "A", "credit"): +0.010, ("MX", "A", "debit"): +0.010,
    ("MX", "B", "credit"): -0.010, ("MX", "B", "debit"): -0.020,
    ("MX", "C", "credit"): +0.000, ("MX", "C", "debit"): +0.010,
    ("CO", "A", "credit"): +0.010, ("CO", "A", "debit"): +0.000,
    ("CO", "B", "credit"): -0.020, ("CO", "B", "debit"): -0.100,   # B is weak on Colombian debit
    ("BR", "A", "credit"): -0.020, ("BR", "A", "debit"): -0.030,
    ("BR", "C", "credit"): +0.010, ("BR", "C", "debit"): +0.000,
}

SEGMENT_EFFECT = {
    "retail": 0.000, "food_delivery": +0.010, "travel": -0.020, "digital_subscriptions": -0.030,
    "gaming": -0.040, "education": 0.000, "marketplace": 0.000,
}

THREE_DS_UPLIFT = 0.04
CROSS_BORDER_EFFECT = -0.18
INSTALLMENTS_EFFECT = -0.02
NIGHT_EFFECT = -0.02          # 00:00-05:59 local

P_TECH_ERROR = {"A": 0.006, "B": 0.008, "C": 0.006}
P_TIMEOUT = {"A": 0.003, "B": 0.005, "C": 0.003}

P_RISK_BLOCK = 0.011
P_REVIEW_TIMEOUT = 0.0015
P_3DS_CHALLENGE = 0.35
P_CHALLENGE_ABANDON = 0.09
P_VOID_AFTER_APPROVAL = 0.015

# Second-attempt (cascade) approval probability by first-attempt reason
CASCADE_RECOVERY = {"05": 0.38, "1A": 0.55, "91": 0.45, "96": 0.62, "59": 0.20,
                    "TECH_ERROR": 0.80, "TIMEOUT": 0.75}
CASCADE_RECOVERY_ISSUER_OUTAGE = 0.04

# Decline reason distributions (conditional on a decline)
SOFT_CODES = ["05", "51", "61", "65", "1A", "91", "96", "59"]
HARD_CODES = ["14", "41", "43", "54", "57", "62", "N7"]
DECLINE_MIX = {
    "credit": {"05": .30, "51": .16, "61": .05, "65": .03, "1A": .06, "91": .03, "96": .02, "59": .04,
               "14": .05, "41": .015, "43": .015, "54": .07, "57": .06, "62": .03, "N7": .05},
    "debit":  {"05": .28, "51": .30, "61": .04, "65": .03, "1A": .07, "91": .03, "96": .02, "59": .03,
               "14": .04, "41": .01, "43": .01, "54": .05, "57": .04, "62": .02, "N7": .03},
    "cross_border": {"05": .40, "51": .10, "61": .03, "65": .02, "1A": .05, "91": .03, "96": .02, "59": .12,
                     "14": .04, "41": .01, "43": .01, "54": .04, "57": .08, "62": .02, "N7": .03},
    # Acquirer B on Colombian domestic debit: no 3DS data passed for debit, generic do-not-honor
    "b_co_debit": {"05": .48, "51": .14, "1A": .22, "91": .03, "96": .02, "14": .03, "54": .03,
                   "57": .03, "N7": .02},
    "issuer_outage": {"91": .90, "96": .05, "05": .05},
}
REASON_LABELS = {
    "00": "Approved", "05": "Do not honor", "51": "Insufficient funds", "61": "Exceeds amount limit",
    "65": "Exceeds frequency limit", "1A": "Additional authentication required (3DS)",
    "91": "Issuer or switch unavailable", "96": "System malfunction", "59": "Suspected fraud",
    "14": "Invalid card number", "41": "Lost card", "43": "Stolen card", "54": "Expired card",
    "57": "Transaction not permitted to cardholder", "62": "Restricted card", "N7": "CVV2 mismatch",
}
# Codes that may be retried on another acquirer (cascade). 51/61/65 are soft but not
# worth cascading (funds/limits), hard declines must never be retried.
RETRYABLE_CODES = {"05", "1A", "91", "96", "59"}

# --------------------------------------------------------------------------------------
# Timeline of changes (UTC). This is the "change log" an analyst would correlate with.
# --------------------------------------------------------------------------------------
UTC = timezone.utc
T = lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=UTC)  # noqa: E731

RISK_V3_START, RISK_V31 = T("2026-06-18 12:00"), T("2026-06-20 18:00")
ROUTING_V13_START = T("2026-07-02 15:00")
ISSUER_OUTAGE_START, ISSUER_OUTAGE_END = T("2026-07-21 12:00"), T("2026-07-21 15:00")
MAPPING_V23_START = T("2026-08-06 13:00")
ROUTING_V14_START = T("2026-08-13 19:00")          # 14:00 in Bogota
ROUTING_V14_ROLLBACK = T("2026-08-14 17:10")       # 12:10 in Bogota
MAPPING_V231_START = T("2026-08-15 13:00")
NEW_MERCHANTS_START = T("2026-08-20 14:00")

CHANGE_LOG = [
    ("CHG-0601", "2026-06-03 15:00", "release", "ALL", "Checkout UI v5.2 (copy and layout changes)", "web"),
    ("CHG-0618", "2026-06-18 12:00", "risk_rules", "BR", "Risk rules v3: new card-velocity rule", "risk-engine"),
    ("CHG-0620", "2026-06-20 18:00", "risk_rules", "BR", "Risk rules v3.1: velocity threshold tuned after review", "risk-engine"),
    ("CHG-0702", "2026-07-02 15:00", "routing", "MX", "Routing v13: 10% of MX debit to Acquirer C (A/B test)", "routing"),
    ("EXT-0721", "2026-07-21 11:45", "external_notice", "BR", "Acquirer notice: Issuer BR-ISS-03 scheduled maintenance 12:00-15:00 UTC", "partner-notice"),
    ("CHG-0728", "2026-07-28 16:00", "release", "MX,CO", "3DS SDK upgrade on web checkout", "web"),
    ("CHG-0806", "2026-08-06 13:00", "decline_mapping", "B", "Decline-code mapping v2.3 for Acquirer B: adds R65 handling", "normalization"),
    ("CHG-0813", "2026-08-13 19:00", "routing", "CO", "Routing v14: 100% of CO domestic debit to Acquirer B (cost optimization, -40 bps)", "routing"),
    ("CHG-0814", "2026-08-14 17:10", "rollback", "CO", "Rollback routing v14 -> v13 (incident INC-0814)", "routing"),
    ("CHG-0815", "2026-08-15 13:00", "decline_mapping", "B", "Mapping hotfix v2.3.1: R05 reclassified as soft and retryable", "normalization"),
    ("CHG-0820", "2026-08-20 14:00", "merchant_onboarding", "MX", "Two new MX merchants onboarded", "commercial"),
]

# Routing rules: (version, valid_from, valid_to, country, card_type, scope, weights)
FAR_FUTURE = T("2099-12-31 00:00")
EPOCH = T("2026-01-01 00:00")
ROUTING_BASE = {
    ("MX", "credit"): {"A": .60, "C": .40},
    ("MX", "debit"):  {"A": .50, "B": .50},
    ("CO", "credit"): {"A": .70, "B": .30},
    ("CO", "debit"):  {"A": 1.0},
    ("BR", "credit"): {"C": .70, "A": .30},
    ("BR", "debit"):  {"C": 1.0},
}


def routing_versions():
    v12 = dict(ROUTING_BASE)
    v13 = dict(ROUTING_BASE)
    v13[("MX", "debit")] = {"A": .45, "B": .45, "C": .10}
    v14 = dict(v13)
    v14[("CO", "debit")] = {"B": 1.0}   # domestic debit only; cross-border stays on A
    return [
        ("v12", EPOCH, ROUTING_V13_START, v12),
        ("v13", ROUTING_V13_START, ROUTING_V14_START, v13),
        ("v14", ROUTING_V14_START, ROUTING_V14_ROLLBACK, v14),
        ("v13", ROUTING_V14_ROLLBACK, FAR_FUTURE, v13),
    ]


ROUTING_VERSIONS = routing_versions()

# --------------------------------------------------------------------------------------
# Reference data
# --------------------------------------------------------------------------------------
MERCHANT_TEMPLATES = [
    # segment, mcc, avg_ticket_usd, weight
    ("retail", "5732", 120, 0.14), ("retail", "5651", 55, 0.12), ("marketplace", "5399", 40, 0.14),
    ("food_delivery", "5814", 14, 0.12), ("food_delivery", "5812", 22, 0.08), ("travel", "4722", 260, 0.06),
    ("digital_subscriptions", "4899", 9, 0.10), ("digital_subscriptions", "5815", 12, 0.06),
    ("gaming", "5816", 18, 0.06), ("education", "8299", 75, 0.05), ("retail", "5311", 65, 0.05),
    ("marketplace", "5399", 30, 0.02),
]
ISSUER_SHARES = [0.24, 0.18, 0.14, 0.12, 0.10, 0.09, 0.07, 0.06]
ISSUER_OFFSETS = {
    "MX": [+0.02, +0.01, 0.00, -0.01, -0.03, +0.01, -0.02, -0.04],
    "CO": [+0.01, +0.02, -0.01, 0.00, -0.02, +0.01, -0.03, 0.00],
    "BR": [+0.02, 0.00, +0.01, -0.02, +0.01, -0.01, -0.03, +0.02],
}
# Colombian issuers that perform worst with Acquirer B on debit
B_CO_DEBIT_WEAK_ISSUERS = {"CO-ISS-03": -0.08, "CO-ISS-05": -0.06}
INTL_ISSUERS = ["INTL-ISS-01", "INTL-ISS-02", "INTL-ISS-03"]

BRANDS = {"MX": (["visa", "mastercard", "amex"], [0.55, 0.40, 0.05]),
          "CO": (["visa", "mastercard"], [0.60, 0.40]),
          "BR": (["visa", "mastercard", "elo"], [0.45, 0.45, 0.10])}

HOURLY_PROFILE = np.array([1.2, 0.8, 0.6, 0.5, 0.5, 0.7, 1.4, 2.6, 3.8, 4.6, 5.2, 5.8,
                           6.6, 6.4, 5.6, 5.3, 5.4, 5.8, 6.2, 6.8, 7.0, 6.4, 4.6, 2.5])
HOURLY_PROFILE = HOURLY_PROFILE / HOURLY_PROFILE.sum()
WEEKDAY_MULT = [0.98, 0.97, 1.00, 1.02, 1.12, 0.88, 0.80]   # Monday..Sunday


def build_merchants(rng):
    rows = []
    for c in COUNTRIES:
        for i, (seg, mcc, ticket, w) in enumerate(MERCHANT_TEMPLATES, start=1):
            rows.append(dict(merchant_id=f"{c}-M{i:02d}", country=c, segment=seg, mcc=mcc,
                             avg_ticket_usd=ticket, weight=w, onboarded_utc="2025-01-01 00:00"))
    # two merchants onboarded in MX on 2026-08-20
    rows.append(dict(merchant_id="MX-M13", country="MX", segment="retail", mcc="5732", avg_ticket_usd=140,
                     weight=0.05, onboarded_utc="2026-08-20 14:00"))
    rows.append(dict(merchant_id="MX-M14", country="MX", segment="food_delivery", mcc="5814", avg_ticket_usd=16,
                     weight=0.05, onboarded_utc="2026-08-20 14:00"))
    return pd.DataFrame(rows)


def build_issuers():
    rows = []
    for c in COUNTRIES:
        for i, (share, off) in enumerate(zip(ISSUER_SHARES, ISSUER_OFFSETS[c]), start=1):
            rows.append(dict(issuer_id=f"{c}-ISS-{i:02d}", issuer_country=c, market_share=share,
                             approval_offset=off, issuer_type="domestic"))
    for j, iid in enumerate(INTL_ISSUERS):
        rows.append(dict(issuer_id=iid, issuer_country="INTL", market_share=1 / 3, approval_offset=0.0,
                         issuer_type="cross_border"))
    return pd.DataFrame(rows)


def build_acquirers():
    return pd.DataFrame([
        dict(acquirer_id="A", acquirer_name="Acquirer A", countries="MX,CO,BR", fee_bps=ACQUIRER_FEES_BPS["A"],
             raw_format="ISO-8601 UTC timestamps, decimal amounts, ISO response codes"),
        dict(acquirer_id="B", acquirer_name="Acquirer B", countries="MX,CO", fee_bps=ACQUIRER_FEES_BPS["B"],
             raw_format="Local time + UTC offset, amounts in minor units, R-prefixed reason codes"),
        dict(acquirer_id="C", acquirer_name="Acquirer C", countries="MX,BR", fee_bps=ACQUIRER_FEES_BPS["C"],
             raw_format="Epoch milliseconds, comma decimals, Portuguese status labels"),
    ])


def build_routing_rules():
    rows = []
    for ver, start, end, table in ROUTING_VERSIONS:
        for (c, ct), weights in table.items():
            for acq, w in weights.items():
                rows.append(dict(routing_version=ver, valid_from_utc=start.strftime("%Y-%m-%d %H:%M:%S"),
                                 valid_to_utc=end.strftime("%Y-%m-%d %H:%M:%S"), country=c, card_type=ct,
                                 scope="domestic", acquirer_id=acq, traffic_share=w))
    return pd.DataFrame(rows)


RAW_STATUS = {
    "A": dict(approved="APPROVED", declined="DECLINED", error="ERROR", timeout="TIMEOUT", reversed="VOIDED"),
    "B": dict(approved="AUTHORIZED", declined="REFUSED", error="FAILED", timeout="PENDING_TIMEOUT", reversed="REVERSED"),
    "C": dict(approved="Aprovada", declined="Negada", error="Erro", timeout="Expirada", reversed="Cancelada"),
}


def raw_code(acq, iso):
    if iso in ("", None):
        return ""
    return f"R{iso}" if acq == "B" else iso


def build_decline_code_map():
    """Versioned normalization map: (acquirer, raw_status, raw_code) -> canonical fields."""
    versions = [("v2.2", EPOCH, MAPPING_V23_START), ("v2.3", MAPPING_V23_START, MAPPING_V231_START),
                ("v2.3.1", MAPPING_V231_START, FAR_FUTURE)]
    rows = []

    def add(ver, start, end, acq, rs, rc, iso, canon, dtype, retry):
        rows.append(dict(mapping_version=ver, valid_from_utc=start.strftime("%Y-%m-%d %H:%M:%S"),
                         valid_to_utc=end.strftime("%Y-%m-%d %H:%M:%S"), source_system=acq, raw_status=rs,
                         raw_code=rc, iso_code=iso, reason=REASON_LABELS.get(iso, canon.replace("_", " ").title()),
                         canonical_status=canon, decline_type=dtype, retryable=retry))

    for ver, start, end in versions:
        for acq in ["A", "B", "C"]:
            st = RAW_STATUS[acq]
            add(ver, start, end, acq, st["approved"], raw_code(acq, "00"), "00", "APPROVED", "none", False)
            add(ver, start, end, acq, st["reversed"], raw_code(acq, "00"), "00", "REVERSED", "none", False)
            add(ver, start, end, acq, st["error"], "", "", "TECHNICAL_ERROR", "technical", True)
            add(ver, start, end, acq, st["timeout"], "", "", "TIMEOUT", "technical", True)
            for iso in SOFT_CODES + HARD_CODES:
                if acq == "B" and iso == "65" and ver == "v2.2":
                    continue  # R65 was not mapped before v2.3 -> lands in quarantine
                soft = iso in SOFT_CODES
                canon, dtype, retry = ("DECLINED_SOFT", "soft", iso in RETRYABLE_CODES) if soft else \
                                      ("DECLINED_HARD", "hard", False)
                if acq == "B" and iso == "05" and ver == "v2.3":
                    canon, dtype, retry = "DECLINED_HARD", "hard", False   # the latent bug
                add(ver, start, end, acq, st["declined"], raw_code(acq, iso), iso, canon, dtype, retry)
        # Orchestrator-level outcomes (never reach an acquirer)
        add(ver, start, end, "ORCHESTRATOR", "BLOCKED_RISK", "", "", "BLOCKED_BY_RISK", "risk", False)
        add(ver, start, end, "ORCHESTRATOR", "REVIEW_TIMEOUT", "", "", "BLOCKED_BY_RISK", "risk", False)
        add(ver, start, end, "ORCHESTRATOR", "CUSTOMER_ABANDONED_3DS", "", "", "ABANDONED", "customer", False)
    df = pd.DataFrame(rows)
    # Fix the reason label for non-code rows
    df.loc[df.iso_code == "", "reason"] = df.loc[df.iso_code == "", "canonical_status"].str.replace("_", " ").str.title()
    return df


def build_change_log():
    return pd.DataFrame([dict(change_id=i, changed_at_utc=ts + ":00", change_type=t, scope=s, description=d, owner_team=o)
                         for i, ts, t, s, d, o in CHANGE_LOG])


def build_fx():
    return pd.DataFrame([dict(currency=v["currency"], usd_rate=v["fx"], note="Synthetic fixed rate") for v in COUNTRIES.values()])


# --------------------------------------------------------------------------------------
# Simulation
# --------------------------------------------------------------------------------------
def active_routing(ts_utc, country, card_type):
    for ver, start, end, table in ROUTING_VERSIONS:
        if start <= ts_utc < end:
            return ver, table[(country, card_type)]
    raise ValueError("no routing version")


def mapping_retryable(acq, iso, ts_utc):
    """Runtime cascade decision uses the mapping version active at the time of the attempt."""
    if iso in ("TECH_ERROR", "TIMEOUT"):
        return True
    if acq == "B" and iso == "05" and MAPPING_V23_START <= ts_utc < MAPPING_V231_START:
        return False  # bug: R05 treated as hard decline
    if acq == "B" and iso == "65" and ts_utc < MAPPING_V23_START:
        return False  # unmapped code: no cascade
    return iso in RETRYABLE_CODES


def sample_codes(rng, mix_key, n):
    mix = DECLINE_MIX[mix_key]
    codes = list(mix.keys())
    p = np.array(list(mix.values()), dtype=float)
    p = p / p.sum()
    return rng.choice(codes, size=n, p=p)


def simulate_day_country(rng, day_idx, country, merchants, issuers):
    cfg = COUNTRIES[country]
    local_day = START_DATE + timedelta(days=day_idx)
    dom = local_day.day
    weekday = local_day.weekday()

    payday = 1.0
    if country in ("MX", "CO") and dom in (1, 15, 16, 30, 31):
        payday = 1.18 if country == "MX" else 1.12
    if country == "BR" and dom in (5, 6, 7):
        payday = 1.12
    trend = 1 + 0.0008 * day_idx
    n = int(round(cfg["base_daily"] * WEEKDAY_MULT[weekday] * payday * trend * rng.normal(1, 0.04)))

    # timestamps (local -> UTC)
    hours = rng.choice(24, size=n, p=HOURLY_PROFILE)
    secs = rng.integers(0, 3600, size=n)
    local_ts = np.array([local_day + timedelta(hours=int(h), seconds=int(s)) for h, s in zip(hours, secs)])
    utc_ts = np.array([(t - timedelta(hours=cfg["utc_offset"])).replace(tzinfo=UTC) for t in local_ts])

    # merchants active at that time
    m = merchants[merchants.country == country].copy()
    m["onboarded"] = pd.to_datetime(m["onboarded_utc"]).dt.tz_localize("UTC")
    day_utc_mid = (local_day + timedelta(hours=12 - cfg["utc_offset"])).replace(tzinfo=UTC)
    m = m[m["onboarded"] <= day_utc_mid]
    mw = m["weight"].to_numpy() / m["weight"].sum()
    midx = rng.choice(len(m), size=n, p=mw)
    merchant_id = m["merchant_id"].to_numpy()[midx]
    segment = m["segment"].to_numpy()[midx]
    avg_ticket = m["avg_ticket_usd"].to_numpy()[midx]

    # card attributes
    card_type = np.where(rng.random(n) < cfg["credit_share"], "credit", "debit")
    # subscriptions and gaming skew to credit
    skew = np.isin(segment, ["digital_subscriptions", "travel"]) & (rng.random(n) < 0.25)
    card_type = np.where(skew, "credit", card_type)
    installments = np.where((card_type == "credit") & (rng.random(n) < cfg["installments_share"]) &
                            np.isin(segment, ["retail", "travel", "education", "marketplace"]),
                            rng.choice([3, 6, 12], size=n, p=[0.5, 0.3, 0.2]), 1)
    brands, bp = BRANDS[country]
    card_brand = rng.choice(brands, size=n, p=bp)
    card_brand = np.where((card_brand == "amex") & (card_type == "debit"), "visa", card_brand)  # Amex is credit-only

    cross_border = rng.random(n) < cfg["cross_border"]
    dom_iss = issuers[issuers.issuer_country == country]
    iss_idx = rng.choice(len(dom_iss), size=n, p=dom_iss["market_share"].to_numpy() / dom_iss["market_share"].sum())
    issuer_id = np.where(cross_border, rng.choice(INTL_ISSUERS, size=n), dom_iss["issuer_id"].to_numpy()[iss_idx])
    iss_off_map = dict(zip(issuers.issuer_id, issuers.approval_offset))
    issuer_off = np.array([iss_off_map[i] for i in issuer_id])

    amount_usd = np.exp(rng.normal(np.log(avg_ticket), 0.6))
    amount_usd = np.clip(amount_usd, 1.0, 5000)
    amount_local = np.round(amount_usd * cfg["fx"], 0 if country == "CO" else 2)

    # 3DS
    three_ds_req = rng.random(n) < cfg["three_ds_share"]
    challenge = three_ds_req & (rng.random(n) < P_3DS_CHALLENGE)
    abandoned = challenge & (rng.random(n) < P_CHALLENGE_ABANDON)
    three_ds_flow = np.where(~three_ds_req, "", np.where(challenge, "challenge", "frictionless"))
    authenticated = three_ds_req & ~abandoned

    # risk
    p_block = np.full(n, P_RISK_BLOCK)
    if country == "BR":
        in_v3 = np.array([RISK_V3_START <= t < RISK_V31 for t in utc_ts])
        p_block = np.where(in_v3, P_RISK_BLOCK + 0.020, p_block)
    r = rng.random(n)
    blocked = (r < p_block) & ~abandoned
    review_to = (~blocked) & (~abandoned) & (r < p_block + P_REVIEW_TIMEOUT)

    orch_status = np.where(abandoned, "CUSTOMER_ABANDONED_3DS",
                           np.where(blocked, "BLOCKED_RISK", np.where(review_to, "REVIEW_TIMEOUT", "ROUTED")))
    routed = orch_status == "ROUTED"

    # routing
    routing_version = np.empty(n, dtype=object)
    first_acq = np.empty(n, dtype=object)
    u = rng.random(n)
    for i in range(n):
        ver, weights = active_routing(utc_ts[i], country, card_type[i])
        if cross_border[i]:
            # cross-border traffic always goes to Acquirer A (multi-currency settlement)
            ver_w = {"A": 1.0}
        else:
            ver_w = weights
        routing_version[i] = ver
        acc = 0.0
        chosen = list(ver_w.keys())[-1]
        for acq, w in ver_w.items():
            acc += w
            if u[i] < acc:
                chosen = acq
                break
        first_acq[i] = chosen
    first_acq = np.where(routed, first_acq, "")

    hour_local = hours
    daily_noise = rng.normal(0, 0.004)

    payments = pd.DataFrame(dict(
        payment_id=[f"pay_{x:012x}" for x in rng.integers(0, 2**48, size=n)],
        merchant_id=merchant_id, segment=segment, created_at_utc=utc_ts, country=country,
        currency=cfg["currency"], amount=amount_local, amount_usd=amount_usd, card_brand=card_brand,
        card_type=card_type, issuer_id=issuer_id, is_cross_border=cross_border, installments=installments,
        three_ds_requested=three_ds_req, three_ds_flow=three_ds_flow, authenticated=authenticated,
        orchestrator_status=orch_status, routing_rule_version=np.where(routed, routing_version, ""),
        first_acquirer=first_acq, channel=rng.choice(["web", "app"], size=n, p=[0.58, 0.42]),
        hour_local=hour_local, issuer_off=issuer_off,
    ))

    # ---------------- attempts ----------------
    att = payments[payments.orchestrator_status == "ROUTED"].copy()
    m_ = len(att)
    acq = att["first_acquirer"].to_numpy()
    ct = att["card_type"].to_numpy()
    ts = att["created_at_utc"].to_numpy()
    is_xb = att["is_cross_border"].to_numpy()
    iss = att["issuer_id"].to_numpy()

    p = np.array([BASE_APPROVAL[(country, c)] for c in ct])
    p += np.array([ACQUIRER_EFFECT[(country, a, c)] for a, c in zip(acq, ct)])
    p += att["issuer_off"].to_numpy()
    p += np.array([SEGMENT_EFFECT[s] for s in att["segment"].to_numpy()])
    p += np.where(att["authenticated"].to_numpy(), THREE_DS_UPLIFT, 0.0)
    p += np.where(is_xb, CROSS_BORDER_EFFECT, 0.0)
    p += np.where(att["installments"].to_numpy() > 1, INSTALLMENTS_EFFECT, 0.0)
    p += np.where(att["hour_local"].to_numpy() < 6, NIGHT_EFFECT, 0.0)
    p += daily_noise
    b_co_debit = (country == "CO") & (acq == "B") & (ct == "debit") & ~is_xb
    weak_iss = np.array([B_CO_DEBIT_WEAK_ISSUERS.get(i, 0.0) for i in iss])
    p += np.where(b_co_debit, weak_iss, 0.0)
    # B passes no 3DS data on CO debit: authentication uplift is lost
    p -= np.where(b_co_debit & att["authenticated"].to_numpy(), THREE_DS_UPLIFT, 0.0)
    outage = (iss == "BR-ISS-03") & np.array([ISSUER_OUTAGE_START <= t < ISSUER_OUTAGE_END for t in ts])
    p = np.where(outage, p * 0.15, p)
    p = np.clip(p, 0.02, 0.995)

    rr = rng.random(m_)
    tech = rr < np.array([P_TECH_ERROR[a] for a in acq])
    tmo = (~tech) & (rr < np.array([P_TECH_ERROR[a] + P_TIMEOUT[a] for a in acq]))
    approved = (~tech) & (~tmo) & (rng.random(m_) < p)
    declined = (~tech) & (~tmo) & (~approved)

    # decline codes
    codes = np.full(m_, "", dtype=object)
    for key, mask in [("issuer_outage", declined & outage),
                      ("b_co_debit", declined & b_co_debit & ~outage),
                      ("cross_border", declined & is_xb & ~outage),
                      ("credit", declined & (ct == "credit") & ~is_xb & ~b_co_debit & ~outage),
                      ("debit", declined & (ct == "debit") & ~is_xb & ~b_co_debit & ~outage)]:
        k = int(mask.sum())
        if k:
            codes[mask] = sample_codes(rng, key, k)
    # Acquirer B also returns R65 (frequency limit) on some declines, and very rarely an
    # undocumented R99. Neither is retried: R65 is not retryable and R99 is unknown.
    rb = rng.random(m_)
    b_decl = declined & (acq == "B")
    codes = np.where(b_decl & (rb < 0.012), "65", codes)
    codes = np.where(b_decl & (rb > 0.9995), "99", codes)
    codes = np.where(approved, "00", codes)

    latency = np.exp(rng.normal(np.log([LATENCY_MEDIAN_MS[a] for a in acq]), 0.40)).astype(int)
    latency = np.where(tmo, rng.integers(10000, 15000, size=m_), latency)
    latency = np.where(outage & declined, latency + rng.integers(1500, 4000, size=m_), latency)

    status_kind = np.where(approved, "approved", np.where(tech, "error", np.where(tmo, "timeout", "declined")))
    reversed_ = approved & (rng.random(m_) < P_VOID_AFTER_APPROVAL)
    status_kind = np.where(reversed_, "reversed", status_kind)

    att_ts = np.array([t + np.timedelta64(int(rng.integers(150, 900)), "ms") for t in ts])
    first = pd.DataFrame(dict(payment_id=att["payment_id"].to_numpy(), acquirer=acq, attempt_seq=1,
                              attempt_ts=att_ts, status_kind=status_kind, iso=codes, latency_ms=latency))

    # cascade decision
    reason_for_cascade = np.where(tech, "TECH_ERROR", np.where(tmo, "TIMEOUT", codes))
    eligible = np.array([(sk in ("declined", "error", "timeout")) and mapping_retryable(a, rc, t)
                         for sk, a, rc, t in zip(status_kind, acq, reason_for_cascade, ts)])
    alt = []
    for a, c in zip(acq, ct):
        options = [x for x in ACQUIRERS_BY_COUNTRY[country] if x != a]
        # prefer A as fallback, then C, then B
        options.sort(key=lambda x: {"A": 0, "C": 1, "B": 2}[x])
        alt.append(options[0] if options else "")
    alt = np.array(alt)
    do_cascade = eligible & (alt != "")
    rec_p = np.array([CASCADE_RECOVERY.get(rc, 0.0) for rc in reason_for_cascade])
    rec_p = np.where(outage, CASCADE_RECOVERY_ISSUER_OUTAGE, rec_p)
    # cascades to B on Colombian debit are weaker
    rec_p = np.where((alt == "B") & (ct == "debit") & (country == "CO"), rec_p * 0.6, rec_p)
    sec_approved = do_cascade & (rng.random(m_) < rec_p)

    sec_n = int(do_cascade.sum())
    sec_codes = np.full(sec_n, "", dtype=object)
    sec_kind = np.where(sec_approved[do_cascade], "approved", "declined")
    k_decl = int((sec_kind == "declined").sum())
    if k_decl:
        sec_codes[sec_kind == "declined"] = sample_codes(rng, "credit", k_decl)
    sec_codes = np.where(sec_kind == "approved", "00", sec_codes)
    sec_rev = (sec_kind == "approved") & (rng.random(sec_n) < P_VOID_AFTER_APPROVAL)
    sec_kind = np.where(sec_rev, "reversed", sec_kind)
    sec_acq = alt[do_cascade]
    sec_lat = np.exp(rng.normal(np.log([LATENCY_MEDIAN_MS[a] for a in sec_acq]), 0.40)).astype(int)
    sec_ts = np.array([t + np.timedelta64(int(l + rng.integers(200, 600)), "ms")
                       for t, l in zip(att_ts[do_cascade], latency[do_cascade])])
    second = pd.DataFrame(dict(payment_id=att["payment_id"].to_numpy()[do_cascade], acquirer=sec_acq,
                               attempt_seq=2, attempt_ts=sec_ts, status_kind=sec_kind, iso=sec_codes,
                               latency_ms=sec_lat))
    attempts = pd.concat([first, second], ignore_index=True)
    attempts = attempts.merge(payments[["payment_id", "merchant_id", "country", "currency", "amount", "card_brand",
                                        "card_type", "issuer_id", "installments", "authenticated"]],
                              on="payment_id", how="left")
    return payments, attempts


# --------------------------------------------------------------------------------------
# Raw formatting per source system (this is what the pipeline has to normalize)
# --------------------------------------------------------------------------------------
def fmt_acquirer_a(df, rng):
    out = pd.DataFrame(dict(
        event_id=[f"evt_a_{x:014x}" for x in rng.integers(0, 2**56, size=len(df))],
        payment_ref=df.payment_id, merchant_ref=df.merchant_id,
        created_at_utc=pd.to_datetime(df.attempt_ts, utc=True).dt.strftime("%Y-%m-%dT%H:%M:%S.%f").str[:-3] + "Z",
        amount=df.amount.map(lambda v: f"{v:.2f}"), currency=df.currency, country=df.country,
        card_brand=df.card_brand.str.upper(), card_type=df.card_type, issuer_ref=df.issuer_id,
        is_3ds=df.authenticated.map({True: "true", False: "false"}), installments=df.installments,
        status=df.status_kind.map(RAW_STATUS["A"]),
        response_code=np.where(df.status_kind.isin(["error", "timeout"]), "", df.iso),
        latency_ms=df.latency_ms, attempt_seq=df.attempt_seq,
    ))
    delay = rng.exponential(4.0, size=len(df))
    late = rng.random(len(df)) < 0.003
    delay = np.where(late, rng.uniform(3600, 172800, size=len(df)), delay)
    recv = pd.to_datetime(df.attempt_ts, utc=True) + pd.to_timedelta(delay, unit="s")
    out["received_at_utc"] = recv.dt.strftime("%Y-%m-%dT%H:%M:%S.%f").str[:-3] + "Z"
    # webhook re-deliveries: duplicated events (same event_id, later received_at)
    dup = out.sample(frac=0.004, random_state=int(rng.integers(0, 1e9))).copy()
    dup["received_at_utc"] = (pd.to_datetime(dup["received_at_utc"]) + pd.to_timedelta(30, unit="s")) \
        .dt.strftime("%Y-%m-%dT%H:%M:%S.%f").str[:-3] + "Z"
    return pd.concat([out, dup], ignore_index=True)


def fmt_acquirer_b(df, rng):
    offs = df.country.map({c: v["utc_offset"] for c, v in COUNTRIES.items()})
    local = pd.to_datetime(df.attempt_ts, utc=True) + pd.to_timedelta(offs, unit="h")
    code = np.where(df.status_kind.isin(["error", "timeout"]), "", "R" + df.iso.astype(str))
    amount_minor = (df.amount * 100).round().astype("int64")
    bad = rng.random(len(df)) < 0.0005
    amount_minor = np.where(bad, 0, amount_minor)
    out = pd.DataFrame(dict(
        id=[f"B{x:010d}" for x in rng.integers(0, 9_999_999_999, size=len(df))],
        order_id=df.payment_id, merchant=df.merchant_id,
        local_datetime=local.dt.strftime("%d/%m/%Y %H:%M:%S"),
        tz_offset=offs.map(lambda h: f"{'-' if h < 0 else '+'}{abs(h):02d}:00"),
        amount_minor=amount_minor, ccy=df.currency, ctry=df.country,
        brand=df.card_brand.str.upper().replace({"MASTERCARD": "MC"}),
        funding=df.card_type.str.upper(), issuer=df.issuer_id,
        auth_3ds=df.authenticated.map({True: "Y", False: "N"}), cuotas=df.installments,
        result=df.status_kind.map(RAW_STATUS["B"]), reason_code=code,
        resp_time_ms=df.latency_ms, try_no=df.attempt_seq,
    ))
    return out


def fmt_acquirer_c(df, rng):
    ts = pd.to_datetime(df.attempt_ts, utc=True)
    # Epoch milliseconds, computed the same way on pandas 1.5 and 2.x (Series.dt.as_unit needs pandas 2)
    ms = ((ts - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(milliseconds=1)).astype("int64")
    moeda = df.currency.copy()
    miss = rng.random(len(df)) < 0.001
    moeda = moeda.where(~miss, "")
    out = pd.DataFrame(dict(
        transacao_id=[f"c-{x:016x}" for x in rng.integers(0, 2**62, size=len(df))],
        pedido_id=df.payment_id, loja_id=df.merchant_id, timestamp_ms=ms,
        valor=df.amount.map(lambda v: f"{v:.2f}".replace(".", ",")), moeda=moeda, pais=df.country,
        bandeira=df.card_brand.str.title().replace({"Mastercard": "Mastercard"}),
        tipo_cartao=df.card_type.map({"credit": "CREDITO", "debit": "DEBITO"}), emissor=df.issuer_id,
        autenticado_3ds=df.authenticated.map({True: "1", False: "0"}), parcelas=df.installments,
        situacao=df.status_kind.map(RAW_STATUS["C"]),
        codigo_retorno=np.where(df.status_kind.isin(["error", "timeout"]), "", df.iso),
        tempo_resposta_ms=df.latency_ms, tentativa=df.attempt_seq,
    ))
    return out


def fmt_orchestrator(p, rng):
    out = pd.DataFrame(dict(
        payment_id=p.payment_id, merchant_id=p.merchant_id,
        created_at_utc=pd.to_datetime(p.created_at_utc, utc=True).dt.strftime("%Y-%m-%d %H:%M:%S"),
        country=p.country, currency=p.currency,
        amount=p.amount.map(lambda v: f"{v:.2f}"), card_brand=p.card_brand, card_type=p.card_type,
        issuer_id=p.issuer_id, is_cross_border=p.is_cross_border, installments=p.installments,
        three_ds_requested=p.three_ds_requested, three_ds_flow=p.three_ds_flow,
        orchestrator_status=p.orchestrator_status, routing_rule_version=p.routing_rule_version,
        first_acquirer=p.first_acquirer, channel=p.channel,
    ))
    miss = rng.random(len(out)) < 0.0003
    out.loc[miss, "merchant_id"] = ""
    return out


def generate(out: str, seed: int = 42, days: int = N_DAYS) -> dict:
    """Writes the raw and reference files under `out`. Returns row counts."""
    rng = np.random.default_rng(seed)
    out = os.path.abspath(out)
    for sub in ["raw/orchestrator", "raw/acquirer_a", "raw/acquirer_b", "raw/acquirer_c", "reference"]:
        os.makedirs(os.path.join(out, sub), exist_ok=True)

    merchants = build_merchants(rng)
    issuers = build_issuers()
    merchants.drop(columns=["weight"]).to_csv(os.path.join(out, "reference/merchants.csv"), index=False)
    issuers.drop(columns=["approval_offset"]).to_csv(os.path.join(out, "reference/issuers.csv"), index=False)
    build_acquirers().to_csv(os.path.join(out, "reference/acquirers.csv"), index=False)
    build_routing_rules().to_csv(os.path.join(out, "reference/routing_rules.csv"), index=False)
    build_decline_code_map().to_csv(os.path.join(out, "reference/decline_code_map.csv"), index=False)
    build_change_log().to_csv(os.path.join(out, "reference/change_log.csv"), index=False)
    build_fx().to_csv(os.path.join(out, "reference/fx_rates.csv"), index=False)

    totals = dict(payments=0, attempts=0)
    for d in range(days):
        day = (START_DATE + timedelta(days=d)).strftime("%Y-%m-%d")
        pay_parts, att_parts = [], []
        for c in COUNTRIES:
            p, a = simulate_day_country(rng, d, c, merchants, issuers)
            pay_parts.append(p)
            att_parts.append(a)
        P = pd.concat(pay_parts, ignore_index=True)
        A = pd.concat(att_parts, ignore_index=True)
        fmt_orchestrator(P, rng).to_csv(os.path.join(out, f"raw/orchestrator/{day}.csv"), index=False)
        fmt_acquirer_a(A[A.acquirer == "A"], rng).to_csv(os.path.join(out, f"raw/acquirer_a/{day}.csv"), index=False)
        fmt_acquirer_b(A[A.acquirer == "B"], rng).to_csv(os.path.join(out, f"raw/acquirer_b/{day}.csv"), index=False)
        fmt_acquirer_c(A[A.acquirer == "C"], rng).to_csv(os.path.join(out, f"raw/acquirer_c/{day}.csv"), index=False)
        totals["payments"] += len(P)
        totals["attempts"] += len(A)
    return totals


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "data", "full"))
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--days", type=int, default=N_DAYS)
    args = ap.parse_args()
    totals = generate(args.out, args.seed, args.days)
    print(f"Generated {totals['payments']:,} payment intents and {totals['attempts']:,} "
          f"authorization attempts into {os.path.abspath(args.out)}")

# COMMAND ----------

LANDING = "/Volumes/workspace/approval_copilot/landing"

print("numpy", np.__version__, "| pandas", pd.__version__)
totals = generate(LANDING, seed=42)
print(f"{totals['payments']:,} payment intents | {totals['attempts']:,} authorization attempts")
# Reference run (seed 42): 661,301 payment intents | 700,038 authorization attempts.

# COMMAND ----------

for folder in ["raw/orchestrator", "raw/acquirer_a", "raw/acquirer_b", "raw/acquirer_c", "reference"]:
    files = dbutils.fs.ls(f"{LANDING}/{folder}")  # noqa: F821  (dbutils exists in Databricks)
    print(f"{folder:20s} {len(files):>3} files")
