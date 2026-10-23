from django.apps import AppConfig


class CommonConfig(AppConfig):
    name = "common"

    def ready(self) -> None:
        # Imported here because it reads settings, which aren't loaded when this module is.
        from common.startup_warnings import report_config_warnings

        report_config_warnings()
