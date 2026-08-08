"""User lifecycle for Telegram identity."""

from __future__ import annotations

import logging

from django.db import transaction

from accounts.models import User
from memory.models import UserPreference
from notifications.models import NotificationPreference

logger = logging.getLogger("atlas.accounts.user_service")


class UserService:
    @staticmethod
    @transaction.atomic
    def get_or_create_from_telegram(
        telegram_id: int,
        username: str = "",
        first_name: str = "",
        last_name: str = "",
    ) -> tuple[User, bool]:
        """
        Resolve a Telegram user into our User model.
        Creates preference rows on first sighting.
        Returns (user, created).
        """
        user, created = User.objects.get_or_create(
            telegram_id=telegram_id,
            defaults={
                "telegram_username": username or "",
                "first_name": first_name or "",
                "last_name": last_name or "",
            },
        )

        if created:
            UserPreference.objects.get_or_create(user=user)
            NotificationPreference.objects.get_or_create(user=user)
            logger.info(
                "event=user_created telegram_id=%s username=%s",
                telegram_id,
                username or "-",
            )
        else:
            # Preference rows are created on first sighting (above). On the hot
            # path (returning users) they already exist — avoid two SELECTs per
            # message by not re-running get_or_create here.
            dirty_fields: list[str] = []
            if username and user.telegram_username != username:
                user.telegram_username = username
                dirty_fields.append("telegram_username")
            if first_name and user.first_name != first_name:
                user.first_name = first_name
                dirty_fields.append("first_name")
            if last_name and user.last_name != last_name:
                user.last_name = last_name
                dirty_fields.append("last_name")
            if dirty_fields:
                user.save(update_fields=[*dirty_fields, "updated_at"])
                logger.info(
                    "event=user_updated telegram_id=%s fields=%s",
                    telegram_id,
                    dirty_fields,
                )

        return user, created
