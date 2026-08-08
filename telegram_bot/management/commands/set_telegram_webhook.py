"""Register Telegram HTTPS webhook for production (Render free web).

Usage:
  python manage.py set_telegram_webhook
  python manage.py set_telegram_webhook --dry-run
  python manage.py set_telegram_webhook --delete
  python manage.py set_telegram_webhook --info

Never prints TELEGRAM_BOT_TOKEN or TELEGRAM_WEBHOOK_SECRET.
"""

from __future__ import annotations

import json

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Register or inspect the Telegram bot webhook (production)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate env and print the plan without calling Telegram.",
        )
        parser.add_argument(
            "--delete",
            action="store_true",
            help="Call deleteWebhook (useful before switching back to polling).",
        )
        parser.add_argument(
            "--info",
            action="store_true",
            help="Call getWebhookInfo only.",
        )
        parser.add_argument(
            "--drop-pending",
            action="store_true",
            help="Pass drop_pending_updates=true to setWebhook/deleteWebhook.",
        )

    def handle(self, *args, **options):
        token = (settings.TELEGRAM_BOT_TOKEN or "").strip()
        if not token:
            raise CommandError("TELEGRAM_BOT_TOKEN is missing.")

        base = f"https://api.telegram.org/bot{token}"
        # Never log or print the token.
        self.stdout.write(f"token_configured=True token_len={len(token)}")

        if options["info"]:
            self._get_info(base)
            return

        if options["delete"]:
            if options["dry_run"]:
                self.stdout.write("dry_run=True action=deleteWebhook")
                return
            self._delete(base, drop_pending=options["drop_pending"])
            return

        url = (settings.TELEGRAM_WEBHOOK_URL or "").strip()
        secret = (settings.TELEGRAM_WEBHOOK_SECRET or "").strip()
        if not url:
            raise CommandError(
                "TELEGRAM_WEBHOOK_URL is missing. "
                "Example: https://your-service.onrender.com/api/telegram/webhook/"
            )
        if not url.startswith("https://"):
            raise CommandError("TELEGRAM_WEBHOOK_URL must be HTTPS.")
        if "/api/telegram/webhook" not in url:
            self.stdout.write(
                self.style.WARNING(
                    "URL does not contain /api/telegram/webhook — "
                    "confirm this matches your Django route."
                )
            )
        if not secret and not settings.DEBUG:
            raise CommandError(
                "TELEGRAM_WEBHOOK_SECRET is required in production "
                "(DEBUG=False)."
            )

        payload = {
            "url": url,
            "allowed_updates": ["message"],
            "drop_pending_updates": bool(options["drop_pending"]),
        }
        if secret:
            payload["secret_token"] = secret

        self.stdout.write(
            "plan=setWebhook "
            f"url={url} "
            f"allowed_updates={payload['allowed_updates']} "
            f"secret_configured={bool(secret)} "
            f"drop_pending={payload['drop_pending_updates']}"
        )

        if options["dry_run"]:
            self.stdout.write(self.style.SUCCESS("dry_run=True — no Telegram API call."))
            return

        try:
            resp = httpx.post(f"{base}/setWebhook", json=payload, timeout=30)
        except httpx.HTTPError as exc:
            raise CommandError(f"Telegram setWebhook network error: {type(exc).__name__}") from exc

        data = self._safe_json(resp)
        ok = bool(data.get("ok"))
        # Never echo secret_token from request; Telegram response has no secret.
        desc = str(data.get("description") or "")[:200]
        if not ok:
            raise CommandError(f"setWebhook failed status={resp.status_code} desc={desc}")

        self.stdout.write(self.style.SUCCESS(f"setWebhook ok description={desc or 'ok'}"))
        self._get_info(base)

    def _delete(self, base: str, *, drop_pending: bool) -> None:
        try:
            resp = httpx.post(
                f"{base}/deleteWebhook",
                json={"drop_pending_updates": drop_pending},
                timeout=30,
            )
        except httpx.HTTPError as exc:
            raise CommandError(f"deleteWebhook network error: {type(exc).__name__}") from exc
        data = self._safe_json(resp)
        if not data.get("ok"):
            raise CommandError(f"deleteWebhook failed: {data}")
        self.stdout.write(self.style.SUCCESS("deleteWebhook ok"))
        self._get_info(base)

    def _get_info(self, base: str) -> None:
        try:
            resp = httpx.get(f"{base}/getWebhookInfo", timeout=30)
        except httpx.HTTPError as exc:
            raise CommandError(f"getWebhookInfo network error: {type(exc).__name__}") from exc
        data = self._safe_json(resp)
        result = data.get("result") or {}
        # Print safe fields only
        safe = {
            "url": result.get("url"),
            "has_custom_certificate": result.get("has_custom_certificate"),
            "pending_update_count": result.get("pending_update_count"),
            "last_error_date": result.get("last_error_date"),
            "last_error_message": result.get("last_error_message"),
            "max_connections": result.get("max_connections"),
            "allowed_updates": result.get("allowed_updates"),
        }
        self.stdout.write("webhook_info=" + json.dumps(safe, default=str))

    @staticmethod
    def _safe_json(resp: httpx.Response) -> dict:
        try:
            data = resp.json()
            return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001
            return {"ok": False, "description": f"non_json status={resp.status_code}"}
