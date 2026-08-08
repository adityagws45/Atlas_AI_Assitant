# Atlas Demo Script (5–7 minutes)

Judge-facing walkthrough. Keep Telegram open; have `run_bot` + `runserver` running if showing live OAuth. Demo Google connectors work without consent.

---

## Pitch (30–45s)

> Investors live across quotes, filings, Sheets, email, and calendar. Atlas is one Telegram analyst that carries context across all of them — not five mini-apps glued together.

---

## Sequence

### 1. Onboarding → Finance (~90s)

| You say | Show |
|---------|------|
| `/start` | Short intro; role ask |
| `I'm an investor` | Focused follow-up |
| `Semiconductors and AI` | Watchlist / sector awareness |
| `Tell me about Nvidia` | Clarifies or researches |
| `What's driving the stock?` | News + **why it matters** |

**Callout:** Memory + analyst voice, not a chatbot dump.

### 2. Documents / Drive (~60s)

| You say | Show |
|---------|------|
| Upload `demo/documents/apple_annual_report_fy2024.md` *or* `Analyze my Apple annual report` (Drive demo) | Ingest / open |
| `What are the biggest risks?` | Cited risks |
| Upload Microsoft *or* compare | `Compare Apple and Microsoft` |

**Callout:** Same assistant; document pipeline reused for Drive + email attachments.

### 3. Sheets portfolio (~60s)

| You say | Show |
|---------|------|
| `Show my spreadsheets` | Demo portfolio + watchlist |
| `Open my portfolio` | Value + allocation |
| `Which holdings concern you?` | Focused risk read |
| `What about Microsoft?` | Follow-up without re-naming the sheet |

### 4. Gmail (~60s)

| You say | Show |
|---------|------|
| `Check my email` / `What needs my attention?` | Priority digest |
| `Find emails about Microsoft` | Thread summary |
| `Summarize the attachment` | Document pipeline |
| `Draft a reply` → `Rewrite politely` | Confirm-gated draft |

### 5. Calendar (~45s)

| You say | Show |
|---------|------|
| `What does my day look like?` | Schedule + conflict callout |
| `Schedule time to review my portfolio tomorrow at 4 PM` → `YES` | Confirm then book |
| `Any conflicts?` | Clear overlap explanation |

### 6. Cross-tool closer (~45s)

| You say | Show |
|---------|------|
| `What did Microsoft say?` | Email memory |
| `Remind me before Nvidia reports` → `YES` | Finance + calendar |
| `What should I watch tomorrow?` | Continuous analyst wrap |

**Close:** One brain — research → documents → portfolio → inbox → schedule.

---

## If something flakes

- No Google consent → demo catalogs still run (Drive/Sheets/Gmail/Calendar).
- Gemini slow → deterministic Sheets/Gmail/Calendar paths still demo well.
- Prefer demo sheets + demo inbox if live APIs are cold.

## Do not

- Paste OAuth URLs into slides with tokens.
- Open admin / logs with secrets.
- Live-send email (confirm path stops at draft under read-only).
