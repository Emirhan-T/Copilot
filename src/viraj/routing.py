"""Routing adapters. Network downloads are explicit CLI operations and cached."""

import hashlib
import json
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

import numpy as np
from pyproj import Geod, Transformer

KONYA = (32.4846, 37.8714)  # Approximate city-center anchors, longitude first.
ANKARA = (32.8597, 39.9334)


def validate_point(point):
    if len(point) != 2 or not np.isfinite(point).all():
        raise ValueError("Point must contain finite longitude and latitude")
    if not -180 <= point[0] <= 180 or not -80 <= point[1] <= 84:
        raise ValueError("Point outside longitude/UTM latitude bounds")


def load_route(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("type") == "Feature":
        geometry, metadata = data["geometry"], data.get("properties") or {}
    elif data.get("type") == "FeatureCollection":
        routes = [f for f in data["features"] if f.get("properties", {}).get("kind") == "route"]
        if len(routes) != 1:
            raise ValueError("FeatureCollection must contain exactly one kind=route Feature")
        geometry, metadata = routes[0]["geometry"], routes[0].get("properties") or {}
    else:
        geometry, metadata = data, {}
    if geometry.get("type") != "LineString":
        raise ValueError("Expected one continuous GeoJSON LineString")
    return geometry["coordinates"], metadata


def save_route(path, coords, metadata):
    feature = {
        "type": "Feature",
        "properties": {"kind": "route", **metadata},
        "geometry": {"type": "LineString", "coordinates": np.asarray(coords).tolist()},
    }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(feature, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )


def fetch_osrm(start, end, *, base_url="https://router.project-osrm.org", cache_dir="cache"):
    for point in (start, end):
        validate_point(point)
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("OSRM base URL must be HTTP(S)")
    coord_string = ";".join(f"{lon:.7f},{lat:.7f}" for lon, lat in (start, end))
    query = urlencode(
        {"overview": "full", "geometries": "geojson", "steps": "false", "alternatives": "false"}
    )
    url = f"{base_url.rstrip('/')}/route/v1/driving/{coord_string}?{query}"
    key = hashlib.sha256(url.encode()).hexdigest()[:24]
    cache_file = Path(cache_dir) / f"osrm-{key}.json"
    if cache_file.exists():
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        payload, fetched_at = cached["response"], cached["fetched_at_utc"]
    else:
        request = Request(
            url, headers={"User-Agent": "CopilotRoadResearch/0.1", "Accept": "application/json"}
        )
        try:
            with urlopen(request, timeout=90) as response:
                payload = json.load(response)
        except (HTTPError, URLError, TimeoutError) as exc:
            raise ValueError(
                f"OSRM download failed: {exc}. Use a cached GeoJSON or own server."
            ) from exc
        if payload.get("code") != "Ok" or not payload.get("routes"):
            raise ValueError(f"OSRM could not find a route: {payload.get('code')}")
        fetched_at = datetime.now(UTC).isoformat()
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps({"fetched_at_utc": fetched_at, "response": payload}), encoding="utf-8"
        )
    route = payload["routes"][0]
    metadata = {
        "provider": "OSRM",
        "routing_objective": "provider_recommended_driving_route",
        "source_url": url,
        "fetched_at_utc": fetched_at,
        "requested_start_lonlat": list(start),
        "requested_end_lonlat": list(end),
        "snapped_waypoints": [
            {"lonlat": w["location"], "snap_distance_m": w.get("distance")}
            for w in payload.get("waypoints", [])
        ],
        "provider_distance_m": route["distance"],
        "provider_duration_s": route["duration"],
        "attribution": "© OpenStreetMap contributors; routing: OSRM",
    }
    return route["geometry"]["coordinates"], metadata


def assemble_osmnx_route(graph, nodes):
    """Respect directed traversal and choose the same minimum-length parallel edge."""
    if not nodes or len(nodes) < 2:
        raise ValueError("No non-zero route found between snapped nodes")
    coords = []
    for u, v in pairwise(nodes):
        edge = min(graph[u][v].values(), key=lambda e: e["length"])
        origin = np.array([graph.nodes[u]["x"], graph.nodes[u]["y"]])
        destination = np.array([graph.nodes[v]["x"], graph.nodes[v]["y"]])
        geometry = edge.get("geometry")
        points = np.asarray(geometry.coords if geometry is not None else [origin, destination])
        if np.linalg.norm(points[-1] - origin) < np.linalg.norm(points[0] - origin):
            points = points[::-1]
        if not np.allclose(points[0], origin, rtol=0, atol=1e-7):
            raise ValueError("Edge geometry does not start at its directed origin")
        if not np.allclose(points[-1], destination, rtol=0, atol=1e-7):
            raise ValueError("Edge geometry does not end at its directed destination")
        if coords and not np.allclose(coords[-1], points[0], rtol=0, atol=1e-7):
            raise ValueError("Discontinuous route geometry")
        coords.extend(points.tolist() if not coords else points[1:].tolist())
    return coords


def fetch_osmnx(start, end, *, buffer_m=2000, cache_dir="cache"):
    for point in (start, end):
        validate_point(point)
    if not np.isfinite(buffer_m) or buffer_m <= 0:
        raise ValueError("buffer_m must be finite and positive")
    geod = Geod(ellps="WGS84")
    _, _, distance = geod.inv(*start, *end)
    if distance > 30_000:
        raise ValueError(
            "OSMnx prototype supports local routes up to 30 km; use OSRM for this route"
        )
    try:
        import osmnx as ox
    except ImportError as exc:
        raise ValueError('Install the OSMnx adapter: pip install -e ".[osm]"') from exc
    ox.settings.use_cache = True
    ox.settings.requests_timeout = 60
    ox.settings.cache_folder = str(Path(cache_dir) / "overpass")
    bounds = [
        geod.fwd(*point, bearing, buffer_m)[:2]
        for point in (start, end)
        for bearing in (0, 90, 180, 270)
    ]
    bounds = np.asarray(bounds)
    bbox = (
        float(bounds[:, 0].min()),
        float(bounds[:, 1].min()),
        float(bounds[:, 0].max()),
        float(bounds[:, 1].max()),
    )
    key = hashlib.sha256(repr(bbox).encode()).hexdigest()[:24]
    graph_file = Path(cache_dir) / f"osmnx-{key}.graphml"
    if graph_file.exists():
        graph = ox.io.load_graphml(graph_file)
    else:
        graph = ox.graph.graph_from_bbox(bbox, network_type="drive", retain_all=True)
        graph_file.parent.mkdir(parents=True, exist_ok=True)
        ox.io.save_graphml(graph, graph_file)
    projected = ox.projection.project_graph(graph)
    transformer = Transformer.from_crs("EPSG:4326", projected.graph["crs"], always_xy=True)
    sx, sy = transformer.transform(*start)
    ex, ey = transformer.transform(*end)
    orig, orig_dist = ox.distance.nearest_nodes(projected, sx, sy, return_dist=True)
    dest, dest_dist = ox.distance.nearest_nodes(projected, ex, ey, return_dist=True)
    if max(orig_dist, dest_dist) > 1000:
        raise ValueError("Endpoint is more than 1 km from a drivable graph node")
    nodes = ox.routing.shortest_path(graph, orig, dest, weight="length")
    coords = assemble_osmnx_route(graph, nodes)
    return coords, {
        "provider": "OSMnx",
        "routing_objective": "shortest_length_within_downloaded_graph",
        "bbox_lonlat": list(bbox),
        "requested_start_lonlat": list(start),
        "requested_end_lonlat": list(end),
        "snap_distance_m": [float(orig_dist), float(dest_dist)],
        "attribution": "© OpenStreetMap contributors; routing: OSMnx",
        "limitations": "Node snapping; bounding box may exclude a better route; turn restrictions unresolved",
    }
