import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from api.models import CandidateSite, ExistingStation, OptimizationRun, SubRegion
from api.optimizer import RESULTS_PATH, clear_cache


class Command(BaseCommand):
    help = "Seed the database from data-pipeline/results/results.json"

    def handle(self, *args, **options):
        results_path: Path = RESULTS_PATH
        if not results_path.exists():
            raise CommandError(
                f"results.json not found at {results_path}. Run the data pipeline first: "
                "cd data-pipeline && python scripts/run_pipeline.py"
            )

        with results_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)

        node_to_region = {}
        for region in data.get("subregions", []):
            for node_id in region.get("node_ids", []):
                node_to_region[node_id] = region["id"]

        CandidateSite.objects.all().delete()
        candidates = [
            CandidateSite(
                node_id=c["node_id"],
                latitude=c["latitude"],
                longitude=c["longitude"],
                population_density=c["population_density"],
                traffic_proxy=c["traffic_proxy"],
                degree_centrality=c["degree_centrality"],
                betweenness_centrality=c["betweenness_centrality"],
                closeness_centrality=c.get("closeness_centrality", 0.0),
                eigenvector_centrality=c["eigenvector_centrality"],
                composite_importance=c["composite_importance"],
                distance_to_nearest_existing_station=c["distance_to_nearest_existing_station"],
                subregion_id=node_to_region.get(c["node_id"]),
            )
            for c in data.get("candidate_sites", [])
        ]
        CandidateSite.objects.bulk_create(candidates)
        self.stdout.write(self.style.SUCCESS(f"Seeded {len(candidates)} candidate sites."))

        ExistingStation.objects.all().delete()
        stations = [
            ExistingStation(
                station_name=s["station_name"],
                latitude=s["latitude"],
                longitude=s["longitude"],
                operator=s.get("operator", ""),
                source=s.get("source", "synthetic_generated"),
                notes=s.get("notes", ""),
            )
            for s in data.get("existing_stations", [])
        ]
        ExistingStation.objects.bulk_create(stations)
        self.stdout.write(self.style.SUCCESS(f"Seeded {len(stations)} existing stations."))

        SubRegion.objects.all().delete()
        regions = [
            SubRegion(
                region_id=r["id"],
                node_count=r["node_count"],
                centroid_lat=r["centroid"][0],
                centroid_lon=r["centroid"][1],
                total_demand=r.get("total_demand", 0.0),
                boundary=r.get("boundary", []),
            )
            for r in data.get("subregions", [])
        ]
        SubRegion.objects.bulk_create(regions)
        self.stdout.write(self.style.SUCCESS(f"Seeded {len(regions)} sub-regions."))

        opt = data.get("optimization", {})
        stats = opt.get("coverage_stats", {})
        OptimizationRun.objects.create(
            budget=opt.get("default_budget", 0),
            objective_value=opt.get("objective_value", 0.0),
            solver_status=opt.get("solver_status", "Unknown"),
            coverage_radius_m=opt.get("coverage_radius_m", 0.0),
            before_pct_covered=stats.get("before_pct_covered", 0.0),
            after_pct_covered=stats.get("after_pct_covered", 0.0),
            before_avg_distance_m=stats.get("before_avg_distance_m"),
            after_avg_distance_m=stats.get("after_avg_distance_m"),
            selected_site_ids=[s["node_id"] for s in opt.get("selected_sites", [])],
            solve_time_seconds=0.0,
        )
        self.stdout.write(self.style.SUCCESS("Recorded default OptimizationRun from pipeline output."))

        clear_cache()
        self.stdout.write(self.style.SUCCESS("Done."))
