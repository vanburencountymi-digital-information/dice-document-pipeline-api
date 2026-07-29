from django.urls import path

from api.views import CreateRemediationView, DocumentStatusView, StatusView

urlpatterns = [
    path("status/", StatusView.as_view(), name="status"),
    path("submit-document/", CreateRemediationView.as_view(), name="submit-document"),
    path(
        "document-status/<str:content_hash>/",
        DocumentStatusView.as_view(),
        name="document-status",
    ),
]
