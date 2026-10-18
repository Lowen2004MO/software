from django.urls import path
from .views import business_select
urlpatterns = [path("", business_select, name="business_select")]
