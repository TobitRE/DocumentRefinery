from rest_framework.routers import DefaultRouter

from .views import ArtifactViewSet, DocumentViewSet, JobViewSet


router = DefaultRouter()
router.register("documents", DocumentViewSet, basename="document")
router.register("artifacts", ArtifactViewSet, basename="artifact")
router.register("jobs", JobViewSet, basename="job")

urlpatterns = router.urls
