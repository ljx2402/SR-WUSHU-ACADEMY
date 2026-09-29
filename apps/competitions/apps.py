from django.apps import AppConfig


class CompetitionsConfig(AppConfig):
    name = "apps.competitions"

    def ready(self):
        from . import signals  # noqa: F401
