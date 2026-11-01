import io
import json
from pathlib import Path
from urllib.error import URLError

import jsonschema
import numpy as np
import pytest
from pyproj import Transformer

from viraj.cli import main
from viraj.core import analyze_lonlat
from viraj.export import export_analysis
from viraj.routing import assemble_osmnx_route, fetch_osrm, load_route, save_route
from viraj.synthetic import demo_road


def geographic_demo():
    xy = demo_road().xy + [450_000, 4_200_000]
    transformer = Transformer.from_crs(32636, 4326, always_xy=True)
    return np.column_stack(transformer.transform(*xy.T))


def test_json_schema_geojson_and_export(tmp_path):
    result = analyze_lonlat(geographic_demo(), metadata={"title": "Test route"})
    export_analysis(result, tmp_path)
    data = json.loads((tmp_path / "analysis.json").read_text(encoding="utf-8"))
    schema = json.loads((Path(__file__).parents[1] / "schemas/analysis.schema.json").read_text())
    jsonschema.validate(data, schema)
    assert data["bend_count"] == len(data["bends"]) == 2
    for bend in data["bends"]:
        assert bend["length_m"] == pytest.approx(bend["end_s_m"] - bend["start_s_m"])
        assert bend["min_radius_m"] <= bend["p50_radius_m"]
        assert bend["start_s_m"] <= bend["apex_s_m"] <= bend["end_s_m"]
    features = json.loads((tmp_path / "bends.geojson").read_text(encoding="utf-8"))["features"]
    assert len(features) == 3
    for feature, bend in zip(features[1:], data["bends"]):
        assert feature["geometry"]["coordinates"][0] == bend["start_lonlat"]
        assert feature["geometry"]["coordinates"][-1] == bend["end_lonlat"]
    coords, _ = load_route(tmp_path / "bends.geojson")
    np.testing.assert_allclose(coords, result.lonlat)
    page = (tmp_path / "map.html").read_text(encoding="utf-8")
    assert "Test route" in page and "fitBounds" in page
    assert (tmp_path / "analysis.png").stat().st_size > 1000


def test_offline_cli_roundtrip(tmp_path):
    path = tmp_path / "route.geojson"
    save_route(path, geographic_demo(), {"provider": "test"})
    assert main(["analyze", str(path), "--out", str(tmp_path / "report")]) == 0
    coords, metadata = load_route(tmp_path / "report/route_input.geojson")
    np.testing.assert_allclose(coords, geographic_demo())
    assert metadata["provider"] == "test"


def test_cli_uses_sensitive_defaults_and_exports_point_and_comparison(tmp_path):
    coords = geographic_demo()
    path = tmp_path / "route.geojson"
    save_route(path, coords, {"provider": "test"})
    baseline_dir = tmp_path / "baseline"
    export_analysis(analyze_lonlat(coords), baseline_dir)
    folder = tmp_path / "report"
    assert (
        main(
            [
                "analyze",
                str(path),
                "--out",
                str(folder),
                "--compare-to",
                str(baseline_dir / "bends.geojson"),
                "--check-point",
                *map(str, coords[100]),
            ]
        )
        == 0
    )
    data = json.loads((folder / "analysis.json").read_text(encoding="utf-8"))
    assert data["config"] == analyze_lonlat(coords).summary()["config"]
    assert data["metadata"]["review_points"][0]["distance_to_route_m"] < 1
    page = (folder / "map.html").read_text(encoding="utf-8")
    assert "Bildirilen nokta" in page
    # Folium serializes layer names as JSON strings with escaped Unicode.
    assert json.dumps("Güncel virajlar") in page
    assert json.dumps("Önceki tespitler (v1)") in page


def test_osrm_cache_avoids_second_network_call(tmp_path, monkeypatch):
    from viraj import routing

    calls = []
    payload = {
        "code": "Ok",
        "routes": [
            {
                "distance": 100,
                "duration": 20,
                "geometry": {"coordinates": [[32, 38], [32.001, 38.001]]},
            }
        ],
        "waypoints": [],
    }

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(routing, "urlopen", fake_urlopen)
    first = fetch_osrm((32, 38), (32.001, 38.001), cache_dir=tmp_path)
    second = fetch_osrm((32, 38), (32.001, 38.001), cache_dir=tmp_path)
    assert first == second
    assert len(calls) == 1
    assert "overview=full" in calls[0] and "geometries=geojson" in calls[0]


def test_failed_osrm_is_not_cached(tmp_path, monkeypatch):
    from viraj import routing

    def fail(*args, **kwargs):
        raise URLError("offline")

    monkeypatch.setattr(routing, "urlopen", fail)
    with pytest.raises(ValueError, match="download failed"):
        fetch_osrm((32, 38), (33, 39), cache_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_osmnx_directed_geometry_and_parallel_edge_selection():
    nx = pytest.importorskip("networkx")
    geometry = pytest.importorskip("shapely.geometry")
    graph = nx.MultiDiGraph()
    graph.add_node(1, x=32, y=38)
    graph.add_node(2, x=32.001, y=38.001)
    graph.add_node(3, x=32.002, y=38.001)
    graph.add_edge(
        1, 2, length=300, geometry=geometry.LineString([(32, 38), (32.003, 38), (32.001, 38.001)])
    )
    graph.add_edge(
        1,
        2,
        length=100,
        geometry=geometry.LineString([(32.001, 38.001), (32.0005, 38.0004), (32, 38)]),
    )
    graph.add_edge(2, 3, length=100)  # no geometry: retain both nodes
    coords = assemble_osmnx_route(graph, [1, 2, 3])
    assert coords == [[32, 38], [32.0005, 38.0004], [32.001, 38.001], [32.002, 38.001]]
