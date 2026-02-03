from rest_framework.routers import DefaultRouter

from .views import ArtifactViewSet, DocumentViewSet, JobViewSet, WebhookEndpointViewSet


router = DefaultRouter()
router.register("documents", DocumentViewSet, basename="document")
router.register("artifacts", ArtifactViewSet, basename="artifact")
router.register("jobs", JobViewSet, basename="job")
router.register("webhooks", WebhookEndpointViewSet, basename="webhook")

urlpatterns = router.urls
