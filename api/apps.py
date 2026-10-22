from django.apps import AppConfig


class ApiConfig(AppConfig):
    name = "api"

    def ready(self) -> None:
        # Registers the knox auth scheme with the API docs (drf-spectacular).
        from api import schema  # noqa: F401
        from api.client_ip import warn_if_proxy_hops_unset

        warn_if_proxy_hops_unset()
