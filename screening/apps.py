from django.apps import AppConfig


class ScreeningConfig(AppConfig):
    name = 'screening'

    def ready(self):
        # Registers the recompute-on-skills-change signal handlers.
        from . import signals  # noqa: F401
