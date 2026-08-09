from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self) -> None:
        # Keep free-tier hosts warm while the process is already awake.
        # (External UptimeRobot on /health/ is still recommended.)
        try:
            from core.keepalive import start_keepalive_thread

            start_keepalive_thread()
        except Exception:
            pass
