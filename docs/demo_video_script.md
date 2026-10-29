# Demo video script (2:30–3:00)

Goal: a recruiter or hiring manager understands the problem, sees the copilot solve it, and sees that it was
tested, in under three minutes. Record in English (add Spanish subtitles if you want), 1080p, browser zoom
110%, notifications off, no personal tabs or emails on screen.

Prepare before recording: dashboard open on page 1; the Genie agent open in a second tab; the pipeline graph open in a
third tab; the eval results table (README or project page) in a fourth. Ask each Genie question in a new conversation in
Agent mode, worded exactly as in the evaluation.

| Time | Screen | Voice-over |
|---|---|---|
| 0:00–0:12 | Dashboard, page 1. Point at the dip in the "CO debit" line | "When card approval drops, every hour of it costs approved sales, and explaining why means digging through four data sources. This is a personal project: a copilot that answers in one question." |
| 0:12–0:30 | Pipeline graph (bronze → silver → gold), hover over the expectations on `silver_auth_attempts` | "Everything here is synthetic: a fictional payment orchestrator in Mexico, Colombia and Brazil, three acquirers, four file formats, six hundred sixty-one thousand payments. A Databricks pipeline cleans it, normalizes three different decline-code dialects with a versioned map, and quarantines bad rows instead of hiding them." |
| 0:30–0:50 | Dashboard: anomaly table (13 Aug, CO debit), then page 2 hourly chart filtered to CO, 12–15 Aug | "In this synthetic scenario, the dashboard flags one abnormal day: Colombian debit on August thirteenth. Hour by hour, approval falls at two in the afternoon, exactly when traffic switches to Acquirer B." |
| 0:50–1:40 | Genie: type "Why did the approval rate for debit cards in Colombia drop on 13 August 2026?". Let it run; scroll the answer; open "Show code" once | "I ask the copilot why. It uses five SQL functions that hold the diagnostic method: the size of the drop, who moved, which decline codes grew, whether retries still worked, and what changed. The answer: a routing change sent all Colombian debit to Acquirer B to save forty basis points. B declined more, and a decline-code map released a week earlier had stopped B's 'do not honor' declines from being retried elsewhere. The impact: about eleven points, a hundred and ten approvals, and about six thousand four hundred dollars of approved sales, in twenty-two hours. Every number comes from a query I can open." |
| 1:40–2:00 | Genie, new conversation: "Was there any early warning before the Colombia debit incident of 13 August 2026?" | "Could we have caught it earlier? Yes. Retries for B's 'do not honor' declines collapsed on August sixth, and the retry alert flags it in the next morning's run, six days before the visible drop. In the scenario, nobody owned that alert, which is a lesson in itself." |
| 2:00–2:12 | Genie, new conversation: "Which real bank is issuer CO-ISS-03?" | "And it knows its limits: the issuers are fictional codes, so it refuses to guess. It won't forecast or delete data either." |
| 2:12–2:35 | Eval results table | "Before trusting it, I tested it: twenty questions, each with a scoring rule. The deployed Genie agent started at thirteen out of twenty. Moving the method from instructions into functions and examples took it to seventeen. Then I ran fourteen new questions it had never seen, written before the run: eleven passed. Every failure, and what I changed, is in the repo." |
| 2:35–2:55 | Project page or README top | "I built it with an AI coding assistant: I set the brief and the review bar. Next would be hourly alerts, and a guardrail that judges routing changes on approved volume, not fees. The PRD, the postmortem and all the code are linked below." |

## Recording tips

- Run each Genie question once before recording so the warehouse is warm; then start a new conversation for the take.
- Ask the three questions in Agent mode, the mode the evaluation graded them in. The mode is chosen when a conversation starts and
  cannot be changed inside it.
- The main question passed two of three samples in the last benchmark run, so check the answer before you keep a take: it should
  size the window from 14:00 on 13 August to the rollback at 12:10 the next day (about −10.9 points, 110 approvals and USD 6,354)
  and treat the 'do not honor' declines as acquirer-side. If it does not, start a new conversation and ask again. A retake is
  fine because the pass rate is published; never edit an answer.
- Read Genie's answer before recording the voice-over. If its wording or numbers differ from the script, say what Genie showed, or keep the line generic ("it finds the routing change…"); never read a number it did not show.
- Cut waiting time in editing; keep the answer on screen long enough to read (3–4 seconds per paragraph).
- Export as MP4. For LinkedIn, upload the video natively (not as a link) and add captions.
