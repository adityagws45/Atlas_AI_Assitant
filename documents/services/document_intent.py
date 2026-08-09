"""Detect document-analysis intents for fast routing."""

from __future__ import annotations

import re

DOC_QUESTION = re.compile(
    r"\b("
    r"summarize|summary|biggest risks?|risk factors?|revenue|net income|profit|"
    r"profitability|margin|cash flow|income statement|balance sheet|"
    r"debt|guidance|management|md&a|business model|segment|competition|"
    r"capital allocation|ai strategy|artificial intelligence|what changed|"
    r"compared to|compare .+ report|compare .+ annual|filing|10[\s\-]?k|"
    r"10[\s\-]?q|transcript|what did (they|management) say|pay attention|"
    r"financial report|this report|the report|uploaded (report|document|pdf|file)|"
    r"from the (report|filing|document|deck)|in the (report|filing)|"
    r"according to the (report|filing)|year[- ]over[- ]year|yoy|"
    r"long[- ]term ai investor|as an? (long[- ]term )?(ai )?investor|"
    r"which section|section discusses|"
    r"what (is|does) (this|the|my) (document|pdf|file|report|filing)|"
    r"(this|the) (document|pdf|file) (about|say|cover)|"
    r"tell me about (this|the) (document|pdf|file|report)"
    r")\b",
    re.IGNORECASE,
)


COMPARE_HINT = re.compile(
    r"\b(compare|versus|vs\.?|difference|what changed|last year|prior year|"
    r"year[- ]over[- ]year|two reports|both (reports|filings))\b",
    re.IGNORECASE,
)


def is_document_question(text: str) -> bool:
    return bool(DOC_QUESTION.search(text or ""))


def is_document_compare(text: str) -> bool:
    return bool(COMPARE_HINT.search(text or "")) and is_document_question(text or "")
