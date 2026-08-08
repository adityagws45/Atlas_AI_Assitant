from django.apps import AppConfig


class GCalendarConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "gcalendar"
    label = "gcalendar"
    verbose_name = "Google Calendar"
