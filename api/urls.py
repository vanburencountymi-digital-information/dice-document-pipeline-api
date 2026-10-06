from django.urls import path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from api.views import (
    CreateRemediationView,
    DocumentDownloadView,
    DocumentStatusView,
    StatusView,
)

urlpatterns = [
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
    path("status/", StatusView.as_view(), name="status"),
    path("submit-document/", CreateRemediationView.as_view(), name="submit-document"),
    path(
        "document-status/<str:content_hash>/",
        DocumentStatusView.as_view(),
        name="document-status",
    ),
    path(
        "document-download/<str:content_hash>/",
        DocumentDownloadView.as_view(),
        name="document-download",
    ),
]
