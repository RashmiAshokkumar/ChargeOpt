from django.urls import path

from .views import network_stats, candidates, existing_stations, subregions, optimize

urlpatterns = [
    path("network-stats/", network_stats, name="network-stats"),
    path("candidates/", candidates, name="candidates"),
    path("existing-stations/", existing_stations, name="existing-stations"),
    path("subregions/", subregions, name="subregions"),
    path("optimize/", optimize, name="optimize"),
]
