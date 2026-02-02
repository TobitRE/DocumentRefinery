from django.urls import path

from .web_views import DashboardPageView, system_status


urlpatterns = [
    path("", DashboardPageView.as_view(), name="dashboard-ui"),
    path("system", system_status, name="dashboard-system"),
]
