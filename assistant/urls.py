from django.urls import path

from . import views

urlpatterns = [
    path("api/ask", views.AskView.as_view(), name="ask"),
    path("api/health", views.HealthView.as_view(), name="health"),
    path("api/examples", views.ExamplesView.as_view(), name="examples"),
    path("", views.index, name="index"),
]
