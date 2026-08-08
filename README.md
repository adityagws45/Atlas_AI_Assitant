# Atlas AI Financial Assistant

Telegram-native financial analyst that researches markets, reads your documents and portfolios, and helps prioritize email and schedule — as **one** continuous assistant.

Built for the Atlas AI Hackathon.

## Why Atlas

Investors bounce between quote sites, Drive folders, Sheets, Gmail, and Calendar. Atlas keeps that context in one Telegram conversation: research a name, open the filing, check the portfolio sheet, draft a reply, and block time to prepare — without switching “modes.”

## Features

- **Finance** — quotes, research, news clusters, compare, earnings, SEC, watchlist (Finnhub → Yahoo fallback)
- **Documents** — PDF / TXT / DOCX / Markdown Q&A with citations and compare
- **Google Drive** — search & import files into the same document pipeline
- **Google Sheets** — portfolio / watchlist intelligence (allocation, performers, risks)
- **Gmail** — inbox prioritization, search, attachment summary, draft replies (confirm before send)
- **Calendar** — day view, free slots, conflicts, schedule / reschedule (confirm before mutate)
- **Memory** — preferences, watchlist, active docs/sheets/threads/events across turns

## Architecture

```
Telegram
   ↓
ConversationProcessor          ← single entry; intent priority
   ↓
ContextBuilder → AIService → Gemini
   ↓                ↓
AssistantMemory   ToolRouter
                     ↓
        finance | documents | drive | sheets | gmail | calendar
```

Intent priority (onboarded users): **Sheets → Calendar → Gmail → Drive → AI/finance/docs**.

One orchestrator. Integrations enrich Atlas; they are not separate apps.

## Stack

- Python 3.11+ / Django 5 / DRF  
- PostgreSQL + Redis  
- python-telegram-bot  
- Google Gemini  
- Finnhub + Yahoo Finance  
- Google OAuth (Drive, Sheets, Gmail, Calendar)

## Quick start

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements/dev.txt

docker compose up -d
# Postgres on host :5433 — use that in DATABASE_URL

cp .env.example .env
# Set TELEGRAM_BOT_TOKEN, GEMINI_API_KEY, FINNHUB_API_KEY,
# FIELD_ENCRYPTION_KEY, and optional GOOGLE_CLIENT_ID/SECRET

python manage.py migrate
python manage.py runserver          # OAuth callback on :8000
python manage.py run_bot            # Telegram polling
```

Webhook (production): `POST /api/telegram/webhook/`

## Verification

```bash
python scripts/verify_milestone2.py   # … through …
python scripts/verify_milestone9.py
python scripts/verify_final_polish.py
```

## Demo

See [DEMO_SCRIPT.md](DEMO_SCRIPT.md) for a 5–7 minute judge-ready walkthrough.

Sample filings live under `demo/documents/`.

## Apps

| App | Role |
|-----|------|
| `telegram_bot/` | Transport only |
| `conversation/` | Orchestrator, onboarding, formatter |
| `ai/` | Prompts + AIService (not a Django app) |
| `tools/` | ToolRouter + executors |
| `memory/` | Preferences, watchlist, AssistantMemory |
| `finance/` | Market data + research |
| `documents/` | Pipeline, embeddings, Q&A |
| `drive/` `sheets/` `gmail/` `gcalendar/` | Google boundaries |
| `accounts/` | Users + encrypted Google tokens |

## Security notes

- OAuth tokens encrypted at rest (`FIELD_ENCRYPTION_KEY`)
- Least-privilege Google scopes (Sheets/Drive/Gmail read-focused; Calendar events)
- Destructive email/calendar actions require explicit confirmation
- Replies never expose message/sheet/event IDs or provider jargon

## Milestones

| # | Status | Focus |
|---|--------|--------|
| 1–2 | ✅ | Scaffold + Telegram onboarding |
| 3–4 | ✅ | Gemini orchestration + finance tools |
| 5 | ✅ | Document intelligence |
| 6 | ✅ | Google Drive |
| 7 | ✅ | Google Sheets |
| 8 | ✅ | Gmail |
| 9 | ✅ | Calendar |
| Polish | ✅ | Product-wide quality pass |

## Settings modules

```
DJANGO_SETTINGS_MODULE=config.settings.development
DJANGO_SETTINGS_MODULE=config.settings.production
DJANGO_SETTINGS_MODULE=config.settings.test
```

## Pitch (one line)

**Atlas is the financial analyst that already knows your filings, portfolio sheet, inbox, and calendar — in Telegram.**
