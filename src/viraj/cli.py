"""viraj demo | analyze | route | validate | sensitivity."""

import argparse
import json
import sys
from pathlib import Path

from .core import Config, analyze_lonlat
from .export import export_analysis
from .routing import ANKARA, KONYA, fetch_osmnx, fetch_osrm, load_route, save_route


def add_config(parser):
    defaults = Config()
    parser.add_argument(
        "--spacing", type=float, default=defaults.spacing_m, help="Maximum sample spacing (m)"
    )
    parser.add_argument(
        "--smoothing",
        type=float,
        default=defaults.smoothing_m,
        help="Radius estimator smoothing span (m); 0=off",
    )
    parser.add_argument(
        "--radius-threshold",
        type=float,
        default=defaults.radius_threshold_m,
        help="Detection seed radius limit (m); wide curves included by default",
    )
    parser.add_argument("--min-length", type=float, default=defaults.min_length_m)
    parser.add_argument("--merge-gap", type=float, default=defaults.merge_gap_m)
    parser.add_argument(
        "--min-turn",
        type=float,
        default=defaults.min_turn_deg,
        help="Minimum net heading change (deg)",
    )
    parser.add_argument(
        "--detection-sigma",
        type=float,
        default=defaults.detection_sigma_m,
        help="Heading detection Gaussian sigma (m)",
    )
    parser.add_argument(
        "--release-ratio",
        type=float,
        default=defaults.release_ratio,
        help="Continuation curvature / seed curvature, in (0,1]",
    )
    parser.add_argument(
        "--opposite-tolerance",
        type=float,
        default=defaults.opposite_tolerance_deg,
        help="Maximum integrated opposite turn when bridging gaps (deg)",
    )
    parser.add_argument(
        "--heading-transition-cap",
        type=float,
        default=defaults.heading_transition_cap_m,
        help="Maximum inferred transition on each source-segment end (m)",
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="OSM rota geometrisinden yaklaşık viraj analizi")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Open the local route and angle test workbench")
    serve.add_argument("--port", type=int, default=8766)
    serve.add_argument("--cache-dir", default="cache")
    serve.add_argument("--osrm-url", default="https://router.project-osrm.org")
    demo = sub.add_parser("demo", help="Konya–Ankara OSRM demo (cached after first request)")
    demo.add_argument("--out", default="outputs/konya-ankara")
    demo.add_argument("--cache-dir", default="cache")
    route = sub.add_parser("route", help="Route between two points, in longitude latitude order")
    route.add_argument("--start", nargs=2, type=float, required=True, metavar=("LON", "LAT"))
    route.add_argument("--end", nargs=2, type=float, required=True, metavar=("LON", "LAT"))
    route.add_argument("--provider", choices=("osrm", "osmnx"), default="osrm")
    route.add_argument("--osrm-url", default="https://router.project-osrm.org")
    route.add_argument("--buffer", type=float, default=2000.0)
    route.add_argument("--cache-dir", default="cache")
    route.add_argument("--out", default="outputs/route")
    analyze = sub.add_parser(
        "analyze", help="Analyze a saved route without routing network requests"
    )
    analyze.add_argument("input", type=Path)
    analyze.add_argument("--out", default="outputs/analysis")
    analyze.add_argument("--compare-to", type=Path, help="Overlay previous bends.geojson on map")
    analyze.add_argument(
        "--check-point",
        nargs=2,
        type=float,
        metavar=("LON", "LAT"),
        help="Mark and inspect a reported missed bend (longitude first)",
    )
    sensitivity = sub.add_parser("sensitivity", help="Parameter sweep on one saved route")
    sensitivity.add_argument("input", type=Path)
    sensitivity.add_argument("--out", default="outputs/sensitivity")
    validate = sub.add_parser("validate", help="Offline synthetic accuracy and sensitivity report")
    validate.add_argument("--out", default="outputs/validation")
    for p in (demo, route, analyze, validate, sensitivity):
        add_config(p)
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            try:
                from .web import run_server
            except ImportError as exc:
                raise ValueError('Web bağımlılıklarını kur: pip install -e ".[web]"') from exc
            run_server(port=args.port, cache_dir=args.cache_dir, osrm_url=args.osrm_url)
            return 0
        config = Config(
            spacing_m=args.spacing,
            smoothing_m=args.smoothing,
            radius_threshold_m=args.radius_threshold,
            min_length_m=args.min_length,
            merge_gap_m=args.merge_gap,
            min_turn_deg=args.min_turn,
            detection_sigma_m=args.detection_sigma,
            release_ratio=args.release_ratio,
            opposite_tolerance_deg=args.opposite_tolerance,
            heading_transition_cap_m=args.heading_transition_cap,
        )
        if args.command == "validate":
            from .validation import run_validation

            summary = run_validation(args.out, config)
            print(json.dumps(summary, indent=2, ensure_ascii=True))
            return 0 if all(summary["synthetic_acceptance"].values()) else 1
        if args.command in ("analyze", "sensitivity"):
            coords, metadata = load_route(args.input)
        elif args.command == "demo":
            coords, metadata = fetch_osrm(KONYA, ANKARA, cache_dir=args.cache_dir)
            metadata["title"] = "Konya → Ankara"
            metadata["anchor_note"] = "Approximate city centers; not user-selected street addresses"
        elif args.provider == "osmnx":
            coords, metadata = fetch_osmnx(
                args.start, args.end, buffer_m=args.buffer, cache_dir=args.cache_dir
            )
        else:
            coords, metadata = fetch_osrm(
                args.start, args.end, base_url=args.osrm_url, cache_dir=args.cache_dir
            )
        if args.command == "sensitivity":
            from .validation import route_sensitivity

            route_sensitivity(coords, args.out, config)
            print(f"Sensitivity report: {Path(args.out).resolve()}")
            return 0
        analysis = analyze_lonlat(coords, config, metadata=metadata)
        if analysis.metadata.get("review_points") or (
            args.command == "analyze" and args.check_point
        ):
            from .inspection import check_point

            review_points = analysis.metadata.get("review_points", [])
            if args.command == "analyze" and args.check_point:
                review_points = [{"lonlat": args.check_point, "name": "Bildirilen nokta"}]
            # Always recompute IDs/offsets when parameters change.
            analysis.metadata["review_points"] = [
                check_point(analysis, review["lonlat"], review.get("name", "Bildirilen nokta"))
                for review in review_points
            ]
            print(
                "Point check: " + json.dumps(analysis.metadata["review_points"], ensure_ascii=True)
            )
        comparison = None
        if args.command == "analyze" and args.compare_to:
            comparison = json.loads(args.compare_to.read_text(encoding="utf-8"))
            if comparison.get("type") != "FeatureCollection":
                raise ValueError("Comparison must be a bends GeoJSON FeatureCollection")
        folder = export_analysis(analysis, args.out, comparison=comparison)
        save_route(folder / "route_input.geojson", coords, metadata)
        print(f"Route: {analysis.s[-1] / 1000:.2f} km; bend candidates: {len(analysis.bends)}")
        print(f"Report: {(folder / 'map.html').resolve()}")
        return 0
    except (ValueError, OSError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
