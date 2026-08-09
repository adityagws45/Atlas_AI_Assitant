"""Google OAuth — connect Drive (and future Google services) securely."""

from __future__ import annotations

import logging
import os
import re
import secrets
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from accounts.models import GoogleIntegration, GoogleService, User
from core.crypto import decrypt_text, encrypt_text

logger = logging.getLogger("atlas.accounts.oauth")

STATE_TTL = 600
DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]

# One Connect Google = Calendar + Gmail + Drive + Sheets. Never re-prompt per surface.
UNIFIED_GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]

# Canonical Google API scopes — never allow truncated hosts like https://www..com/...
_REQUIRED_SCOPE_HOST = "https://www.googleapis.com/auth/"
_SHEETS_MIN_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
]
_CALENDAR_MIN_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
]
_SERVICE_REQUIRED_SCOPE_ANY = {
    GoogleService.SHEETS: {
        "https://www.googleapis.com/auth/spreadsheets.readonly",
    },
    GoogleService.CALENDAR: {
        "https://www.googleapis.com/auth/calendar.readonly",
        "https://www.googleapis.com/auth/calendar.events",
        "https://www.googleapis.com/auth/calendar",
    },
    GoogleService.DRIVE: {
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/drive",
    },
    GoogleService.GMAIL: {
        "https://www.googleapis.com/auth/gmail.readonly",
    },
}
_ALL_ATLAS_SERVICES = (
    GoogleService.GMAIL,
    GoogleService.CALENDAR,
    GoogleService.DRIVE,
    GoogleService.SHEETS,
)


def normalize_oauth_scopes(scopes: list[str] | None) -> list[str]:
    """Deduplicate and validate Google OAuth scopes."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in scopes or []:
        s = (raw or "").strip()
        if not s:
            continue
        # Repair accidental stripping of "googleapis" (legacy formatter bug)
        if "www..com/auth/" in s:
            s = s.replace("www..com/auth/", "www.googleapis.com/auth/")
        if s.startswith("https://www.") and "googleapis.com" not in s and "/auth/" in s:
            # e.g. https://www.com/auth/... — reject
            raise ValueError(f"malformed_oauth_scope:{s}")
        if s.startswith("http") and _REQUIRED_SCOPE_HOST not in s and s not in {"openid"}:
            if "accounts.google.com" not in s:
                raise ValueError(f"unexpected_oauth_scope:{s}")
        if s not in seen:
            seen.add(s)
            out.append(s)
    if "openid" not in seen:
        out.append("openid")
    if "https://www.googleapis.com/auth/userinfo.email" not in seen:
        out.append("https://www.googleapis.com/auth/userinfo.email")
    return out


class GoogleOAuthService:
    """Authorization-code flow with encrypted per-user token persistence."""

    def is_configured(self) -> bool:
        return bool(
            (getattr(settings, "GOOGLE_CLIENT_ID", "") or "").strip()
            and (getattr(settings, "GOOGLE_CLIENT_SECRET", "") or "").strip()
            and (getattr(settings, "GOOGLE_REDIRECT_URI", "") or "").strip()
        )

    def redirect_uri(self) -> str:
        return (getattr(settings, "GOOGLE_REDIRECT_URI", "") or "").strip()

    def start_auth(
        self,
        user: User,
        *,
        service: str = GoogleService.DRIVE,
        pending_spreadsheet_id: str | None = None,
        pending_action: str | None = None,
    ) -> dict[str, Any]:
        if not self.is_configured():
            return {
                "ok": False,
                "error_code": "oauth_not_configured",
                "error": (
                    "Google isn't connected on this server yet. "
                    "Set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, and "
                    "GOOGLE_REDIRECT_URI (or PUBLIC_BASE_URL) on the deployed backend."
                ),
            }
        state = secrets.token_urlsafe(24)
        # Always request the full Atlas Google bundle — connecting Calendar must also
        # unlock Gmail / Drive / Sheets so the bot never asks again per feature.
        try:
            scopes = normalize_oauth_scopes(list(UNIFIED_GOOGLE_SCOPES))
        except ValueError as exc:
            logger.error("event=oauth_scope_invalid err=%s", exc)
            return {
                "ok": False,
                "error_code": "oauth_scope_invalid",
                "error": "Google authorization scopes are misconfigured on the server.",
            }
        for required in _SHEETS_MIN_SCOPES + _CALENDAR_MIN_SCOPES:
            if required not in scopes:
                scopes.append(required)
        gmail_scope = "https://www.googleapis.com/auth/gmail.readonly"
        if gmail_scope not in scopes:
            scopes.append(gmail_scope)
        events_scope = "https://www.googleapis.com/auth/calendar.events"
        if events_scope not in scopes:
            scopes.append(events_scope)

        try:
            flow = self._build_flow(scopes=scopes, state=state)
            # prompt=consent ensures refresh token + every Atlas scope is shown once.
            auth_url, returned_state = flow.authorization_url(
                access_type="offline",
                prompt="consent",
                include_granted_scopes="true",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("event=oauth_url_build_failed err=%s", type(exc).__name__)
            return {
                "ok": False,
                "error_code": "oauth_url_failed",
                "error": "I couldn't start Google authorization. Please try again.",
            }

        if returned_state and returned_state != state:
            state = returned_state

        cache.set(
            f"oauth:state:{state}",
            {
                "user_id": str(user.id),
                "service": service,
                "telegram_id": user.telegram_id,
                "code_verifier": flow.code_verifier or "",
                "scopes": scopes,
                "pending_spreadsheet_id": (pending_spreadsheet_id or "").strip(),
                "pending_action": (pending_action or "").strip(),
            },
            STATE_TTL,
        )

        ok, detail = self._validate_auth_url(auth_url)
        logger.info(
            "event=oauth_start telegram_id=%s service=%s auth_url_ok=%s detail=%s redirect=%s",
            user.telegram_id,
            service,
            ok,
            detail,
            self.redirect_uri()[:80],
        )
        if not ok:
            return {
                "ok": False,
                "error_code": "oauth_url_invalid",
                "error": f"Generated authorization URL failed validation ({detail}).",
            }
        return {
            "ok": True,
            "auth_url": auth_url,
            "state": state,
            "service": service,
            "redirect_uri": self.redirect_uri(),
        }

    def handle_callback(self, *, code: str, state: str) -> dict[str, Any]:
        payload = cache.get(f"oauth:state:{state}")
        cache.delete(f"oauth:state:{state}")
        if not payload:
            return {"ok": False, "error_code": "invalid_state", "error": "This link expired. Try connecting again."}
        user = User.objects.filter(id=payload["user_id"]).first()
        if not user:
            return {"ok": False, "error_code": "user_missing", "error": "User not found."}
        service = payload.get("service") or GoogleService.DRIVE
        try:
            scopes = normalize_oauth_scopes(list(payload.get("scopes") or []))
        except ValueError:
            scopes = normalize_oauth_scopes(
                list(getattr(settings, "GOOGLE_OAUTH_SCOPES", {}).get(service, DRIVE_SCOPES))
            )
        code_verifier = payload.get("code_verifier") or None
        try:
            tokens = self._exchange_code(
                code, scopes=scopes, state=state, code_verifier=code_verifier
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("event=oauth_exchange_failed err=%s", type(exc).__name__)
            return {
                "ok": False,
                "error_code": "exchange_failed",
                "error": "I couldn't finish Google authorization. Please try again.",
            }

        # Trust Google's actual granted scopes (tokeninfo), never the requested list alone.
        actual_scopes = self._resolve_actual_scopes(tokens)
        tokens["scope"] = " ".join(actual_scopes)
        missing = self._missing_required_scopes(service, actual_scopes)
        if missing:
            logger.warning(
                "event=oauth_scopes_insufficient telegram_id=%s service=%s actual=%s missing=%s",
                user.telegram_id,
                service,
                actual_scopes,
                missing,
            )
            # Do not persist a useless Calendar/Sheets token that lacks required scopes
            return {
                "ok": False,
                "error_code": "insufficient_scopes",
                "telegram_id": user.telegram_id,
                "service": service,
                "actual_scopes": actual_scopes,
                "error": (
                    "Google connected, but the required permission was not granted. "
                    "Please tap Connect Google again and allow all requested access "
                    "(Calendar, Gmail, Drive, and Sheets)."
                ),
            }

        saved_services = self._persist_tokens_for_all_covered(
            user, tokens=tokens, actual_scopes=actual_scopes
        )
        return {
            "ok": True,
            "telegram_id": user.telegram_id,
            "user_id": str(user.id),
            "service": service,
            "scopes": actual_scopes,
            "saved_services": saved_services,
            "pending_spreadsheet_id": payload.get("pending_spreadsheet_id") or "",
            "pending_action": payload.get("pending_action") or "",
            "message": "Google is connected. You can return to Telegram.",
        }

    def get_valid_access_token(self, user: User, *, service: str = GoogleService.DRIVE) -> str | None:
        integ = self._integration_for_service(user, service)
        if not integ:
            integ = self._adopt_token_from_sibling(user, service)
        if not integ:
            return None
        access = decrypt_text(integ.access_token_encrypted)
        # Never treat demo placeholders as live Google credentials
        if not access or str(access).startswith("demo:"):
            return None
        if integ.token_expires_at and integ.token_expires_at <= timezone.now() + timedelta(minutes=2):
            refresh = decrypt_text(integ.refresh_token_encrypted)
            if not refresh or str(refresh).startswith("demo:"):
                integ.is_active = False
                integ.save(update_fields=["is_active", "updated_at"])
                return None
            try:
                tokens = self._refresh(refresh)
            except Exception as exc:  # noqa: BLE001
                logger.warning("event=oauth_refresh_failed err=%s", type(exc).__name__)
                return None
            # Prefer scopes Google returned on refresh; fall back to tokeninfo
            if not tokens.get("scope"):
                tokens["scope"] = " ".join(
                    self._resolve_actual_scopes(
                        {
                            "access_token": tokens.get("access_token") or "",
                            "scope": " ".join(integ.scopes or []),
                        }
                    )
                )
            # Reject refresh tokens that lost required scopes (e.g. Calendar revoked)
            missing = self._missing_required_scopes(
                service,
                [s for s in str(tokens.get("scope") or "").split() if s],
            )
            if missing:
                logger.warning(
                    "event=oauth_refresh_scopes_insufficient telegram_id=%s service=%s",
                    user.telegram_id,
                    service,
                )
                integ.is_active = False
                integ.save(update_fields=["is_active", "updated_at"])
                return None
            # Refresh updates every Atlas service that these scopes still cover
            self._persist_tokens_for_all_covered(
                user,
                tokens=tokens,
                actual_scopes=[s for s in str(tokens.get("scope") or "").split() if s],
            )
            access = tokens.get("access_token") or ""
        return access or None

    def is_connected(self, user: User, *, service: str = GoogleService.DRIVE) -> bool:
        return self.get_valid_access_token(user, service=service) is not None

    def disconnect(self, user: User, *, service: str = GoogleService.DRIVE) -> None:
        GoogleIntegration.objects.filter(user=user, service=service).update(is_active=False)

    def _client_config(self) -> dict[str, Any]:
        return {
            "web": {
                "client_id": (settings.GOOGLE_CLIENT_ID or "").strip(),
                "client_secret": (settings.GOOGLE_CLIENT_SECRET or "").strip(),
                "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [(settings.GOOGLE_REDIRECT_URI or "").strip()],
            }
        }

    def _build_flow(
        self,
        *,
        scopes: list[str],
        state: str | None = None,
        code_verifier: str | None = None,
    ):
        from google_auth_oauthlib.flow import Flow

        kwargs: dict[str, Any] = {"scopes": scopes}
        if state is not None:
            kwargs["state"] = state
        if code_verifier:
            kwargs["code_verifier"] = code_verifier
            kwargs["autogenerate_code_verifier"] = False
        flow = Flow.from_client_config(self._client_config(), **kwargs)
        flow.redirect_uri = (settings.GOOGLE_REDIRECT_URI or "").strip()
        return flow

    @staticmethod
    def _validate_auth_url(auth_url: str) -> tuple[bool, str]:
        """Ensure include_granted_scopes=true exactly once and scopes are well-formed."""
        if not auth_url.startswith("https://accounts.google.com/"):
            return False, "unexpected_host"
        raw_matches = re.findall(r"include_granted_scopes=([^&]*)", auth_url)
        if len(raw_matches) != 1:
            return False, f"include_granted_scopes_count={len(raw_matches)}"
        if raw_matches[0] != "true":
            return False, f"include_granted_scopes_value={raw_matches[0]!r}"
        if "include_granted_scopes=True" in auth_url or "include_granted_scopes=False" in auth_url:
            return False, "python_bool_cased"
        if "include_granted_scopes=%22" in auth_url or "include_granted_scopes=%27" in auth_url:
            return False, "quoted_value"
        if "www..com" in auth_url:
            return False, "malformed_googleapis_host"
        qs = parse_qs(urlparse(auth_url).query, keep_blank_values=True)
        vals = qs.get("include_granted_scopes") or []
        if vals != ["true"]:
            return False, f"parsed_values={vals!r}"
        scope_blob = " ".join(qs.get("scope") or [])
        from urllib.parse import unquote

        scope_blob = unquote(scope_blob)
        if "googleapis.com/auth/spreadsheets.readonly" not in scope_blob and "drive.readonly" not in scope_blob:
            # Drive-only connect is fine without spreadsheets scope
            if "googleapis.com/auth/" not in scope_blob:
                return False, "missing_googleapis_scopes"
        if "www..com" in scope_blob:
            return False, "malformed_scope_host"
        for key, value_list in qs.items():
            for v in value_list:
                if v != v.strip() or (v.startswith('"') and v.endswith('"')):
                    return False, f"whitespace_or_quotes_on_{key}"
        return True, "ok"

    def _exchange_code(
        self,
        code: str,
        *,
        scopes: list[str],
        state: str,
        code_verifier: str | None = None,
    ) -> dict[str, Any]:
        # oauthlib raises Warning when Google returns a subset/superset of scopes
        # (common with include_granted_scopes / openid+email). Relax so exchange succeeds.
        os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
        flow = self._build_flow(
            scopes=scopes, state=state, code_verifier=code_verifier
        )
        try:
            flow.fetch_token(code=code)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "event=oauth_fetch_token_failed err=%s detail=%s",
                type(exc).__name__,
                str(exc)[:200],
            )
            raise
        creds = flow.credentials
        expires_in = 3600
        if creds.expiry is not None:
            from datetime import timezone as dt_timezone

            expiry = creds.expiry
            if timezone.is_naive(expiry):
                expiry = timezone.make_aware(expiry, dt_timezone.utc)
            delta = expiry - timezone.now()
            expires_in = max(60, int(delta.total_seconds()))
        # Prefer the scope string Google actually returned on the token response.
        raw_scope = ""
        try:
            token_data = getattr(flow.oauth2session, "token", None) or {}
            raw_scope = str(token_data.get("scope") or "").strip()
        except Exception:  # noqa: BLE001
            raw_scope = ""
        if not raw_scope and creds.scopes:
            raw_scope = " ".join(creds.scopes)
        return {
            "access_token": creds.token or "",
            "refresh_token": creds.refresh_token or "",
            "expires_in": expires_in,
            "scope": raw_scope,
        }

    def _resolve_actual_scopes(self, tokens: dict[str, Any]) -> list[str]:
        """Return scopes actually present on the access token (via tokeninfo)."""
        access = (tokens.get("access_token") or "").strip()
        claimed = [
            s for s in str(tokens.get("scope") or "").replace(",", " ").split() if s
        ]
        if not access or access.startswith("demo:"):
            return claimed
        try:
            import httpx

            resp = httpx.get(
                "https://oauth2.googleapis.com/tokeninfo",
                params={"access_token": access},
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json() or {}
                scope_blob = str(data.get("scope") or "").strip()
                if scope_blob:
                    return [s for s in scope_blob.replace(",", " ").split() if s]
        except Exception as exc:  # noqa: BLE001
            logger.warning("event=oauth_tokeninfo_failed err=%s", type(exc).__name__)
        return claimed

    def _missing_required_scopes(self, service: str, actual_scopes: list[str]) -> list[str]:
        required_any = _SERVICE_REQUIRED_SCOPE_ANY.get(service) or set()
        if not required_any:
            return []
        actual = set(actual_scopes)
        if actual & required_any:
            return []
        return sorted(required_any)

    def token_has_required_scopes(self, user: User, *, service: str) -> bool:
        integ = self._integration_for_service(user, service)
        if not integ:
            integ = self._find_sibling_with_scopes(user, service)
        if not integ:
            return False
        access = decrypt_text(integ.access_token_encrypted)
        if not access or str(access).startswith("demo:"):
            return False
        actual = list(integ.scopes or [])
        # Prefer stored scopes (fast path). Only hit tokeninfo when empty.
        if not actual:
            actual = self._resolve_actual_scopes(
                {"access_token": access, "scope": " ".join(integ.scopes or [])}
            )
        if actual and list(integ.scopes or []) != actual and integ.service == service:
            integ.scopes = actual
            integ.save(update_fields=["scopes", "updated_at"])
        return not self._missing_required_scopes(service, actual)

    def _integration_for_service(
        self, user: User, service: str
    ) -> GoogleIntegration | None:
        return (
            GoogleIntegration.objects.filter(user=user, service=service, is_active=True)
            .order_by("-updated_at")
            .first()
        )

    def _find_sibling_with_scopes(
        self, user: User, service: str
    ) -> GoogleIntegration | None:
        """Find any active Google row whose scopes cover the requested service."""
        for integ in GoogleIntegration.objects.filter(
            user=user, is_active=True
        ).order_by("-updated_at"):
            access = decrypt_text(integ.access_token_encrypted)
            if not access or str(access).startswith("demo:"):
                continue
            scopes = list(integ.scopes or [])
            if not self._missing_required_scopes(service, scopes):
                return integ
        return None

    def _adopt_token_from_sibling(
        self, user: User, service: str
    ) -> GoogleIntegration | None:
        """
        If Calendar was connected with the full Atlas scope bundle, clone that
        token into Gmail/Drive/Sheets rows so those surfaces never re-prompt.
        """
        sibling = self._find_sibling_with_scopes(user, service)
        if not sibling:
            return None
        access = decrypt_text(sibling.access_token_encrypted) or ""
        refresh = decrypt_text(sibling.refresh_token_encrypted) or ""
        if not access or access.startswith("demo:"):
            return None
        expires_in = 3600
        if sibling.token_expires_at:
            expires_in = max(
                60, int((sibling.token_expires_at - timezone.now()).total_seconds())
            )
        tokens = {
            "access_token": access,
            "refresh_token": refresh,
            "expires_in": expires_in,
            "scope": " ".join(sibling.scopes or []),
        }
        return self._persist_tokens(user, service=service, tokens=tokens)

    def _persist_tokens_for_all_covered(
        self,
        user: User,
        *,
        tokens: dict[str, Any],
        actual_scopes: list[str],
    ) -> list[str]:
        """Write the same Google token onto every Atlas service the scopes cover."""
        saved: list[str] = []
        for svc in _ALL_ATLAS_SERVICES:
            if self._missing_required_scopes(svc, actual_scopes):
                continue
            self._persist_tokens(user, service=svc, tokens=tokens)
            saved.append(svc)
        logger.info(
            "event=oauth_tokens_fanout telegram_id=%s services=%s",
            user.telegram_id,
            saved,
        )
        return saved

    def _refresh(self, refresh_token: str) -> dict[str, Any]:
        import httpx

        resp = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={
                "refresh_token": refresh_token,
                "client_id": (settings.GOOGLE_CLIENT_ID or "").strip(),
                "client_secret": (settings.GOOGLE_CLIENT_SECRET or "").strip(),
                "grant_type": "refresh_token",
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        data.setdefault("refresh_token", refresh_token)
        return data

    def _persist_tokens(
        self,
        user: User,
        *,
        service: str,
        tokens: dict[str, Any],
        existing: GoogleIntegration | None = None,
    ) -> GoogleIntegration:
        expires_in = int(tokens.get("expires_in") or 3600)
        access = tokens.get("access_token") or ""
        refresh = tokens.get("refresh_token") or ""
        integ = existing or GoogleIntegration.objects.filter(user=user, service=service).first()
        if integ is None:
            integ = GoogleIntegration(user=user, service=service)
        # Preserve prior refresh token when Google omits a new one
        if not refresh and integ.refresh_token_encrypted:
            prior = decrypt_text(integ.refresh_token_encrypted)
            if prior and not str(prior).startswith("demo:"):
                refresh = prior
        integ.access_token_encrypted = encrypt_text(access)
        if refresh:
            integ.refresh_token_encrypted = encrypt_text(refresh)
        integ.token_expires_at = timezone.now() + timedelta(seconds=expires_in)
        integ.scopes = [s for s in str(tokens.get("scope") or "").split() if s]
        integ.is_active = True
        integ.last_refreshed_at = timezone.now()
        integ.save()
        logger.info(
            "event=oauth_tokens_saved telegram_id=%s service=%s scopes=%s",
            user.telegram_id,
            service,
            integ.scopes,
        )
        return integ
