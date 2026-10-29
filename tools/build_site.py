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


def fmt(value, f):
    if f == "int":
        return f"{round(value):,}"
    if f == "pct1":
        return f"{value * 100:.1f}%"
    if f == "pct0":
        return f"{value * 100:.0f}%"
    if f == "pp1":
        return f"{value:.1f}".replace("-", "−")
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


DOCS = [("PRD", "Problem, users, goals, requirements, rollout", "docs/PRD.md"),
        ("Metrics", "Metric tree, definitions and pitfalls", "docs/metrics.md"),
        ("Decision log", "Eleven decisions with their trade-offs", "docs/decisions.md"),
        ("Postmortem INC-0814", "Timeline, root cause, action items", "docs/postmortem_INC-0814.md"),
        ("Evaluation", "Question set, rubric, all runs", "evals/README.md"),
        ("Data dictionary", "Every table and column", "docs/data_dictionary.md"),
        ("Runbook", "Deploy on Databricks Free Edition", "docs/runbook_databricks.md"),
        ("Code", "Generator, pipeline SQL, functions, tests", "")]


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

    genie = summary["genie"]  # the deployed agent's benchmark run (evals/results/genie_run_*.json)
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
            cells.append(f"<td data-label=\"Run {run[1:]}\">{verdict_chip(item.get(run), mark)}</td>")
        cells.append(f"<td data-label=\"Genie\" class=\"genie\">{verdict_chip(genie['verdicts'][item['id']])}</td>")
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
                              f"<p>{html.escape(f['next'])}</p></div>")

    answer = summary["showcase"]
    sql_html = "".join(f"<pre><code>{html.escape(s)}</code></pre>" for s in answer["queries"])
    case = dict(hourly_co_debit=data["hourly_co_debit"], cascade_b05=data["cascade_b05"], incident=data["incident"])

    run_ids = summary["runs"]
    passed = summary["passed"]
    labels = summary.get("run_labels", {})
    head_html = "".join(f"<th>Run {n}</th>" for n in run_ids) + "<th>Genie</th>"
    score_html = "\n".join(
        f'      <div class="cell" role="listitem"><span class="v">{passed[str(n)]}/{summary["total"]}</span>'
        f'<span class="l">{html.escape(labels.get(str(n), f"run {n}"))}</span></div>' for n in run_ids)
    score_html += (f'\n      <div class="cell genie" role="listitem"><span class="v">{genie["passed"]}/{genie["total"]}</span>'
                   f'<span class="l">on the deployed Genie agent, first run: chat mode {genie["chat"]["passed"]}/'
                   f'{genie["chat"]["total"]}, agent mode {genie["agent"]["passed"]}/{genie["agent"]["total"]}</span></div>')
    earlier = [str(passed[str(n)]) for n in run_ids[:-1]]
    earlier_passed = ", ".join(earlier[:-1]) + (" and " if len(earlier) > 1 else "") + earlier[-1]

    ctx = dict(
        headline=data["headline"], incident=data["incident"],
        pipeline=data["pipeline"],
        eval=dict(total=summary["total"], last_run=run_ids[-1], last_passed=passed[str(run_ids[-1])],
                  earlier_passed=earlier_passed, runs=len(run_ids),
                  v1_judge_passed=summary["v1_judge_passed"],
                  sql_ok=summary["sql_ok"], guardrails=summary["guardrails"],
                  head_html=head_html, score_html=score_html,
                  rows_html="\n".join(rows), notes_html=notes_html, fixes_html="\n".join(fixes),
                  failure_modes=len(summary["fixes"]),
                  genie_passed=genie["passed"], genie_findings_html="\n".join(genie_findings)),
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
