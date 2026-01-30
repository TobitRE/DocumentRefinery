from django.urls import path

from .web_views import DashboardPageView


urlpatterns = [
    path("", DashboardPageView.as_view(), name="dashboard-ui"),
]
