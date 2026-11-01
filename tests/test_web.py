"""The web workbench uses the existing core and never needs live network in tests."""

import csv
import io
from dataclasses import asdict

import numpy as np
import pytest
from pyproj import Transformer

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient

from viraj import Config, analyze_lonlat
from viraj.synthetic import demo_road
from viraj.web import create_app, signal_points


@pytest.fixture
def workbench(tmp_path):
    xy = demo_road().xy + [450000, 4200000]
    transformer = Transformer.from_crs(32636, 4326, always_xy=True)
    coords = np.column_stack(transformer.transform(*xy.T)).tolist()
    calls = []

    def route_fetcher(start, end):
        calls.append((start, end))
        return coords, {"provider": "test"}

    client = TestClient(create_app(cache_dir=tmp_path, route_fetcher=route_fetcher))
    return client, coords, calls


def request_route(client):
    response = client.post(
        "/api/routes", json={"start": {"lat": 37.9, "lon": 32.4}, "end": {"lat": 38, "lon": 32.5}}
    )
    assert response.status_code == 200
    return response.json()["route_id"]


def test_workbench_page_assets_and_defaults(workbench):
    client, _, _ = workbench
    page = client.get("/")
    assert page.status_code == 200 and "Viraj Lab" in page.text
    for path in ("/static/app.js", "/static/style.css", "/static/vendor/leaflet.js"):
        assert client.get(path).status_code == 200
    defaults = client.get("/api/defaults").json()
    assert all(value == asdict(Config())[key] for key, value in defaults["config"].items())
    assert defaults["presets"][0]["start"] == {"lat": 37.8714, "lon": 32.4846}


def test_routing_coordinate_order_and_memory_cache(workbench):
    client, _, calls = workbench
    first = request_route(client)
    assert request_route(client) == first
    assert calls == [((32.4, 37.9), (32.5, 38.0))]


def test_api_matches_core_and_reuses_route_for_different_angle(workbench):
    client, coords, calls = workbench
    route_id = request_route(client)
    response = client.post(
        "/api/analyze", json={"route_id": route_id, "config": {"min_turn_deg": 0.5}}
    )
    assert response.status_code == 200
    data = response.json()
    expected = analyze_lonlat(coords)
    assert data["summary"]["bends"] == expected.bends
    assert data["summary"]["config"] == asdict(Config())
    assert data["geojson"]["features"][0]["geometry"]["coordinates"] == coords
    filtered = client.post(
        "/api/analyze", json={"route_id": route_id, "config": {"min_turn_deg": 100}}
    )
    assert filtered.status_code == 200
    assert filtered.json()["summary"]["bend_count"] == 0
    assert len(calls) == 1
    cached = client.post(
        "/api/analyze", json={"route_id": route_id, "config": {"min_turn_deg": 0.5}}
    )
    assert cached.json() == data


def test_csv_has_one_header_and_all_samples(workbench):
    client, coords, _ = workbench
    route_id = request_route(client)
    data = client.post("/api/analyze", json={"route_id": route_id}).json()
    response = client.get(f"/api/analyses/{data['analysis_id']}/samples.csv")
    assert response.status_code == 200
    rows = list(csv.reader(io.StringIO(response.text)))
    assert len(rows) == len(analyze_lonlat(coords).s) + 1
    assert rows[0][0] == "s_m" and sum(row[0] == "s_m" for row in rows) == 1
    assert "attachment" in response.headers["content-disposition"]


def test_detail_plot_returns_samples_in_requested_distance_window(workbench):
    client, _, _ = workbench
    route_id = request_route(client)
    data = client.post("/api/analyze", json={"route_id": route_id}).json()
    path = f"/api/analyses/{data['analysis_id']}/signals"
    response = client.get(path, params={"start_s_m": 150, "end_s_m": 400})
    assert response.status_code == 200
    points = response.json()["signals"]["detection"]
    assert len(points) > 30 and all(150 <= p[0] <= 400 for p in points)
    assert client.get(path, params={"start_s_m": 400, "end_s_m": 150}).status_code == 422


def test_plot_downsampling_keeps_short_extreme_peaks():
    s = np.arange(20000, dtype=float)
    curvature = np.zeros(len(s))
    curvature[1237] = 2
    curvature[1241] = -3
    points = signal_points(s, curvature, np.ones(len(s), dtype=bool))
    assert len(points) <= 2400
    assert [1237.0, 2.0] in points and [1241.0, -3.0] in points


@pytest.mark.parametrize(
    "config",
    [
        {"min_turn_deg": -1},
        {"spacing_m": 0},
        {"merge_gap_m": -5},
        {"detection_sigma_m": 0},
        {"extra": 1},
    ],
)
def test_invalid_analysis_parameters_rejected(workbench, config):
    client, _, _ = workbench
    assert (
        client.post("/api/analyze", json={"route_id": "none", "config": config}).status_code == 422
    )


def test_invalid_and_coincident_endpoints_rejected_without_routing(workbench):
    client, _, calls = workbench
    for point in ({"lat": 95, "lon": 32}, {"lat": 38, "lon": 200}, {"lat": 38, "lon": 32}):
        response = client.post("/api/routes", json={"start": point, "end": point})
        assert response.status_code == 422
    assert not calls


def test_unknown_route_and_analysis_have_clear_errors(workbench):
    client, _, _ = workbench
    assert client.post("/api/analyze", json={"route_id": "expired"}).status_code == 404
    assert client.get("/api/analyses/expired/samples.csv").status_code == 404


def test_routing_service_failure_is_reported_without_an_analysis(tmp_path):
    def fail(*args):
        raise ValueError("offline")

    client = TestClient(create_app(cache_dir=tmp_path, route_fetcher=fail))
    response = client.post(
        "/api/routes", json={"start": {"lat": 38, "lon": 32}, "end": {"lat": 39, "lon": 33}}
    )
    assert response.status_code == 502 and "Rota alınamadı" in response.json()["detail"]


def test_local_api_rejects_cross_origin_mutation(workbench):
    client, _, calls = workbench
    response = client.post(
        "/api/routes",
        headers={"origin": "https://example.com"},
        json={"start": {"lat": 38, "lon": 32}, "end": {"lat": 39, "lon": 33}},
    )
    assert response.status_code == 403 and not calls
