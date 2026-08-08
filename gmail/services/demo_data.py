"""Demo inbox used when Google Gmail OAuth isn't configured."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

NOW = datetime.now(tz=timezone.utc)


def _ts(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).isoformat()


DEMO_MESSAGES = [
    {
        "id": "demo_msg_msft_earnings",
        "thread_id": "demo_thread_msft",
        "subject": "Microsoft Q2 investor update — cloud growth & AI",
        "from_name": "Microsoft Investor Relations",
        "from_email": "investor@microsoft.com",
        "snippet": "Azure revenue up 29% YoY. OpenAI partnership deepening. Reply requested by Friday.",
        "body": (
            "Hi team,\n\nQuick Q2 note: Azure grew 29% YoY with strong AI attach. "
            "We'd like your feedback on the capital allocation slide before Friday's call. "
            "Meeting proposed: Thursday 3pm ET.\n\nBest,\nMSFT IR"
        ),
        "received_at": _ts(2),
        "unread": True,
        "labels": ["INBOX", "IMPORTANT"],
        "companies": ["Microsoft"],
        "tickers": ["MSFT"],
        "people": ["Microsoft Investor Relations"],
        "categories": ["investor_update", "finance", "meeting", "reply_request"],
        "attachments": [
            {
                "filename": "MSFT_Q2_Capital_Allocation.pdf",
                "mime_type": "application/pdf",
                "size": 4200,
                "demo_text": (
                    "Microsoft Q2 Capital Allocation\n\n"
                    "Cloud & AI: 48% of incremental spend.\n"
                    "Buybacks: $8.2B this quarter.\n"
                    "Risks: capex intensity and competition in inference.\n"
                ),
            }
        ],
    },
    {
        "id": "demo_msg_nvda_reply",
        "thread_id": "demo_thread_nvda",
        "subject": "Re: NVIDIA partnership follow-up",
        "from_name": "Priya Chen",
        "from_email": "priya.chen@nvidia.com",
        "snippet": "Happy to share the revised GPU allocation numbers. Can you confirm Thursday?",
        "body": (
            "Thanks for the note. We're comfortable with the revised allocation. "
            "Please confirm Thursday's working session and we'll send the deck."
        ),
        "received_at": _ts(5),
        "unread": True,
        "labels": ["INBOX"],
        "companies": ["NVIDIA"],
        "tickers": ["NVDA"],
        "people": ["Priya Chen"],
        "categories": ["reply_request", "company_announcement", "follow_up"],
        "attachments": [],
    },
    {
        "id": "demo_msg_amazon_invoice",
        "thread_id": "demo_thread_amzn_inv",
        "subject": "Invoice #A-10482 from Amazon Web Services",
        "from_name": "AWS Billing",
        "from_email": "no-reply@amazon.com",
        "snippet": "Your August invoice of $4,280 is ready. Payment due in 14 days.",
        "body": "Invoice A-10482 for $4,280. Due in 14 days. Download the PDF for records.",
        "received_at": _ts(8),
        "unread": True,
        "labels": ["INBOX"],
        "companies": ["Amazon"],
        "tickers": ["AMZN"],
        "people": ["AWS Billing"],
        "categories": ["invoice", "finance", "deadline"],
        "attachments": [
            {
                "filename": "AWS_Invoice_A10482.pdf",
                "mime_type": "application/pdf",
                "size": 1800,
                "demo_text": (
                    "Amazon Web Services Invoice\nInvoice: A-10482\nAmount due: $4,280\n"
                    "Due date: 14 days\nServices: compute + storage\n"
                ),
            }
        ],
    },
    {
        "id": "demo_msg_meeting",
        "thread_id": "demo_thread_meet",
        "subject": "Invitation: Portfolio review — Friday 10:00",
        "from_name": "Alex Rivera",
        "from_email": "alex.rivera@atlasdemo.com",
        "snippet": "Please join Friday's portfolio review. Agenda attached.",
        "body": (
            "Hi — calendar invite for Friday 10:00 AM. "
            "We'll cover allocation drift and earnings exposure. RSVP appreciated."
        ),
        "received_at": _ts(12),
        "unread": False,
        "labels": ["INBOX"],
        "companies": [],
        "tickers": [],
        "people": ["Alex Rivera"],
        "categories": ["meeting", "action_item"],
        "attachments": [],
    },
    {
        "id": "demo_msg_resume",
        "thread_id": "demo_thread_resume",
        "subject": "Application — Equity Research Analyst",
        "from_name": "Jordan Lee",
        "from_email": "jordan.lee@email.com",
        "snippet": "Attached resume for the equity research role.",
        "body": "Please find my resume attached for the Equity Research Analyst opening.",
        "received_at": _ts(26),
        "unread": True,
        "labels": ["INBOX"],
        "companies": [],
        "tickers": [],
        "people": ["Jordan Lee"],
        "categories": ["resume"],
        "attachments": [
            {
                "filename": "Jordan_Lee_Resume.pdf",
                "mime_type": "application/pdf",
                "size": 2200,
                "demo_text": (
                    "Jordan Lee — Equity Research Analyst\n"
                    "Experience: 4 years covering tech / semiconductors.\n"
                    "Skills: financial modeling, earnings analysis.\n"
                ),
            }
        ],
    },
    {
        "id": "demo_msg_earnings_report",
        "thread_id": "demo_thread_fin_rpt",
        "subject": "Forwarded: Apple FY2024 financial highlights",
        "from_name": "Sam Okonkwo",
        "from_email": "sam@atlasdemo.com",
        "snippet": "Sharing Apple's FY highlights PDF — services mix still expanding.",
        "body": "Thought you'd want this for the watchlist review. Attachment has the summary.",
        "received_at": _ts(30),
        "unread": False,
        "labels": ["INBOX"],
        "companies": ["Apple"],
        "tickers": ["AAPL"],
        "people": ["Sam Okonkwo"],
        "categories": ["financial_report", "finance"],
        "attachments": [
            {
                "filename": "AAPL_FY2024_Highlights.txt",
                "mime_type": "text/plain",
                "size": 900,
                "demo_text": (
                    "Apple FY2024 Highlights\nRevenue: $383B\nServices growth: +13%\n"
                    "Gross margin: 46%\nKey risk: China demand softness.\n"
                ),
            }
        ],
    },
    {
        "id": "demo_msg_urgent_board",
        "thread_id": "demo_thread_board",
        "subject": "URGENT: Board pack — action items before Monday",
        "from_name": "Board Office",
        "from_email": "board@atlasdemo.com",
        "snippet": "Please review risk section and reply with comments by Sunday 6pm.",
        "body": (
            "Urgent: board pack attached conceptually. Comment on liquidity risk "
            "and concentration before Sunday 6pm. This needs your attention."
        ),
        "received_at": _ts(1),
        "unread": True,
        "labels": ["INBOX", "IMPORTANT"],
        "companies": [],
        "tickers": [],
        "people": ["Board Office"],
        "categories": ["urgent", "deadline", "reply_request", "action_item"],
        "attachments": [],
    },
]
