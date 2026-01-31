from django.urls import path

from .views import DashboardSummaryView, DashboardWorkersView, UsageReportView


urlpatterns = [
    path("summary", DashboardSummaryView.as_view(), name="dashboard-summary"),
    path("workers", DashboardWorkersView.as_view(), name="dashboard-workers"),
    path("reports/usage", UsageReportView.as_view(), name="usage-report"),
]
