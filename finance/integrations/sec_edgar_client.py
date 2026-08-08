"""SEC EDGAR public filings client (no API key)."""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from finance.types import FinanceError, FinanceNotFound, FinanceTimeout, SecFiling

logger = logging.getLogger("atlas.finance.sec")

USER_AGENT = "AtlasAIAssistant contact@atlas-ai.local"
TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"


class SecEdgarClient:
    name = "sec"

    def __init__(self, *, timeout: float = 15.0) -> None:
        self.timeout = timeout
        self._ticker_map: dict[str, str] | None = None

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }

    def _get_json(self, url: str) -> Any:
        try:
            with httpx.Client(timeout=self.timeout, headers=self._headers()) as client:
                resp = client.get(url)
                if resp.status_code == 404:
                    raise FinanceNotFound("SEC resource not found")
                resp.raise_for_status()
                return resp.json()
        except FinanceNotFound:
            raise
        except httpx.TimeoutException as exc:
            raise FinanceTimeout("SEC request timed out") from exc
        except Exception as exc:  # noqa: BLE001
            raise FinanceError(f"SEC request failed: {exc}") from exc

    def _cik_for_symbol(self, symbol: str) -> str:
        if self._ticker_map is None:
            raw = self._get_json(TICKER_MAP_URL)
            mapping: dict[str, str] = {}
            if isinstance(raw, dict):
                for item in raw.values():
                    tick = str(item.get("ticker") or "").upper()
                    cik = str(item.get("cik_str") or "").zfill(10)
                    if tick and cik:
                        mapping[tick] = cik
            self._ticker_map = mapping
        cik = (self._ticker_map or {}).get(symbol.upper())
        if not cik:
            raise FinanceNotFound(f"No SEC CIK for {symbol}")
        return cik

    def get_filings(self, symbol: str, *, form: str = "", limit: int = 5) -> list[SecFiling]:
        cik = self._cik_for_symbol(symbol)
        data = self._get_json(SUBMISSIONS_URL.format(cik=cik))
        recent = (data.get("filings") or {}).get("recent") or {}
        forms = recent.get("form") or []
        dates = recent.get("filingDate") or []
        accessions = recent.get("accessionNumber") or []
        primary = recent.get("primaryDocument") or []
        out: list[SecFiling] = []
        for i, f in enumerate(forms):
            if form and form.upper() not in str(f).upper():
                continue
            acc = accessions[i] if i < len(accessions) else ""
            doc = primary[i] if i < len(primary) else ""
            acc_nodash = re.sub(r"[^0-9]", "", acc)
            url = ""
            if acc_nodash and doc:
                url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_nodash}/{doc}"
            out.append(
                SecFiling(
                    symbol=symbol.upper(),
                    form=str(f),
                    filed_at=str(dates[i] if i < len(dates) else ""),
                    description=str(f),
                    url=url,
                    accession=str(acc),
                )
            )
            if len(out) >= limit:
                break
        return out
