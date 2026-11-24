from django.urls import path

from . import views

app_name = "search"
urlpatterns = [
    path("", views.home, name="home"),
    path("search/", views.results, name="results"),
    path("suggest/", views.suggest, name="suggest"),
    path("sources/", views.sources, name="sources"),
    path("healthz", views.healthz, name="healthz"),
    path("perf/", views.perf, name="perf"),
    path("perf.json", views.perf_json, name="perf_json"),
]
