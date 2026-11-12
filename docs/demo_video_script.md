# Demo video script (about 3 minutes)

Goal: in about three minutes, a recruiter or hiring manager understands the problem and sees the copilot find
the cause. The last part shows how it was tested. Record in English (Spanish subtitles are optional), at 1080p, with the browser at 110%
zoom, notifications off and no personal tabs or email on screen.

Before recording, open four tabs: the dashboard on page 1, the Genie agent, the pipeline graph, and the eval
results table (README or project page). Ask each Genie question in a new conversation in Agent mode, with the
same wording as in the evaluation.

| Time | Screen | Voice-over |
|---|---|---|
| 0:00–0:12 | Dashboard, page 1. Point at the dip in the "CO debit" line | "When card approval rates drop, every hour costs approved sales, and finding out why means going through four data sources. As a personal project, I built a copilot that does it from a single question." |
| 0:12–0:30 | Pipeline graph of `approval_copilot` with the bronze, reference and silver layers (dbt builds gold in the next task of the job); open the expectations of `silver_auth_attempts` | "The data is synthetic: a fictional payment orchestrator in Mexico, Colombia and Brazil, with three acquirers, four file formats and six hundred sixty-one thousand payments. A Databricks pipeline maps each acquirer's decline codes to a common set, using a versioned map, and moves the rows that fail quality checks to a quarantine table." |
| 0:30–0:50 | Dashboard: the abnormal-days table (13 Aug, CO debit), then the hourly chart on page 2, filtered to CO debit, 12–15 Aug | "The dashboard flags one abnormal day: Colombian debit on August thirteenth. The hourly view shows approval falling at two in the afternoon, when the traffic moves to Acquirer B." |
| 0:50–1:40 | Genie: "Why did the approval rate for debit cards in Colombia drop on 13 August 2026?" Let it run, scroll the answer and open "Show code" once | "I ask the copilot why. It has five SQL functions for this: the size of the drop, which segments moved, which decline codes grew, whether retries still worked, and what changed in production. It finds that a routing change sent all Colombian debit to Acquirer B to save forty basis points. B approved less, and a decline-map change a week earlier had stopped its 'do not honor' declines from being retried on another acquirer. In twenty-two hours, that cost about eleven points of approval, a hundred and ten approvals and around six thousand four hundred dollars in approved sales. I can open the query behind every number." |
| 1:40–2:00 | Genie, new conversation: "Was there any early warning before the Colombia debit incident of 13 August 2026?" | "There was an early warning. Retries for B's 'do not honor' declines collapsed on August sixth, a week before the incident, and the retry alert flagged them every day from then on. In the scenario, the alert had no owner, so nobody acted on it." |
| 2:00–2:12 | Genie, new conversation: "Which real bank is issuer CO-ISS-03?" | "When I ask which real bank an issuer code belongs to, it says the issuers are fictional codes and doesn't guess. It refuses to delete data, and asked straight out for a forecast, it declines." |
| 2:12–2:42 | Eval results table | "I tested it on twenty questions with scoring rules. The Genie agent passed thirteen at first. Later releases scored between fourteen and nineteen, and the last one, nineteen. On six sets of new questions, committed to the repo before each run and asked three times each, it got twenty-six to thirty-five answers right out of thirty-nine, and thirty-one on the last two. Every answer and every failure is published." |
| 2:42–3:05 | Project page or README top | "I built it with an AI coding assistant: I wrote the brief and reviewed the work against it. The same five functions also work from other AI assistants, through an MCP server. A guardrail on every production change would have flagged the routing change two hours after it went live. The PRD, the postmortem and the code are linked below." |

## 60 to 90 second cut

A shorter version for the LinkedIn feed, cut from the same recordings, so it needs no extra takes. It keeps the
problem, the diagnosis, the early warning and the evaluation, and drops the pipeline and the guardrails.

| Time | Screen | Voice-over (sentences from the full script) |
|---|---|---|
| 0:00–0:14 | Dashboard, page 1, the dip in the "CO debit" line | The whole 0:00 line: "When card approval rates drop, every hour costs approved sales, and finding out why means going through four data sources. As a personal project, I built a copilot that does it from a single question." |
| 0:14–0:42 | Genie, the main question: the answer scrolling, then "Show code" | From the 0:50 line, the three sentences that start with "It finds that a routing change...", "B approved less..." and "In twenty-two hours..." |
| 0:42–0:54 | Genie, the early-warning question | From the 1:40 line: "Retries for B's 'do not honor' declines collapsed on August sixth, a week before the incident, and the retry alert flagged them every day from then on." |
| 0:54–1:22 | Eval results table | The 2:12 line without its last sentence: from "I tested it on twenty questions..." to "...and thirty-one on the last two." |
| 1:22–1:26 | Project page or README top | The last sentence of the 2:42 line: "The PRD, the postmortem and the code are linked below." |

The length depends on the pace of the recording, about 85 to 90 seconds at a normal speaking pace. If it runs
over 90 seconds, the first cut is the 0:42 segment.

## Recording tips

- Run each Genie question once before recording so the warehouse is warm, then start a new conversation for the
  take.
- Ask the three questions in Agent mode, the mode the evaluation graded them in. You pick the mode when you start
  a conversation, and you can't change it inside that conversation.
- The main question passed two of three samples in the last benchmark run. The third recommended a map fix that the
  hotfix had already made, and earlier runs failed when an answer read figures from another window as the
  incident's, so read the whole answer before you keep a take. It should size the window from 14:00 on 13
  August to the rollback at 12:10 the next day (about −10.9 points, 110 approvals and USD 6,354), treat the 'do not
  honor' declines as acquirer-side, recommend no map fix that the hotfix of 15 August already made, and state
  nothing that its own query results contradict. If it doesn't, start a new conversation and ask again. A retake is
  fine because the pass rate is published. Never edit an answer.
- The early-warning question also passed two of three samples in the last benchmark run; the one that failed explained
  the warning with retries of retries, which its own results contradict. Keep a take only if it dates the retry alert
  from 6 August, ties it to the decline-map change of that day and says that B's 'do not honor' declines stopped being
  retried.
- Read Genie's answer before recording the voice-over. If its wording or numbers differ from the script, say what
  Genie showed, or keep the line general ("it finds the routing change..."). Never read a number it did not show.
- Cut the waiting time in editing, and keep the answer on screen long enough to read (3–4 seconds per paragraph).
- Export as MP4. Upload the video file to LinkedIn, so it plays in the feed, and add captions.
