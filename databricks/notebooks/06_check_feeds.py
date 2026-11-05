# Databricks notebook source
# MAGIC %md
# MAGIC # 06 · Check the raw feeds against their data contracts
# MAGIC First check of the job, before the pipeline. It fails the run when a file is missing or empty,
# MAGIC when columns change, when a column's values stop matching their format, when the orchestrator's
# MAGIC volume is far off, when an acquirer file does not reconcile with what was routed to it, or when
# MAGIC any value looks like a full card number. Single bad rows are left to the pipeline's quarantine.
# MAGIC
# MAGIC Copy of `checks/feed_checks.py` with the contracts from `contracts/*.yml` embedded, made by
# MAGIC `tools/build_databricks.py`. Do not edit by hand.

# COMMAND ----------

"""
Checks each raw feed against its data contract (contracts/*.yml) before the pipeline runs.

  python checks/feed_checks.py --data data/full [--date 2026-08-29] [--all-dates]

What stops the run:
- the file for the run date is missing or empty (freshness);
- a contract column is missing, or the file has a column the contract does not know (schema);
- more than `max_invalid_share` of a column's values break its type, format or allowed values,
  which means the feed changed rather than a few rows being bad;
- the orchestrator file repeats a payment_id (unique key), which catches a delivery sent twice, or its row
  count is outside min_ratio..max_ratio of the median of the previous files (volume), which catches a
  truncated delivery;
- an acquirer file's first attempts do not reconcile with the payments the orchestrator routed to that
  acquirer on the same day (reconciliation). A routing change moves both sides, so it passes;
- any text or number value looks like a full card number (13 to 19 digits that pass the Luhn check).
  The analytics layer must never hold one (PRD requirement R10), so a single value is enough to stop.
  Timestamp columns are skipped: epoch milliseconds have 13 digits and some pass the Luhn check.

Single bad rows inside a valid file are not this check's job: the pipeline's quality rules route
them to silver_dq_quarantine with the rule they failed.

The same code runs locally, in CI and as a task of the Databricks job, right before the pipeline
(notebook 06 embeds it). Without a run date it checks the newest date found in any feed, so a missing
orchestrator file for a day the acquirers already sent is caught.
"""

import argparse
import csv
import glob
import json
import os
import re
import statistics
import sys
from datetime import datetime, timezone

ROOT = os.getcwd()  # unused here: the contracts are embedded below
CONTRACTS_DIR = os.path.join(ROOT, "contracts")
FEEDS = ["orchestrator", "acquirer_a", "acquirer_b", "acquirer_c"]
EPOCH_MS_RANGE = (1577836800000, 1893456000000)   # 2020-01-01 to 2030-01-01
PAN_CANDIDATE = re.compile(r"^[0-9][0-9 -]{11,22}[0-9]$")


def load_contracts(directory: str = CONTRACTS_DIR) -> dict:
    import yaml  # only needed when contracts are read from YAML files
    contracts = {}
    for feed in FEEDS:
        with open(os.path.join(directory, f"{feed}.yml"), encoding="utf-8") as f:
            contracts[feed] = yaml.safe_load(f)
    return contracts


def luhn_ok(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def looks_like_pan(value: str) -> bool:
    if not PAN_CANDIDATE.match(value):
        return False
    digits = re.sub(r"[ -]", "", value)
    return 13 <= len(digits) <= 19 and luhn_ok(digits)


def value_ok(col: dict, v: str) -> bool:
    t = col["type"]
    if t == "string":
        return re.match(col["pattern"], v) is not None if "pattern" in col else True
    if t == "enum":
        return v in col["values"]
    if t == "integer":
        return re.fullmatch(r"-?[0-9]+", v) is not None
    if t == "decimal":
        sep = re.escape(col.get("separator", "."))
        return re.fullmatch(rf"-?[0-9]+({sep}[0-9]+)?", v) is not None
    if t == "timestamp":
        try:
            datetime.strptime(v, col["format"])
            return True
        except ValueError:
            return False
    if t == "epoch_ms":
        return v.isdigit() and EPOCH_MS_RANGE[0] <= int(v) <= EPOCH_MS_RANGE[1]
    raise ValueError(f"unknown column type {t}")


def check_file(contract: dict, path: str) -> dict:
    """Schema, value and card-number checks for one file."""
    violations, invalid_share = [], {}
    with open(path, newline="", encoding=contract["format"].get("encoding", "utf-8")) as f:
        reader = csv.reader(f, delimiter=contract["format"].get("delimiter", ","))
        header = next(reader, None)
        rows = list(reader)
    if not header or not rows:
        return {"rows": 0, "violations": [{"check": "freshness", "detail": "file is empty"}], "invalid_share": {}}
    declared = [c["name"] for c in contract["columns"]]
    missing = [c for c in declared if c not in header]
    extra = [c for c in header if c not in declared]
    if missing:
        violations.append({"check": "schema", "detail": f"missing columns: {', '.join(missing)}"})
    if extra:
        violations.append({"check": "schema", "detail": f"columns not in the contract: {', '.join(extra)}"})
    pos = {name: i for i, name in enumerate(header)}
    limit = contract.get("max_invalid_share", 0.01)
    text_cols = []
    for col in contract["columns"]:
        if col["name"] not in pos:
            continue
        i = pos[col["name"]]
        if col["type"] in ("string", "enum", "integer", "decimal"):
            text_cols.append(i)
        bad = 0
        for r in rows:
            v = r[i] if i < len(r) else ""
            if v == "":
                bad += 0 if col.get("nullable") else 1
            elif not value_ok(col, v):
                bad += 1
        share = bad / len(rows)
        invalid_share[col["name"]] = round(share, 5)
        if share > limit:
            violations.append({"check": "values", "detail": f"{col['name']}: {share:.1%} of values break the "
                                                            f"contract ({col['type']}), limit {limit:.0%}"})
    text_cols += [pos[c] for c in extra]
    for key in contract.get("unique", []):
        if key in pos:
            values = [r[pos[key]] for r in rows if pos[key] < len(r)]
            dupes = len(values) - len(set(values))
            if dupes:
                violations.append({"check": "unique", "detail": f"{dupes} repeated values of {key}"})
    pans = sum(1 for r in rows for i in text_cols if i < len(r) and looks_like_pan(r[i]))
    if pans:
        violations.append({"check": "card_number", "detail": f"{pans} values look like full card numbers"})
    return {"rows": len(rows), "violations": violations, "invalid_share": invalid_share}


def count_rows(path: str) -> int:
    with open(path, "rb") as f:
        return max(sum(1 for _ in f) - 1, 0)


def column_count(path: str, column: str, value: str) -> int:
    with open(path, newline="", encoding="utf-8") as f:
        return sum(1 for r in csv.DictReader(f) if r.get(column) == value)


def check_reconciliation(rec: dict, path: str, orchestrator_path: str) -> tuple[float | None, list]:
    if not os.path.exists(orchestrator_path):
        return None, []          # the orchestrator's own freshness check reports the missing file
    routed = column_count(orchestrator_path, rec["orchestrator_column"], rec["acquirer"])
    first = column_count(path, rec["attempt_column"], rec["first_attempt"])
    if routed == 0:
        return None, ([] if first == 0 else
                      [{"check": "reconciliation", "detail": f"{first} first attempts, none routed here"}])
    ratio = first / routed
    if rec["min_ratio"] <= ratio <= rec["max_ratio"]:
        return ratio, []
    return ratio, [{"check": "reconciliation", "detail": f"{first} first attempts for {routed} payments routed "
                                                          f"here ({ratio:.3f}x)"}]


def check_feed(contract: dict, feed_dir: str, run_date: str) -> dict:
    path = os.path.join(feed_dir, f"{run_date}.csv")
    report = {"feed": contract["feed"], "date": run_date, "file": os.path.basename(path)}
    if not os.path.exists(path):
        return {**report, "status": "fail", "rows": 0,
                "violations": [{"check": "freshness", "detail": f"no file for {run_date}"}]}
    res = check_file(contract, path)
    report.update(rows=res["rows"], violations=res["violations"])
    vol = contract.get("volume")
    earlier = sorted(p for p in glob.glob(os.path.join(feed_dir, "*.csv"))
                     if os.path.basename(p) < f"{run_date}.csv")
    if vol and res["rows"]:
        history = [count_rows(p) for p in earlier[-vol["lookback_files"]:]]
        if len(history) >= 3:
            median = statistics.median(history)
            ratio = res["rows"] / median
            report["volume_ratio"] = round(ratio, 3)
            if not vol["min_ratio"] <= ratio <= vol["max_ratio"]:
                report["violations"].append({"check": "volume", "detail": f"{res['rows']} rows is {ratio:.2f}x the "
                                                                          f"median of the previous {len(history)} files"})
        else:
            report["volume_ratio"] = None   # not enough history yet: the check starts with the fourth file
    rec = contract.get("reconciliation")
    if rec and res["rows"]:
        orchestrator_path = os.path.join(os.path.dirname(feed_dir), "orchestrator", f"{run_date}.csv")
        ratio, problems = check_reconciliation(rec, path, orchestrator_path)
        report["reconciliation_ratio"] = None if ratio is None else round(ratio, 4)
        report["violations"] += problems
    report["status"] = "fail" if report["violations"] else "pass"
    return report


def latest_date(raw_dir: str) -> str:
    """Newest date found in any feed."""
    files = [p for f in FEEDS for p in glob.glob(os.path.join(raw_dir, f, "*.csv"))]
    if not files:
        raise SystemExit(f"no feed files under {raw_dir}")
    return max(os.path.basename(p)[:-4] for p in files)


def run(data_dir: str, run_date: str | None = None, contracts: dict | None = None, all_dates: bool = False) -> list[dict]:
    contracts = contracts or load_contracts()
    raw = os.path.join(data_dir, "raw")
    if all_dates:
        dates = sorted(os.path.basename(p)[:-4] for p in glob.glob(os.path.join(raw, "orchestrator", "*.csv")))
    else:
        dates = [run_date or latest_date(raw)]
    return [check_feed(contracts[f], os.path.join(raw, f), d) for d in dates for f in FEEDS]


def summarize(reports: list[dict]) -> str:
    lines = []
    for r in reports:
        vol = "" if r.get("volume_ratio") is None else f"  volume {r['volume_ratio']:.2f}x"
        rec = "" if r.get("reconciliation_ratio") is None else f"  reconciles {r['reconciliation_ratio']:.3f}x"
        lines.append(f"{r['status'].upper():4s}  {r['feed']:12s} {r['date']}  {r['rows']:>6,} rows{vol}{rec}")
        lines += [f"      {v['check']}: {v['detail']}" for v in r["violations"]]
    return "\n".join(lines)

# COMMAND ----------

CONTRACTS = {'acquirer_a': {'feed': 'acquirer_a',
                'description': 'Authorization responses from Acquirer A, one row per attempt (webhook '
                               're-deliveries included)',
                'delivery': 'one CSV file per business day, named YYYY-MM-DD.csv',
                'format': {'type': 'csv', 'delimiter': ',', 'header': True, 'encoding': 'utf-8'},
                'max_invalid_share': 0.01,
                'reconciliation': {'orchestrator_column': 'first_acquirer',
                                   'acquirer': 'A',
                                   'attempt_column': 'attempt_seq',
                                   'first_attempt': '1',
                                   'min_ratio': 0.98,
                                   'max_ratio': 1.02},
                'columns': [{'name': 'event_id', 'type': 'string', 'pattern': '^evt_a_[0-9a-f]{14}$'},
                            {'name': 'payment_ref', 'type': 'string', 'pattern': '^pay_[0-9a-f]{12}$'},
                            {'name': 'merchant_ref', 'type': 'string', 'pattern': '^(MX|CO|BR)-M[0-9]{2}$'},
                            {'name': 'created_at_utc',
                             'type': 'timestamp',
                             'format': '%Y-%m-%dT%H:%M:%S.%fZ'},
                            {'name': 'amount', 'type': 'decimal', 'separator': '.'},
                            {'name': 'currency', 'type': 'enum', 'values': ['MXN', 'COP', 'BRL']},
                            {'name': 'country', 'type': 'enum', 'values': ['MX', 'CO', 'BR']},
                            {'name': 'card_brand',
                             'type': 'enum',
                             'values': ['VISA', 'MASTERCARD', 'AMEX', 'ELO']},
                            {'name': 'card_type', 'type': 'enum', 'values': ['credit', 'debit']},
                            {'name': 'issuer_ref',
                             'type': 'string',
                             'pattern': '^(MX|CO|BR|INTL)-ISS-[0-9]{2}$'},
                            {'name': 'is_3ds', 'type': 'enum', 'values': ['true', 'false']},
                            {'name': 'installments', 'type': 'integer'},
                            {'name': 'status',
                             'type': 'enum',
                             'values': ['APPROVED', 'DECLINED', 'ERROR', 'TIMEOUT', 'VOIDED']},
                            {'name': 'response_code',
                             'type': 'string',
                             'pattern': '^[0-9A-Z]{2}$',
                             'nullable': True},
                            {'name': 'latency_ms', 'type': 'integer'},
                            {'name': 'attempt_seq', 'type': 'enum', 'values': ['1', '2']},
                            {'name': 'received_at_utc',
                             'type': 'timestamp',
                             'format': '%Y-%m-%dT%H:%M:%S.%fZ'}]},
 'acquirer_b': {'feed': 'acquirer_b',
                'description': 'Authorization responses from Acquirer B, one row per attempt',
                'delivery': 'one CSV file per business day, named YYYY-MM-DD.csv',
                'format': {'type': 'csv', 'delimiter': ',', 'header': True, 'encoding': 'utf-8'},
                'max_invalid_share': 0.01,
                'reconciliation': {'orchestrator_column': 'first_acquirer',
                                   'acquirer': 'B',
                                   'attempt_column': 'try_no',
                                   'first_attempt': '1',
                                   'min_ratio': 0.98,
                                   'max_ratio': 1.02},
                'columns': [{'name': 'id', 'type': 'string', 'pattern': '^B[0-9]{10}$'},
                            {'name': 'order_id', 'type': 'string', 'pattern': '^pay_[0-9a-f]{12}$'},
                            {'name': 'merchant', 'type': 'string', 'pattern': '^(MX|CO|BR)-M[0-9]{2}$'},
                            {'name': 'local_datetime', 'type': 'timestamp', 'format': '%d/%m/%Y %H:%M:%S'},
                            {'name': 'tz_offset', 'type': 'enum', 'values': ['-06:00', '-05:00', '-03:00']},
                            {'name': 'amount_minor', 'type': 'integer'},
                            {'name': 'ccy', 'type': 'enum', 'values': ['MXN', 'COP', 'BRL']},
                            {'name': 'ctry', 'type': 'enum', 'values': ['MX', 'CO', 'BR']},
                            {'name': 'brand', 'type': 'enum', 'values': ['VISA', 'MC', 'AMEX', 'ELO']},
                            {'name': 'funding', 'type': 'enum', 'values': ['CREDIT', 'DEBIT']},
                            {'name': 'issuer', 'type': 'string', 'pattern': '^(MX|CO|BR|INTL)-ISS-[0-9]{2}$'},
                            {'name': 'auth_3ds', 'type': 'enum', 'values': ['Y', 'N']},
                            {'name': 'cuotas', 'type': 'integer'},
                            {'name': 'result',
                             'type': 'enum',
                             'values': ['AUTHORIZED', 'REFUSED', 'FAILED', 'PENDING_TIMEOUT', 'REVERSED']},
                            {'name': 'reason_code',
                             'type': 'string',
                             'pattern': '^R[0-9A-Z]{2}$',
                             'nullable': True},
                            {'name': 'resp_time_ms', 'type': 'integer'},
                            {'name': 'try_no', 'type': 'enum', 'values': ['1', '2']}]},
 'acquirer_c': {'feed': 'acquirer_c',
                'description': 'Authorization responses from Acquirer C, one row per attempt',
                'delivery': 'one CSV file per business day, named YYYY-MM-DD.csv',
                'format': {'type': 'csv', 'delimiter': ',', 'header': True, 'encoding': 'utf-8'},
                'max_invalid_share': 0.01,
                'reconciliation': {'orchestrator_column': 'first_acquirer',
                                   'acquirer': 'C',
                                   'attempt_column': 'tentativa',
                                   'first_attempt': '1',
                                   'min_ratio': 0.98,
                                   'max_ratio': 1.02},
                'columns': [{'name': 'transacao_id', 'type': 'string', 'pattern': '^c-[0-9a-f]{16}$'},
                            {'name': 'pedido_id', 'type': 'string', 'pattern': '^pay_[0-9a-f]{12}$'},
                            {'name': 'loja_id', 'type': 'string', 'pattern': '^(MX|CO|BR)-M[0-9]{2}$'},
                            {'name': 'timestamp_ms', 'type': 'epoch_ms'},
                            {'name': 'valor', 'type': 'decimal', 'separator': ','},
                            {'name': 'moeda', 'type': 'enum', 'values': ['MXN', 'COP', 'BRL']},
                            {'name': 'pais', 'type': 'enum', 'values': ['MX', 'CO', 'BR']},
                            {'name': 'bandeira',
                             'type': 'enum',
                             'values': ['Visa', 'Mastercard', 'Amex', 'Elo']},
                            {'name': 'tipo_cartao', 'type': 'enum', 'values': ['CREDITO', 'DEBITO']},
                            {'name': 'emissor',
                             'type': 'string',
                             'pattern': '^(MX|CO|BR|INTL)-ISS-[0-9]{2}$'},
                            {'name': 'autenticado_3ds', 'type': 'enum', 'values': ['0', '1']},
                            {'name': 'parcelas', 'type': 'integer'},
                            {'name': 'situacao',
                             'type': 'enum',
                             'values': ['Aprovada', 'Negada', 'Erro', 'Expirada', 'Cancelada']},
                            {'name': 'codigo_retorno',
                             'type': 'string',
                             'pattern': '^[0-9A-Z]{2}$',
                             'nullable': True},
                            {'name': 'tempo_resposta_ms', 'type': 'integer'},
                            {'name': 'tentativa', 'type': 'enum', 'values': ['1', '2']}]},
 'orchestrator': {'feed': 'orchestrator',
                  'description': 'Payment intents created at checkout, one row per intent',
                  'delivery': 'one CSV file per business day, named YYYY-MM-DD.csv',
                  'format': {'type': 'csv', 'delimiter': ',', 'header': True, 'encoding': 'utf-8'},
                  'max_invalid_share': 0.01,
                  'volume': {'lookback_files': 7, 'min_ratio': 0.5, 'max_ratio': 1.5},
                  'unique': ['payment_id'],
                  'columns': [{'name': 'payment_id', 'type': 'string', 'pattern': '^pay_[0-9a-f]{12}$'},
                              {'name': 'merchant_id', 'type': 'string', 'pattern': '^(MX|CO|BR)-M[0-9]{2}$'},
                              {'name': 'created_at_utc', 'type': 'timestamp', 'format': '%Y-%m-%d %H:%M:%S'},
                              {'name': 'country', 'type': 'enum', 'values': ['MX', 'CO', 'BR']},
                              {'name': 'currency', 'type': 'enum', 'values': ['MXN', 'COP', 'BRL']},
                              {'name': 'amount', 'type': 'decimal', 'separator': '.'},
                              {'name': 'card_brand',
                               'type': 'enum',
                               'values': ['visa', 'mastercard', 'amex', 'elo']},
                              {'name': 'card_type', 'type': 'enum', 'values': ['credit', 'debit']},
                              {'name': 'issuer_id',
                               'type': 'string',
                               'pattern': '^(MX|CO|BR|INTL)-ISS-[0-9]{2}$'},
                              {'name': 'is_cross_border', 'type': 'enum', 'values': ['True', 'False']},
                              {'name': 'installments', 'type': 'integer'},
                              {'name': 'three_ds_requested', 'type': 'enum', 'values': ['True', 'False']},
                              {'name': 'three_ds_flow',
                               'type': 'enum',
                               'values': ['frictionless', 'challenge'],
                               'nullable': True},
                              {'name': 'orchestrator_status',
                               'type': 'enum',
                               'values': ['ROUTED',
                                          'BLOCKED_RISK',
                                          'REVIEW_TIMEOUT',
                                          'CUSTOMER_ABANDONED_3DS']},
                              {'name': 'routing_rule_version',
                               'type': 'string',
                               'pattern': '^v[0-9]+$',
                               'nullable': True},
                              {'name': 'first_acquirer',
                               'type': 'enum',
                               'values': ['A', 'B', 'C'],
                               'nullable': True},
                              {'name': 'channel', 'type': 'enum', 'values': ['web', 'app']},
                              {'name': 'card_bin', 'type': 'string', 'pattern': '^[0-9]{6}$'},
                              {'name': 'card_last4', 'type': 'string', 'pattern': '^[0-9]{4}$'}]}}

# COMMAND ----------

LANDING = "/Volumes/workspace/approval_copilot/landing"
# After a regeneration every file is new, so every date is checked; otherwise the run date (or the newest).
try:
    dbutils.widgets.text("run_date", "")  # noqa: F821
    dbutils.widgets.text("regenerate_data", "false")  # noqa: F821
    run_date = dbutils.widgets.get("run_date").strip() or None  # noqa: F821
    all_dates = dbutils.widgets.get("regenerate_data").strip().lower() == "true"  # noqa: F821
except NameError:
    run_date, all_dates = None, False
reports = run(LANDING, run_date, contracts=CONTRACTS, all_dates=all_dates)
print(summarize(reports))
failed = [r for r in reports if r["status"] == "fail"]
if failed:
    details = "; ".join(f"{r['feed']} {v['check']}: {v['detail']}" for r in failed for v in r["violations"])
    raise RuntimeError(f"{len(failed)} of {len(reports)} feed checks failed, so the pipeline does not run. {details}")
print(f"All {len(reports)} feed checks passed, {reports[0]['date']} to {reports[-1]['date']}")
