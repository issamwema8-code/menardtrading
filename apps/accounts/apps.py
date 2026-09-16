from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.accounts'
    verbose_name = 'Accounts & Access Control'

    def ready(self):
        try:
            import apps.accounts.signals  # noqa
        except ImportError:
            pass
