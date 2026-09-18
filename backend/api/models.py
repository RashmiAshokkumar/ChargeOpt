from django.db import models


class CandidateSite(models.Model):
    """A shortlisted road-network node considered for a new charging station."""

    node_id = models.CharField(max_length=64, unique=True)
    latitude = models.FloatField()
    longitude = models.FloatField()
    population_density = models.FloatField()
    traffic_proxy = models.FloatField()
    degree_centrality = models.FloatField()
    betweenness_centrality = models.FloatField()
    closeness_centrality = models.FloatField()
    eigenvector_centrality = models.FloatField()
    composite_importance = models.FloatField()
    distance_to_nearest_existing_station = models.FloatField()
    subregion_id = models.IntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-composite_importance"]

    def __str__(self) -> str:
        return f"CandidateSite({self.node_id})"


class ExistingStation(models.Model):
    """A known existing charging station. See provenance: synthetic, not scraped."""

    station_name = models.CharField(max_length=255)
    latitude = models.FloatField()
    longitude = models.FloatField()
    operator = models.CharField(max_length=128, blank=True)
    source = models.CharField(max_length=64, default="synthetic_generated")
    notes = models.TextField(blank=True)

    def __str__(self) -> str:
        return self.station_name


class SubRegion(models.Model):
    """A Louvain community-detection sub-region, used for equity constraints."""

    region_id = models.IntegerField(unique=True)
    node_count = models.IntegerField()
    centroid_lat = models.FloatField()
    centroid_lon = models.FloatField()
    total_demand = models.FloatField(default=0.0)
    boundary = models.JSONField(default=list)  # [[lat, lon], ...] convex hull

    def __str__(self) -> str:
        return f"SubRegion({self.region_id})"


class OptimizationRun(models.Model):
    """A record of one MCLP solve (live or from the default pipeline run)."""

    budget = models.IntegerField()
    objective_value = models.FloatField()
    solver_status = models.CharField(max_length=32)
    coverage_radius_m = models.FloatField()
    before_pct_covered = models.FloatField()
    after_pct_covered = models.FloatField()
    before_avg_distance_m = models.FloatField(null=True)
    after_avg_distance_m = models.FloatField(null=True)
    selected_site_ids = models.JSONField(default=list)
    solve_time_seconds = models.FloatField(default=0.0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"OptimizationRun(budget={self.budget}, obj={self.objective_value:.1f})"
