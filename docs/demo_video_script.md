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
| 0:12–0:30 | Pipeline graph with the bronze, silver and gold layers; open the expectations of `silver_auth_attempts` | "The data is synthetic: a fictional payment orchestrator in Mexico, Colombia and Brazil, with three acquirers, four file formats and six hundred sixty-one thousand payments. A Databricks pipeline maps each acquirer's decline codes to a common set, using a versioned map, and moves the rows that fail quality checks to a quarantine table." |
| 0:30–0:50 | Dashboard: the abnormal-days table (13 Aug, CO debit), then the hourly chart on page 2, filtered to CO debit, 12–15 Aug | "The dashboard flags one abnormal day: Colombian debit on August thirteenth. The hourly view shows approval falling at two in the afternoon, when the traffic moves to Acquirer B." |
| 0:50–1:40 | Genie: "Why did the approval rate for debit cards in Colombia drop on 13 August 2026?" Let it run, scroll the answer and open "Show code" once | "I ask the copilot why. It has five SQL functions for this: the size of the drop, which segments moved, which decline codes grew, whether retries still worked, and what changed in production. It finds that a routing change sent all Colombian debit to Acquirer B to save forty basis points. B approved less, and a decline-code map released a week earlier had stopped B's 'do not honor' declines from being retried on another acquirer. In twenty-two hours, that cost about eleven points of approval, a hundred and ten approvals and around six thousand four hundred dollars in approved sales. I can open the query behind every number." |
| 1:40–2:00 | Genie, new conversation: "Was there any early warning before the Colombia debit incident of 13 August 2026?" | "There was an early warning. Retries for B's 'do not honor' declines collapsed on August sixth, and the retry alert flagged it in the next morning's run, six days before the drop showed up in daily approval. In the scenario, the alert had no owner, so nobody acted on it." |
| 2:00–2:12 | Genie, new conversation: "Which real bank is issuer CO-ISS-03?" | "When I ask which real bank an issuer code belongs to, it says the issuers are fictional codes and doesn't guess. It also refuses to forecast or to delete data." |
| 2:12–2:35 | Eval results table | "I tested it on twenty questions, each with a scoring rule. The deployed Genie agent passed thirteen on the first run, and seventeen after I moved the method from text instructions into functions and example queries. Then I ran fourteen new questions it had never seen, committed to the repo before the run. It passed eleven. All the answers and failures are published in the repo." |
| 2:35–2:55 | Project page or README top | "I built it with an AI coding assistant: I wrote the brief and reviewed the work against it. Next, I'd add hourly alerts and a guardrail that checks routing changes against approved volume instead of fee savings. The PRD, the postmortem and the code are linked below." |

## Recording tips

- Run each Genie question once before recording so the warehouse is warm, then start a new conversation for the
  take.
- Ask the three questions in Agent mode, the mode the evaluation graded them in. You pick the mode when you start
  a conversation, and you can't change it inside that conversation.
- The main question passed two of three samples in the last benchmark run, so check the answer before you keep a
  take. It should size the window from 14:00 on 13 August to the rollback at 12:10 the next day (about −10.9
  points, 110 approvals and USD 6,354) and treat the 'do not honor' declines as acquirer-side. If it doesn't,
  start a new conversation and ask again. A retake is fine because the pass rate is published. Never edit an
  answer.
- Read Genie's answer before recording the voice-over. If its wording or numbers differ from the script, say what
  Genie showed, or keep the line general ("it finds the routing change..."). Never read a number it did not show.
- Cut the waiting time in editing, and keep the answer on screen long enough to read (3–4 seconds per paragraph).
- Export as MP4. Upload the video file to LinkedIn, so it plays in the feed, and add captions.
