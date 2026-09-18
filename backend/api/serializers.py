from rest_framework import serializers

from .models import CandidateSite, ExistingStation, OptimizationRun, SubRegion


class CandidateSiteSerializer(serializers.ModelSerializer):
    class Meta:
        model = CandidateSite
        fields = [
            "node_id", "latitude", "longitude", "population_density", "traffic_proxy",
            "degree_centrality", "betweenness_centrality", "closeness_centrality",
            "eigenvector_centrality", "composite_importance",
            "distance_to_nearest_existing_station", "subregion_id",
        ]


class ExistingStationSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExistingStation
        fields = ["station_name", "latitude", "longitude", "operator", "source", "notes"]


class SubRegionSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubRegion
        fields = ["region_id", "node_count", "centroid_lat", "centroid_lon", "total_demand"]


class OptimizationRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = OptimizationRun
        fields = [
            "budget", "objective_value", "solver_status", "coverage_radius_m",
            "before_pct_covered", "after_pct_covered", "before_avg_distance_m",
            "after_avg_distance_m", "selected_site_ids", "solve_time_seconds", "created_at",
        ]
