"""Demo spreadsheets used when Google Sheets OAuth isn't configured.

These are SAMPLE catalogs for offline demos/tests — product logic never
assumes a particular company. Opening by URL only works when the spreadsheet
ID exists in this catalog (demo) or via live OAuth (production).
"""

DEMO_PORTFOLIO = {
    "id": "demo_portfolio_fy2024",
    "title": "Atlas Demo Portfolio",
    "sheets": ["Holdings", "Monthly"],
    "values": {
        "Holdings": [
            ["Ticker", "Company", "Sector", "Shares", "Cost Basis", "Price", "Market Value", "P/L %", "Weight %"],
            ["AAPL", "Apple", "Technology", "120", "150.00", "190.00", "22800", "26.7", "22.8"],
            ["MSFT", "Microsoft", "Technology", "80", "280.00", "420.00", "33600", "50.0", "33.6"],
            ["NVDA", "NVIDIA", "Semiconductors", "40", "200.00", "480.00", "19200", "140.0", "19.2"],
            ["JPM", "JPMorgan", "Banking", "60", "140.00", "198.00", "11880", "41.4", "11.9"],
            ["XOM", "Exxon Mobil", "Energy", "90", "95.00", "110.00", "9900", "15.8", "9.9"],
            ["CASH", "Cash", "Cash", "1", "2600", "2600", "2600", "0", "2.6"],
        ],
        "Monthly": [
            ["Month", "Portfolio Value", "Net Flows", "Return %"],
            ["2024-01", "82000", "0", "2.1"],
            ["2024-02", "84500", "1000", "1.8"],
            ["2024-03", "91000", "0", "6.5"],
            ["2024-04", "88000", "-500", "-2.7"],
            ["2024-05", "94500", "0", "7.4"],
            ["2024-06", "100000", "2000", "3.7"],
        ],
    },
}

DEMO_WATCHLIST = {
    "id": "demo_watchlist",
    "title": "AI Watchlist",
    "sheets": ["Watchlist"],
    "values": {
        "Watchlist": [
            ["Ticker", "Company", "Sector", "Thesis", "Target"],
            ["NVDA", "NVIDIA", "Semiconductors", "AI infrastructure leader", "550"],
            ["MSFT", "Microsoft", "Cloud", "Azure + OpenAI leverage", "450"],
            ["GOOGL", "Alphabet", "AI", "Search + Gemini optionality", "200"],
            ["AMD", "AMD", "Semiconductors", "GPU challenger", "180"],
        ],
    },
}

# Demo IDs usable in pasted Google Sheets-style URLs for offline verification
DEMO_MSFT_FINANCIALS = {
    "id": "demo_sample_msft_financials",
    "title": "Sample Co Financials A",
    "sheets": ["Income"],
    "values": {
        "Income": [
            ["Metric", "2023", "2024", "2025"],
            ["Revenue", "211900", "245100", "268000"],
            ["Operating Income", "88500", "109400", "120000"],
            ["Net Income", "72360", "88140", "95000"],
            ["Gross Margin %", "68.5", "69.0", "69.8"],
        ],
    },
}

DEMO_AMZN_FINANCIALS = {
    "id": "demo_sample_amzn_financials",
    "title": "Sample Co Financials B",
    "sheets": ["Results"],
    "values": {
        "Results": [
            ["Metric", "2023", "2024", "2025"],
            ["Revenue", "574785", "637959", "700000"],
            ["Operating Income", "36850", "68590", "78000"],
            ["Net Income", "30425", "50790", "58000"],
            ["Free Cash Flow", "35480", "47890", "52000"],
        ],
    },
}

DEMO_GENERIC_HOLDINGS = {
    "id": "demo_sample_generic_holdings",
    "title": "Personal Holdings Tracker",
    "sheets": ["Positions"],
    "values": {
        "Positions": [
            ["Ticker", "Company", "Sector", "Shares", "Price", "Market Value", "P/L %", "Weight %"],
            ["TSLA", "Tesla", "Auto", "25", "250", "6250", "12.0", "25.0"],
            ["VOO", "Vanguard S&P 500", "ETF", "40", "480", "19200", "8.5", "76.8"],
            ["CASH", "Cash", "Cash", "1", "-500", "-500", "0", "-1.8"],
        ],
    },
}

DEMO_WORKBOOKS = [
    DEMO_PORTFOLIO,
    DEMO_WATCHLIST,
    DEMO_MSFT_FINANCIALS,
    DEMO_AMZN_FINANCIALS,
    DEMO_GENERIC_HOLDINGS,
]
