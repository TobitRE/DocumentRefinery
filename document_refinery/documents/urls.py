from rest_framework.routers import DefaultRouter

from .views import ArtifactViewSet, DocumentViewSet


router = DefaultRouter()
router.register("documents", DocumentViewSet, basename="document")
router.register("artifacts", ArtifactViewSet, basename="artifact")

urlpatterns = router.urls
