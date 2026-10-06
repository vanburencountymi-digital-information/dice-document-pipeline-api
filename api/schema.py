from drf_spectacular.extensions import OpenApiAuthenticationExtension


class KnoxTokenScheme(OpenApiAuthenticationExtension):
    """Tells the API docs that knox tokens go in the `Authorization` header, as `Token <key>`."""

    target_class = "knox.auth.TokenAuthentication"
    name = "knoxToken"

    def get_security_definition(self, auto_schema: object) -> dict[str, str]:
        return {
            "type": "apiKey",
            "in": "header",
            "name": "Authorization",
            "description": "Enter `Token <your token>` (the word Token, a space, then the token).",
        }
