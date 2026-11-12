"""
Builds the project page from site/template.html:

  site/index.html          full HTML document for GitHub Pages
  site/artifact.html       the same page without <html>/<head>/<body>, for hosted previews

Numbers come from site/data.json (tools/export_site_data.py) and evals/results/summary.json, so the page
can never disagree with the pipeline output or the evaluation.

  python tools/build_site.py
  SITE_URL=https://<user>.github.io/<repo>/ REPO_URL=https://github.com/<user>/<repo> python tools/build_site.py
  (SITE_URL adds the link-preview image tags; REPO_URL links the documents in the footer)
"""
from __future__ import annotations

import html
import json
import os
import re

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SITE = os.path.join(ROOT, "site")

CATEGORY = {"kpi": "KPI lookup", "diagnosis": "Diagnosis", "comparison": "Comparison", "guardrail": "Guardrail"}
ORDINAL = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth", 7: "seventh",
           8: "eighth", 9: "ninth"}
TIMES = {1: "once", 2: "twice", 3: "three times", 4: "four times", 5: "five times"}


def fmt(value, f):
    if f == "int":
        return f"{round(value):,}"
    if f == "pct1":
        return f"{value * 100:.1f}%"
    if f == "pct0":
        return f"{value * 100:.0f}%"
    if f == "pp1":
        return f"{value:.1f}".replace("-", "−")
    if f == "spp1":  # signed: +3.6, −3.8
        return f"{value:+.1f}".replace("-", "−")
    if f == "abs1":
        return f"{abs(value):.1f}"
    if f == "round0":
        return f"{round(value):,}"
    if f == "round10":
        return f"{int(round(value, -1)):,}"
    if f == "k0":
        return f"{value / 1000:.0f}k"
    return str(value)


def inline_md(text):
    text = html.escape(text, quote=False)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    return text


UL_ITEM, OL_ITEM = re.compile(r"^\s*[-*] "), re.compile(r"^\s*\d+\. ")


def lines_to_html(lines):
    """Consecutive list items become one list; runs of other lines become one paragraph."""
    out, i = [], 0
    while i < len(lines):
        for pattern, tag in ((UL_ITEM, "ul"), (OL_ITEM, "ol")):
            if pattern.match(lines[i]):
                items = []
                while i < len(lines) and pattern.match(lines[i]):
                    items.append(f"<li>{inline_md(pattern.sub('', lines[i]))}</li>")
                    i += 1
                out.append(f"<{tag}>{''.join(items)}</{tag}>")
                break
        else:
            text = []
            while i < len(lines) and not (UL_ITEM.match(lines[i]) or OL_ITEM.match(lines[i])):
                text.append(inline_md(lines[i]))
                i += 1
            out.append("<p>" + "<br>".join(text) + "</p>")
    return out


def md_to_html(md):
    """Small, safe Markdown subset for model answers: paragraphs, bold, code, lists, tables."""
    out = []
    for block in re.split(r"\n\s*\n", md.strip()):
        lines = [l for l in block.split("\n") if l.strip()]
        if not lines:
            continue
        if all(l.lstrip().startswith("|") for l in lines):
            rows = [[c.strip() for c in l.strip().strip("|").split("|")] for l in lines
                    if not re.match(r"^\s*\|?\s*:?-{2,}", l)]
            head, body = rows[0], rows[1:]
            t = ["<div class=\"md-table\"><table><thead><tr>"]
            t += [f"<th>{inline_md(c)}</th>" for c in head]
            t.append("</tr></thead><tbody>")
            for r in body:
                t.append("<tr>" + "".join(f"<td>{inline_md(c)}</td>" for c in r) + "</tr>")
            t.append("</tbody></table></div>")
            out.append("".join(t))
        else:
            out += lines_to_html(lines)
    return "\n".join(out)


def verdict_chip(v, mark=""):
    if v is None:
        return '<span class="verdict">pending</span>'
    ok = v == "PASS"
    return (f'<span class="verdict {"pass" if ok else "fail"}"><span class="ic" aria-hidden="true">'
            f'{"✓" if ok else "✗"}</span><span class="t">{"Pass" if ok else "Fail"}</span>{mark}</span>')


def samples_chip(verdicts, mark=""):
    """One chip for a question asked several times: passed in every sample, in some, or in none."""
    n, k = len(verdicts), sum(v == "PASS" for v in verdicts)
    cls, ic = ("pass", "✓") if k == n else ("fail", "✗") if k == 0 else ("part", "")
    marks = " ".join("pass" if v == "PASS" else "fail" for v in verdicts)
    icon = f'<span class="ic" aria-hidden="true">{ic}</span>' if ic else ""
    return (f'<span class="verdict samples {cls}" title="samples: {marks}">{icon}'
            f'<span class="t">{k} of {n}{mark}</span></span>')


DOCS = [("PRD", "Problem, users, goals, requirements, rollout", "docs/PRD.md"),
        ("Metrics", "Metric tree, definitions and pitfalls", "docs/metrics.md"),
        ("Decision log", "Twenty-four decisions with their trade-offs", "docs/decisions.md"),
        ("Postmortem INC-0814", "Timeline, root cause, action items", "docs/postmortem_INC-0814.md"),
        ("Impact and detection", "Control-group estimate, forecast alert backtest", "docs/impact_and_detection.md"),
        ("Evaluation", "Question set, rubric, all runs", "evals/README.md"),
        ("Data model", "Entities, keys and table lineage", "docs/data_model.md"),
        ("Data dictionary", "Every table and column", "docs/data_dictionary.md"),
        ("Runbook", "Deploy on Databricks Free Edition", "docs/runbook_databricks.md"),
        ("Code", "Generator, pipeline, contracts, functions, tests", "")]


def docs_html():
    """Document list for the footer. With REPO_URL set (the GitHub URL), each entry links to its file."""
    repo = os.environ.get("REPO_URL", "").rstrip("/")
    out = []
    for name, desc, path in DOCS:
        title = html.escape(name)
        if repo:
            href = f"{repo}/blob/main/{path}" if path else repo
            title = f'<a href="{html.escape(href)}">{title}</a>'
        out.append(f"      <div><b>{title}</b><span>{html.escape(desc)}</span></div>")
    return "\n".join(out)


def build():
    data = json.load(open(os.path.join(SITE, "data.json")))
    summary = json.load(open(os.path.join(ROOT, "evals", "results", "summary.json")))
    questions = {q["id"]: q for q in json.load(open(os.path.join(ROOT, "evals", "eval_set.json")))}

    genie = summary["genie"]  # benchmark runs on the deployed Genie agent (evals/results/genie_run*.json)
    genie_runs = genie["runs"]
    rows, notes = [], []
    for item in summary["questions"]:
        q = questions[item["id"]]
        cells = []
        for run in (f"v{n}" for n in summary["runs"]):
            override = item.get(f"{run}_override")
            mark = ""
            if override:
                notes.append(override["note"])
                mark = "*" * len(notes)
            cells.append(f"<td data-label=\"{run[1:]}\">{verdict_chip(item.get(run), mark)}</td>")
        for i, g in enumerate(genie_runs):
            cls = ' class="genie"' if i == 0 else ""
            mark = "†" if item["id"] in g.get("overruled", []) else ""  # overruled to a failure; see the table note
            cells.append(f"<td data-label=\"G{g['run']}\"{cls}>{verdict_chip(g['verdicts'][item['id']], mark)}</td>")
        rows.append(f"<tr><td class=\"mono\">{item['id']}</td><td>{html.escape(q['question'])}</td>"
                    f"<td>{CATEGORY[q['category']]}</td>{''.join(cells)}</tr>")
    notes_html = "".join(f"<p class=\"table-note\">{'*' * (i + 1)} {html.escape(n)}</p>" for i, n in enumerate(notes))
    notes_html += f"<p class=\"table-note\">{html.escape(genie['table_note'])}</p>"
    fixes = []
    for f in summary["fixes"]:
        fixes.append(f"<div class=\"fix\"><div><h3>{html.escape(f['title'])}</h3><p>{html.escape(f['problem'])}</p></div>"
                     f"<p>{html.escape(f['change'])}</p></div>")
    genie_findings = []
    for f in genie["findings"]:
        genie_findings.append(f"<div class=\"fix\"><div><h3>{html.escape(f['title'])}</h3><p>{html.escape(f['problem'])}</p></div>"
                              f"<p>{html.escape(f['change'])}</p></div>")

    # held-out sets: questions the agent was never tuned on, committed before the run
    # (evals/results/heldout_run4.json, asked once; heldout2_run5.json to heldout6_run9.json, three samples each)
    held, held2, held3, held4 = genie["heldout"], genie["heldout2"], genie["heldout3"], genie["heldout4"]
    held5, held6 = genie["heldout5"], genie["heldout6"]
    hq = {q["id"]: q for q in json.load(open(os.path.join(ROOT, "evals", "heldout_set.json")))}
    heldout_rows = "\n".join(
        f'<tr><td class="mono">{qid}</td><td>{html.escape(q["question"])}</td><td>{CATEGORY[q["category"]]}</td>'
        f'<td data-label="Mode" class="mode">{q["mode"]}</td><td data-label="G{held["run"]}" class="genie">{verdict_chip(held["verdicts"].get(qid))}</td></tr>'
        for qid, q in hq.items())
    g = held["by_group"]
    heldout_intro = (f'The first set: {held["total"]} questions written after the last change to the agent and '
                     f'committed to the repository before Genie run {held["run"]}. The agent answered each one once '
                     f'and passed {held["passed"]} (KPI lookups {g.get("kpi", "–")}, comparisons '
                     f'{g.get("comparison", "–")}, diagnoses {g.get("diagnosis", "–")}, guardrails '
                     f'{g.get("guardrail", "–")}). They were graded by the judge of the benchmark\'s agent-mode questions, '
                     f'with the same kind of scoring rules. {held.get("note", "")}').strip()
    hq2 = {q["id"]: q for q in json.load(open(os.path.join(ROOT, "evals", "heldout_set_2.json")))}
    heldout2_rows = "\n".join(
        f'<tr><td class="mono">{qid}</td><td>{html.escape(q["question"])}</td><td>{CATEGORY[q["category"]]}</td>'
        f'<td data-label="Mode" class="mode">{q["mode"]}</td><td data-label="G{held2["run"]}" class="genie">{samples_chip(held2["samples_by_question"][qid])}</td></tr>'
        for qid, q in hq2.items())
    g2 = {k: v["answers"] for k, v in held2["by_group"].items()}
    heldout2_intro = (f'The second set: {held2["questions_total"]} new questions, committed before Genie run '
                      f'{held2["run"]} and asked {TIMES[held2["samples"]]} each, every time in a new conversation. '
                      f'{held2["answers_passed"]} of {held2["answers_total"]} answers passed (KPI lookups {g2.get("kpi", "–")}, '
                      f'comparisons {g2.get("comparison", "–")}, diagnoses {g2.get("diagnosis", "–")}, guardrails '
                      f'{g2.get("guardrail", "–")}), and {held2["questions_all_samples"]} of the {held2["questions_total"]} '
                      f'questions passed in every sample. {held2.get("note", "")}').strip()
    hq3 = {q["id"]: q for q in json.load(open(os.path.join(ROOT, "evals", "heldout_set_3.json")))}
    heldout3_rows = "\n".join(
        f'<tr><td class="mono">{qid}</td><td>{html.escape(q["question"])}</td><td>{CATEGORY[q["category"]]}</td>'
        f'<td data-label="Mode" class="mode">{q["mode"]}</td><td data-label="G{held3["run"]}" class="genie">'
        f'{samples_chip(held3["samples_by_question"][qid], "†" if any(o.startswith(qid + "_") for o in held3.get("overruled", [])) else "")}</td></tr>'
        for qid, q in hq3.items())
    g3 = {k: v["answers"] for k, v in held3["by_group"].items()}
    heldout3_intro = (f'The third set: {held3["questions_total"]} new questions, written after the release that addressed '
                      f'what the second set found. They were committed before Genie run {held3["run"]} and asked '
                      f'{TIMES[held3["samples"]]} each in the same way. {held3["answers_passed"]} of '
                      f'{held3["answers_total"]} answers passed (KPI lookups {g3.get("kpi", "–")}, comparisons '
                      f'{g3.get("comparison", "–")}, diagnoses {g3.get("diagnosis", "–")}, guardrails '
                      f'{g3.get("guardrail", "–")}), and {held3["questions_all_samples"]} of the '
                      f'{held3["questions_total"]} questions passed in every sample. † marks a question with verdicts I '
                      f'overruled to failures. {held3.get("note", "")}').strip()

    hq4 = {q["id"]: q for q in json.load(open(os.path.join(ROOT, "evals", "heldout_set_4.json")))}
    heldout4_rows = "\n".join(
        f'<tr><td class="mono">{qid}</td><td>{html.escape(q["question"])}</td><td>{CATEGORY[q["category"]]}</td>'
        f'<td data-label="Mode" class="mode">{q["mode"]}</td><td data-label="G{held4["run"]}" class="genie">'
        f'{samples_chip(held4["samples_by_question"][qid], "†" if any(o.startswith(qid + "_") for o in held4.get("overruled", [])) else "")}</td></tr>'
        for qid, q in hq4.items())
    g4 = {k: v["answers"] for k, v in held4["by_group"].items()}
    heldout4_intro = (f'The fourth set: {held4["questions_total"]} new questions, written after the release that addressed '
                      f'what run 6 and the third set found. They were committed before Genie run {held4["run"]} and asked '
                      f'{TIMES[held4["samples"]]} each in the same way. {held4["answers_passed"]} of '
                      f'{held4["answers_total"]} answers passed (KPI lookups {g4.get("kpi", "–")}, comparisons '
                      f'{g4.get("comparison", "–")}, diagnoses {g4.get("diagnosis", "–")}, guardrails '
                      f'{g4.get("guardrail", "–")}), and {held4["questions_all_samples"]} of the '
                      f'{held4["questions_total"]} questions passed in every sample. '
                      + ('† marks a question with verdicts I overruled to failures. ' if held4.get("overruled") else '')
                      + held4.get("note", "")).strip()

    hq5 = {q["id"]: q for q in json.load(open(os.path.join(ROOT, "evals", "heldout_set_5.json")))}
    heldout5_rows = "\n".join(
        f'<tr><td class="mono">{qid}</td><td>{html.escape(q["question"])}</td><td>{CATEGORY[q["category"]]}</td>'
        f'<td data-label="Mode" class="mode">{q["mode"]}</td><td data-label="G{held5["run"]}" class="genie">'
        f'{samples_chip(held5["samples_by_question"][qid], "†" if any(o.startswith(qid + "_") for o in held5.get("overruled", [])) else "")}</td></tr>'
        for qid, q in hq5.items())
    g5 = {k: v["answers"] for k, v in held5["by_group"].items()}
    heldout5_intro = (f'The fifth set: {held5["questions_total"]} new questions, written after the release that addressed '
                      f'what run 7 and the fourth set found. They were committed before Genie run {held5["run"]} and asked '
                      f'{TIMES[held5["samples"]]} each in the same way. {held5["answers_passed"]} of '
                      f'{held5["answers_total"]} answers passed (KPI lookups {g5.get("kpi", "–")}, comparisons '
                      f'{g5.get("comparison", "–")}, diagnoses {g5.get("diagnosis", "–")}, guardrails '
                      f'{g5.get("guardrail", "–")}), and {held5["questions_all_samples"]} of the '
                      f'{held5["questions_total"]} questions passed in every sample. '
                      + ('† marks a question with verdicts I overruled to failures. ' if held5.get("overruled") else '')
                      + held5.get("note", "")).strip()

    hq6 = {q["id"]: q for q in json.load(open(os.path.join(ROOT, "evals", "heldout_set_6.json")))}
    heldout6_rows = "\n".join(
        f'<tr><td class="mono">{qid}</td><td>{html.escape(q["question"])}</td><td>{CATEGORY[q["category"]]}</td>'
        f'<td data-label="Mode" class="mode">{q["mode"]}</td><td data-label="G{held6["run"]}" class="genie">'
        f'{samples_chip(held6["samples_by_question"][qid], "†" if any(o.startswith(qid + "_") for o in held6.get("overruled", [])) else "")}</td></tr>'
        for qid, q in hq6.items())
    g6 = {k: v["answers"] for k, v in held6["by_group"].items()}
    heldout6_intro = (f'The sixth set: {held6["questions_total"]} new questions, written after the release that addressed '
                      f'what run 8 and the fifth set found. They were committed before Genie run {held6["run"]} and asked '
                      f'{TIMES[held6["samples"]]} each in the same way. {held6["answers_passed"]} of '
                      f'{held6["answers_total"]} answers passed (KPI lookups {g6.get("kpi", "–")}, comparisons '
                      f'{g6.get("comparison", "–")}, diagnoses {g6.get("diagnosis", "–")}, guardrails '
                      f'{g6.get("guardrail", "–")}), and {held6["questions_all_samples"]} of the '
                      f'{held6["questions_total"]} questions passed in every sample. '
                      + ('† marks a question with verdicts I overruled to failures. ' if held6.get("overruled") else '')
                      + held6.get("note", "")).strip()

    answer = summary["showcase"]
    sql_html = "".join(f"<pre><code>{html.escape(s)}</code></pre>" for s in answer["queries"])
    case = dict(hourly_co_debit=data["hourly_co_debit"], cascade_b05=data["cascade_b05"], incident=data["incident"],
                analysis=data["analysis"])

    run_ids = summary["runs"]
    passed = summary["passed"]
    labels = summary.get("run_labels", {})
    head_html = "".join(f"<th>Run {n}</th>" for n in run_ids) + "".join(
        f"<th{' class=\"genie\"' if i == 0 else ''}>Genie {g['run']}</th>" for i, g in enumerate(genie_runs))
    score_html = "\n".join(
        f'      <div class="cell" role="listitem"><span class="v">{passed[str(n)]}/{summary["total"]}</span>'
        f'<span class="l">{html.escape(labels.get(str(n), f"run {n}"))}</span></div>' for n in run_ids)
    for i, g in enumerate(genie_runs):  # the Genie runs start a row of their own
        score_html += (f'\n      <div class="cell genie{" g-first" if i == 0 else ""}" role="listitem"><span class="v">{g["passed"]}/{g["total"]}</span>'
                       f'<span class="l">{html.escape(g["label"])}</span></div>')
    score_html += (f'\n      <div class="cell genie" role="listitem"><span class="v">{held["passed"]}/{held["total"]}</span>'
                   f'<span class="l">held-out questions, never used for tuning, asked once in Genie run '
                   f'{held["run"]}</span></div>')
    score_html += (f'\n      <div class="cell genie" role="listitem"><span class="v">{held2["answers_passed"]}/{held2["answers_total"]}</span>'
                   f'<span class="l">answers to {held2["questions_total"]} new held-out questions, each asked '
                   f'{TIMES[held2["samples"]]}, in Genie run {held2["run"]}</span></div>')
    score_html += (f'\n      <div class="cell genie" role="listitem"><span class="v">{held3["answers_passed"]}/{held3["answers_total"]}</span>'
                   f'<span class="l">answers to {held3["questions_total"]} more new questions, each asked '
                   f'{TIMES[held3["samples"]]}, in Genie run {held3["run"]}</span></div>')
    score_html += (f'\n      <div class="cell genie" role="listitem"><span class="v">{held4["answers_passed"]}/{held4["answers_total"]}</span>'
                   f'<span class="l">answers to {held4["questions_total"]} more new questions, each asked '
                   f'{TIMES[held4["samples"]]}, in Genie run {held4["run"]}</span></div>')
    score_html += (f'\n      <div class="cell genie" role="listitem"><span class="v">{held5["answers_passed"]}/{held5["answers_total"]}</span>'
                   f'<span class="l">answers to {held5["questions_total"]} more new questions, each asked '
                   f'{TIMES[held5["samples"]]}, in Genie run {held5["run"]}</span></div>')
    score_html += (f'\n      <div class="cell genie" role="listitem"><span class="v">{held6["answers_passed"]}/{held6["answers_total"]}</span>'
                   f'<span class="l">answers to {held6["questions_total"]} more new questions, each asked '
                   f'{TIMES[held6["samples"]]}, in Genie run {held6["run"]}</span></div>')
    genie_last = genie_runs[-1]
    genie_best = max(genie_runs, key=lambda r: r["passed"])
    genie_prev = genie_runs[-2]  # the run before the last, for the headline figure
    earlier = [str(passed[str(n)]) for n in run_ids[:-1]]
    earlier_passed = ", ".join(earlier[:-1]) + (" and " if len(earlier) > 1 else "") + earlier[-1]

    ctx = dict(
        headline=data["headline"], incident=data["incident"],
        pipeline=data["pipeline"], analysis=data["analysis"],
        eval=dict(total=summary["total"], last_run=run_ids[-1], last_passed=passed[str(run_ids[-1])],
                  earlier_passed=earlier_passed, runs=len(run_ids),
                  v1_judge_passed=summary["v1_judge_passed"],
                  sql_ok=summary["sql_ok"], guardrails=summary["guardrails"],
                  head_html=head_html, score_html=score_html,
                  rows_html="\n".join(rows), notes_html=notes_html, fixes_html="\n".join(fixes),
                  failure_modes=len(summary["fixes"]),
                  genie_first=genie_runs[0]["passed"], genie_passed=genie_last["passed"],
                  genie_runs=len(genie_runs), genie_findings_html="\n".join(genie_findings),
                  genie_last_ordinal=ORDINAL[len(genie_runs)],
                  genie_best=genie_best["passed"], genie_best_ordinal=ORDINAL[genie_best["run"]],
                  genie_prev=genie_prev["passed"], genie_prev_ordinal=ORDINAL[genie_prev["run"]],
                  genie_round3=genie_runs[3]["passed"],  # run 4, after the three tuning rounds of ADR-12
                  genie_run7=genie_runs[6]["passed"],  # the seventh release, which moved more of the method into functions
                  genie_run8=genie_runs[7]["passed"],  # the eighth, which added rules as text
                  genie_variants=genie["variants_passed_run4"].replace("/", " of "),
                  heldout_rows_html=heldout_rows, heldout_intro=html.escape(heldout_intro),
                  heldout_passed=held["passed"], heldout_total=held["total"], heldout_run=held["run"],
                  heldout2_rows_html=heldout2_rows, heldout2_intro=html.escape(heldout2_intro),
                  heldout2_answers_passed=held2["answers_passed"], heldout2_answers_total=held2["answers_total"],
                  heldout2_total=held2["questions_total"], heldout2_all=held2["questions_all_samples"],
                  heldout2_run=held2["run"], heldout2_times=TIMES[held2["samples"]],
                  heldout2_mixed=sum(0 < v.count("PASS") < len(v) for v in held2["samples_by_question"].values()),
                  heldout3_rows_html=heldout3_rows, heldout3_intro=html.escape(heldout3_intro),
                  heldout3_answers_passed=held3["answers_passed"], heldout3_answers_total=held3["answers_total"],
                  heldout3_total=held3["questions_total"], heldout3_all=held3["questions_all_samples"],
                  heldout3_run=held3["run"],
                  heldout3_mixed=sum(0 < v.count("PASS") < len(v) for v in held3["samples_by_question"].values()),
                  heldout4_rows_html=heldout4_rows, heldout4_intro=html.escape(heldout4_intro),
                  heldout4_answers_passed=held4["answers_passed"], heldout4_answers_total=held4["answers_total"],
                  heldout4_total=held4["questions_total"], heldout4_run=held4["run"],
                  heldout4_mixed=sum(0 < v.count("PASS") < len(v) for v in held4["samples_by_question"].values()),
                  heldout5_rows_html=heldout5_rows, heldout5_intro=html.escape(heldout5_intro),
                  heldout5_answers_passed=held5["answers_passed"], heldout5_answers_total=held5["answers_total"],
                  heldout5_total=held5["questions_total"], heldout5_run=held5["run"],
                  heldout5_mixed=sum(0 < v.count("PASS") < len(v) for v in held5["samples_by_question"].values()),
                  heldout6_rows_html=heldout6_rows, heldout6_intro=html.escape(heldout6_intro),
                  heldout6_answers_passed=held6["answers_passed"], heldout6_answers_total=held6["answers_total"],
                  heldout6_total=held6["questions_total"], heldout6_run=held6["run"],
                  heldout6_mixed=sum(0 < v.count("PASS") < len(v) for v in held6["samples_by_question"].values()),
                  heldout_questions=(held["total"] + held2["questions_total"] + held3["questions_total"]
                                     + held4["questions_total"] + held5["questions_total"] + held6["questions_total"])),
        answer=dict(question=html.escape(answer["question"]), html=md_to_html(answer["answer"]),
                    sql_html=sql_html, query_count=len(answer["queries"]),
                    run_label=html.escape(answer.get("run_label", "evaluation run"))),
        links=dict(note_html=(f"<p>{html.escape(summary['links_note'])}</p>" if summary.get("links_note") else ""),
                   docs_html=docs_html()),
    )

    tpl = open(os.path.join(SITE, "template.html")).read()

    def sub(m):
        key, _, f = m.group(1).partition("|")
        if key == "CASE_JSON":
            return json.dumps(case, separators=(",", ":")).replace("</", "<\\/")
        obj = ctx
        for part in key.split("."):
            obj = obj[part]
        return fmt(obj, f) if f else str(obj)

    page = re.sub(r"\{\{([A-Za-z0-9_.|]+)\}\}", sub, tpl)
    leftover = re.findall(r"\{\{[^}]+\}\}", page)
    if leftover:
        raise SystemExit(f"unfilled placeholders: {leftover}")
    open(os.path.join(SITE, "artifact.html"), "w").write(page)

    title = re.search(r"<title>.*?</title>", page).group(0)
    head_bits = re.findall(r"<(?:meta|link)[^>]*>", page.split("<style>")[0])
    # Link previews (LinkedIn, Slack): absolute URLs are required, so they are added only when SITE_URL is set
    site_url = os.environ.get("SITE_URL", "").rstrip("/")
    if not site_url or not os.environ.get("REPO_URL"):
        print("note: SITE_URL and/or REPO_URL not set: no link-preview tags or document links "
              "(set both before publishing, see README)")
    if site_url:
        head_bits += [f'<meta property="og:url" content="{site_url}/">',
                      f'<meta property="og:image" content="{site_url}/og.png">',
                      '<meta property="og:image:width" content="1200">',
                      '<meta property="og:image:height" content="627">']
    style = page[page.index("<style>"):page.index("</style>") + len("</style>")]
    body = page[page.index("</style>") + len("</style>"):]
    doc = ("<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
           "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">\n"
           f"{title}\n" + "\n".join(h for h in head_bits if "claude:" not in h) + f"\n{style}\n</head>\n<body>\n{body}\n</body>\n</html>\n")
    open(os.path.join(SITE, "index.html"), "w").write(doc)
    print(f"wrote site/index.html ({len(doc):,} chars) and site/artifact.html ({len(page):,} chars)")


if __name__ == "__main__":
    build()
