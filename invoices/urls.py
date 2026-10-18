from django.urls import path
from . import views
app_name = "invoices"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("invoices/", views.invoice_list, name="list"),
    path("invoices/upload/", views.upload, name="upload"),
    path("invoices/export/", views.export, name="export"),
    path("invoices/<int:pk>/", views.detail, name="detail"),
    path("invoices/<int:pk>/edit/", views.edit, name="edit"),
    path("invoices/<int:pk>/source/", views.source, name="source"),
    path("invoices/<int:pk>/delete/", views.delete, name="delete"),
    path("invoices/<int:pk>/action/<str:action>/", views.transition, name="transition"),
]
