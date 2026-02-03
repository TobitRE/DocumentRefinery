from django.urls import path

from .web_views import (
    DashboardPageView,
    api_key_detail,
    api_key_new,
    api_keys_list,
    system_status,
    webhook_deliveries_list,
    webhook_delivery_detail,
    webhook_detail,
    webhook_new,
    webhooks_list,
)


urlpatterns = [
    path("", DashboardPageView.as_view(), name="dashboard-ui"),
    path("system", system_status, name="dashboard-system"),
    path("api-keys/", api_keys_list, name="dashboard-api-keys"),
    path("api-keys/new/", api_key_new, name="dashboard-api-keys-new"),
    path("api-keys/<int:pk>/", api_key_detail, name="dashboard-api-key-detail"),
    path("webhooks/", webhooks_list, name="dashboard-webhooks"),
    path("webhooks/new/", webhook_new, name="dashboard-webhooks-new"),
    path("webhooks/<int:pk>/", webhook_detail, name="dashboard-webhooks-detail"),
    path("webhook-deliveries/", webhook_deliveries_list, name="dashboard-webhook-deliveries"),
    path(
        "webhook-deliveries/<int:pk>/",
        webhook_delivery_detail,
        name="dashboard-webhook-delivery-detail",
    ),
]
