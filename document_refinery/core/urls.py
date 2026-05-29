from django.urls import path

from . import views


urlpatterns = [
    path("", views.home, name="home"),
    path("healthz", views.healthz, name="healthz"),
    path("readyz", views.readyz, name="readyz"),
    path("metrics", views.metrics, name="metrics"),
]
