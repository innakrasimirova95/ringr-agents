from django.urls import path

from calls import views

urlpatterns = [
    path('calls/<uuid:call_id>/context', views.call_context),
    path('calls/<uuid:call_id>/result', views.call_result),
    path('tenants/<int:tenant_id>/config', views.tenant_config),
]
