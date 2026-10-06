from django.test import SimpleTestCase
from django.urls import reverse
from drf_spectacular.generators import SchemaGenerator
from parameterized import parameterized


class ApiDocsTests(SimpleTestCase):
    @parameterized.expand([("schema",), ("docs",)])
    def test_docs_pages_are_public(self, url_name: str) -> None:
        response = self.client.get(reverse(url_name))

        self.assertEqual(response.status_code, 200)

    def test_docs_page_loads_swagger_from_our_own_static_files(self) -> None:
        response = self.client.get(reverse("docs"))

        self.assertNotContains(response, "cdn.jsdelivr.net")
        self.assertContains(
            response, "/static/drf_spectacular_sidecar/swagger-ui-dist/swagger-ui-bundle.js"
        )


class SchemaContentTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.schema = SchemaGenerator().get_schema(request=None, public=True)

    def test_documents_all_four_endpoints(self) -> None:
        self.assertEqual(
            set(self.schema["paths"]),
            {
                "/api/status/",
                "/api/submit-document/",
                "/api/document-status/{content_hash}/",
                "/api/document-download/{content_hash}/",
            },
        )

    def test_submit_document_is_a_multipart_upload(self) -> None:
        post = self.schema["paths"]["/api/submit-document/"]["post"]

        self.assertIn("multipart/form-data", post["requestBody"]["content"])
        self.assertEqual(set(post["responses"]), {"200", "201"})

    def test_token_auth_is_an_authorization_header(self) -> None:
        scheme = self.schema["components"]["securitySchemes"]["knoxToken"]

        self.assertEqual(scheme["in"], "header")
        self.assertEqual(scheme["name"], "Authorization")
