from django.apps import AppConfig


class UserappConfig(AppConfig):
    name = 'userapp'

    def ready(self):
        from . import checks  # noqa: F401
