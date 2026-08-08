"""
Anti-spam notification rules (design for later scheduler milestones).

These helpers encode product rules from the hackathon brief.
No proactive sends happen in Milestone 2.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone

from accounts.models import User
from notifications.models import NotificationLog, NotificationPreference


@dataclass
class SuppressionDecision:
    allow: bool
    reason: str = ""


class SuppressionService:
    """
    Gate every future proactive Telegram DM.

    Rules:
    - Never exceed max notifications / day
    - Never send more than one morning briefing / day
    - Stay silent if content is marked unimportant
    - Don't send duplicate content within 24h
    - Respect quiet hours and minimum gap between proactive messages
    """

    def should_send(
        self,
        user: User,
        *,
        notification_type: str,
        content: str,
        is_important: bool = True,
        content_fingerprint: str = "",
    ) -> SuppressionDecision:
        prefs, _ = NotificationPreference.objects.get_or_create(user=user)

        if prefs.silence_if_nothing_important and not is_important:
            return SuppressionDecision(False, "nothing_important")

        now = timezone.now()
        if self._in_quiet_hours(prefs, now):
            return SuppressionDecision(False, "quiet_hours")

        max_per_day = prefs.max_notifications_per_day or settings.MAX_NOTIFICATIONS_PER_DAY
        sent_today = NotificationLog.objects.filter(
            user=user,
            was_suppressed=False,
            sent_at__date=now.date(),
        ).count()
        if sent_today >= max_per_day:
            return SuppressionDecision(False, "daily_cap")

        if notification_type == "briefing":
            if NotificationLog.objects.filter(
                user=user,
                notification_type="briefing",
                was_suppressed=False,
                sent_at__date=now.date(),
            ).exists():
                return SuppressionDecision(False, "briefing_already_sent")

        if prefs.last_proactive_sent_at:
            gap = timedelta(minutes=settings.MIN_MINUTES_BETWEEN_PROACTIVE)
            if now - prefs.last_proactive_sent_at < gap:
                return SuppressionDecision(False, "too_soon")

        if content_fingerprint:
            since = now - timedelta(hours=24)
            if NotificationLog.objects.filter(
                user=user,
                was_suppressed=False,
                sent_at__gte=since,
                content__icontains=content_fingerprint[:80],
            ).exists():
                return SuppressionDecision(False, "duplicate")

        return SuppressionDecision(True, "")

    def _in_quiet_hours(self, prefs: NotificationPreference, now: datetime) -> bool:
        start = prefs.quiet_hours_start
        end = prefs.quiet_hours_end
        if not start or not end:
            return False
        current = now.timetz().replace(tzinfo=None)
        if start <= end:
            return start <= current <= end
        # Wraps midnight
        return current >= start or current <= end
