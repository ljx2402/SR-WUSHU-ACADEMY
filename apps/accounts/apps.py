from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = "apps.accounts"
    verbose_name = "Users, parents and coaches"

    def ready(self):
        from . import signals  # noqa: F401
