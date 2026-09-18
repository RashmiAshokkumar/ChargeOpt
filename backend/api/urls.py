from django.urls import path

from .views import (
    OptimizeView,
    candidates,
    demand_heatmap,
    existing_stations,
    network_stats,
    optimization_history,
    road_network,
    subregions,
)

urlpatterns = [
    path("network-stats/", network_stats, name="network-stats"),
    path("road-network/", road_network, name="road-network"),
    path("demand-heatmap/", demand_heatmap, name="demand-heatmap"),
    path("candidates/", candidates, name="candidates"),
    path("existing-stations/", existing_stations, name="existing-stations"),
    path("subregions/", subregions, name="subregions"),
    path("optimize/", OptimizeView.as_view(), name="optimize"),
    path("optimization-history/", optimization_history, name="optimization-history"),
]
