from django.urls import path
from rest_framework.authentication import SessionAuthentication
from rest_framework.routers import DefaultRouter
from drf_spectacular.views import SpectacularAPIView

from authn.authentication import APIKeyAuthentication
from authn.permissions import StaffOrAPIKey
from .views import (
    ArtifactViewSet,
    DoclingCapabilitiesView,
    DoclingOptionsResolveView,
    DoclingProfilesView,
    DocumentViewSet,
    JobViewSet,
    WebhookEndpointViewSet,
)


router = DefaultRouter()
router.register("documents", DocumentViewSet, basename="document")
router.register("artifacts", ArtifactViewSet, basename="artifact")
router.register("jobs", JobViewSet, basename="job")
router.register("webhooks", WebhookEndpointViewSet, basename="webhook")

urlpatterns = [
    path(
        "schema/",
        SpectacularAPIView.as_view(
            authentication_classes=[APIKeyAuthentication, SessionAuthentication],
            permission_classes=[StaffOrAPIKey],
        ),
        name="schema",
    ),
    path("docling/profiles/", DoclingProfilesView.as_view(), name="docling-profiles"),
    path(
        "docling/capabilities/",
        DoclingCapabilitiesView.as_view(),
        name="docling-capabilities",
    ),
    path(
        "docling/options/resolve/",
        DoclingOptionsResolveView.as_view(),
        name="docling-options-resolve",
    ),
    path(
        "documents/<uuid:document_uuid>/ingest/",
        DocumentViewSet.as_view({"post": "ingest_by_uuid"}),
        name="document-ingest-by-uuid",
    ),
] + router.urls
