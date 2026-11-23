import json
from pathlib import Path

import jsonschema
import numpy as np
import pytest
from pyproj import Transformer

from viraj.cli import main
from viraj.core import Config, analyze_lonlat, analyze_xy
from viraj.export import export_analysis
from viraj.routing import save_route
from viraj.speed import (
    AASHTO_FMAX_MPH,
    MPH_TO_KMH,
    OverspeedMonitor,
    SpeedConfig,
    SpeedProfile,
    assess_speeds,
    curve_speed_kmh,
    side_friction,
    warning_distance_m,
)
from viraj.synthetic import make_road

G = 9.81


def road_with_arc(radius, degrees, *, noise=0.0, straight=400):
    length = radius * np.radians(degrees)
    return make_road([(straight, 0), (length, 1 / radius), (straight, 0)], noise_m=noise)


def test_side_friction_matches_aashto_table_and_clamps():
    for mph, f in AASHTO_FMAX_MPH:
        assert side_friction(mph * MPH_TO_KMH) == pytest.approx(f)
    assert side_friction(5) == pytest.approx(0.32)
    assert side_friction(200) == pytest.approx(0.08)
    speeds = np.linspace(10, 160, 50)
    assert np.all(np.diff(side_friction(speeds)) <= 0)


@pytest.mark.parametrize("radius", [30, 100, 250, 600])
@pytest.mark.parametrize("e", [0.0, 0.06])
def test_curve_speed_solves_point_mass_equation(radius, e):
    v_kmh = curve_speed_kmh(radius, superelevation=e)
    v = v_kmh / 3.6
    assert v * v / (G * radius) == pytest.approx(e + side_friction(v_kmh), rel=1e-6)


def test_curve_speed_monotonic_and_capped():
    radii = [20, 50, 100, 200, 400, 800]
    speeds = [curve_speed_kmh(r) for r in radii]
    assert speeds == sorted(speeds)
    assert curve_speed_kmh(200, superelevation=0.08) > curve_speed_kmh(200)
    assert curve_speed_kmh(200, friction_scale=0.5) < curve_speed_kmh(200)
    assert curve_speed_kmh(1e6, cap_kmh=90) == 90
    assert curve_speed_kmh(np.inf, cap_kmh=90) == 90


def test_warning_distance_formula():
    # 90 -> 50 km/h: 25 m/s * 2.5 s + (25^2 - 13.89^2) / (2 * 3.4)
    expected = 25 * 2.5 + (25**2 - (50 / 3.6) ** 2) / (2 * 3.4)
    assert warning_distance_m(90, 50) == pytest.approx(expected)
    assert warning_distance_m(90, 90) == pytest.approx(25 * 2.5)
    assert warning_distance_m(90, 120) == pytest.approx(25 * 2.5)  # never "accelerate"


@pytest.mark.parametrize("radius", [60, 120, 250])
def test_known_arc_radius_target_and_warning_point(radius):
    result = analyze_xy(road_with_arc(radius, 90).xy)
    speed = assess_speeds(result, SpeedConfig())
    assert speed["series_count"] == 1
    record = next(r for r in speed["bends"] if r["requires_slowdown"])
    assert record["radius_m"] == pytest.approx(radius, rel=0.12)
    assert record["confidence"] == "high"
    assert record["target_speed_kmh"] == pytest.approx(curve_speed_kmh(radius), rel=0.06)
    series = speed["series"][0]
    assert series["bend_ids"] == [record["bend_id"]] and record["series_id"] == 1
    # Allowed speed starts to fall where braking at 3.4 m/s^2 from 87 km/h
    # (approach minus tolerance) just reaches the target at the core start.
    vt, v_tol = record["target_speed_kmh"] / 3.6, 87 / 3.6
    braking = (v_tol**2 - vt**2) / (2 * 3.4)
    assert series["slowdown_start_s_m"] == pytest.approx(record["core_start_s_m"] - braking, abs=6)
    assert series["trigger_s_at_approach_m"] == pytest.approx(
        max(0, series["slowdown_start_s_m"] - 25 * 2.5), abs=0.2
    )


def test_speed_radius_does_not_follow_smoothing():
    xy = road_with_arc(150, 60, noise=0.15).xy
    radii = []
    for smoothing in (25, 45, 85):
        result = analyze_xy(xy, Config(smoothing_m=smoothing))
        speed = assess_speeds(result)
        radii.append(next(r for r in speed["bends"] if r["requires_slowdown"])["radius_m"])
    assert max(radii) / min(radii) < 1.02


def test_straight_and_tiny_deflections_do_not_warn():
    straight = analyze_xy(make_road([(2000, 0)], noise_m=0.15, seed=3).xy)
    assert assess_speeds(straight)["series_count"] == 0
    # Sparse 2 degree polyline kink: only the vertex angle is known.
    angle = np.radians(2)
    kink = np.array([[0, 0], [600, 0], [600 + 600 * np.cos(angle), 600 * np.sin(angle)]])
    result = analyze_xy(kink)
    assert result.bends
    speed = assess_speeds(result)
    assert speed["series_count"] == 0
    assert all(r["confidence"] == "low" for r in speed["bends"])


def test_lane_slack_flattens_a_sharp_small_deflection():
    # Densely digitized 6 degree kink with a narrow detector: the geometric
    # core radius is ~100 m, but a car can flatten it within 0.5 m of the lane.
    angle = np.radians(6)
    first = np.column_stack([np.arange(0, 400.1, 2), np.zeros(201)])
    t = np.arange(2, 400.1, 2)
    second = first[-1] + np.column_stack([t * np.cos(angle), t * np.sin(angle)])
    result = analyze_xy(np.vstack([first, second]), Config(detection_sigma_m=5))
    record = max(assess_speeds(result)["bends"], key=lambda r: abs(r["core_turn_deg"]))
    assert record["radius_basis"] == "lane_slack"
    assert record["heading_core_radius_m"] < record["lane_slack_radius_m"]
    assert record["lane_slack_radius_m"] == pytest.approx(8 * 0.5 / angle**2, rel=0.1)


def test_higher_approach_speed_or_lower_friction_adds_warnings():
    result = analyze_xy(road_with_arc(500, 40).xy)
    assert assess_speeds(result, SpeedConfig(approach_speed_kmh=80))["series_count"] == 0
    assert assess_speeds(result, SpeedConfig(approach_speed_kmh=120))["series_count"] == 1
    assert (
        assess_speeds(result, SpeedConfig(approach_speed_kmh=80, friction_scale=0.4))[
            "series_count"
        ]
        == 1
    )


def test_warning_point_clipped_at_route_start():
    result = analyze_xy(road_with_arc(40, 90, straight=60).xy)
    series = assess_speeds(result)["series"][0]
    assert series["trigger_s_at_approach_m"] == 0
    assert series["trigger_clipped_to_route_start"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"approach_speed_kmh": 0},
        {"superelevation": 0.2},
        {"deceleration_mps2": -1},
        {"core_fraction": 1},
        {"lane_slack_m": -0.1},
        {"min_circle_vertices": 2},
    ],
)
def test_speed_config_validation(kwargs):
    with pytest.raises(ValueError):
        SpeedConfig(**kwargs)


def geographic(xy):
    transformer = Transformer.from_crs(32636, 4326, always_xy=True)
    return np.column_stack(transformer.transform(*(xy + [450_000, 4_200_000]).T))


def test_schema_export_and_cli(tmp_path):
    coords = geographic(road_with_arc(80, 90).xy)
    result = analyze_lonlat(coords, metadata={"title": "Speed test"})
    assess_speeds(result)
    export_analysis(result, tmp_path / "out")
    data = json.loads((tmp_path / "out/analysis.json").read_text(encoding="utf-8"))
    schema = json.loads((Path(__file__).parents[1] / "schemas/analysis.schema.json").read_text())
    jsonschema.validate(data, schema)
    assert data["speed"]["series_count"] == 1
    assert "trigger_lonlat" in data["speed"]["series"][0]
    page = (tmp_path / "out/map.html").read_text(encoding="utf-8")
    assert "circle_marker" in page and "uyarı serisi" in page

    path = tmp_path / "route.geojson"
    save_route(path, coords, {"provider": "test"})
    assert (
        main(["analyze", str(path), "--out", str(tmp_path / "cli"), "--approach-speed", "50"]) == 0
    )
    cli = json.loads((tmp_path / "cli/analysis.json").read_text(encoding="utf-8"))
    assert cli["speed"]["config"]["approach_speed_kmh"] == 50
    assert main(["analyze", str(path), "--out", str(tmp_path / "plain"), "--no-speed"]) == 0
    assert "speed" not in json.loads((tmp_path / "plain/analysis.json").read_text("utf-8"))


def two_bend_road(gap_m, radius=60):
    arc = radius * np.pi / 2
    return make_road([(500, 0), (arc, 1 / radius), (gap_m, 0), (arc, -1 / radius), (500, 0)])


def test_close_bends_form_one_series_and_far_bends_two():
    close = assess_speeds(analyze_xy(two_bend_road(60).xy))
    assert close["slowdown_bend_count"] == 2
    assert close["series_count"] == 1 and len(close["series"][0]["bend_ids"]) == 2
    assert "2 viraj arka arkaya" in close["series"][0]["message"]
    far = assess_speeds(analyze_xy(two_bend_road(1500).xy))
    assert far["series_count"] == 2
    assert all(len(item["bend_ids"]) == 1 for item in far["series"])


def test_series_names_the_sharpest_bend_not_the_first():
    road = make_road(
        [(500, 0), (100 * np.pi / 2, 1 / 100), (50, 0), (30 * np.pi / 2, -1 / 30), (500, 0)]
    )
    speed = assess_speeds(analyze_xy(road.xy))
    series = speed["series"][0]
    targets = {r["bend_id"]: r["target_speed_kmh"] for r in speed["bends"]}
    assert series["min_target_kmh"] == min(targets[i] for i in series["bend_ids"])
    assert series["critical_bend_id"] == series["bend_ids"][-1]


def test_compact_envelope_rebuilds_the_profile_safely():
    result = analyze_xy(two_bend_road(60).xy)
    speed = assess_speeds(result)
    full = result.speed_profile
    compact = SpeedProfile.from_series(speed["series"], 90)
    start, end = speed["series"][0]["envelope"][0][0], speed["series"][0]["envelope"][-1][0]
    inside = (full.s >= start) & (full.s <= end)
    difference = compact.allowed_at(full.s[inside]) - full.allowed_kmh[inside]
    assert difference.max() <= 0.06  # rounding only; never meaningfully more permissive
    assert difference.min() >= -2


def drive(result, speed, policy, *, start_kmh=90, dt=0.5, profile=None):
    profile = profile or result.speed_profile
    monitor = OverspeedMonitor(profile, speed["series"])
    s, v, t, events, alerted = 0.0, float(start_kmh), 0.0, [], None
    while s < result.s[-1]:
        event = monitor.update(s, v, t)
        if event:
            events.append(event)
            alerted = alerted if alerted is not None else t
        if policy == "follow":  # anticipates the profile slightly beyond the reaction distance
            v = min(start_kmh, profile.minimum_between(s, s + v / 3.6 * 3.5))
        elif policy == "first_bend_only" and alerted is not None and t - alerted >= 1:
            hold = speed["bends"][0]["target_speed_kmh"]
            v = max(hold, v - 3.0 * 3.6 * dt) if v > hold else v
        s += v / 3.6 * dt
        t += dt
    return [e["kind"] for e in events]


def test_monitor_warns_only_when_speed_is_exceeded():
    road = make_road(
        [(600, 0), (100 * np.pi / 2, 1 / 100), (50, 0), (30 * np.pi / 2, -1 / 30), (600, 0)]
    )
    result = analyze_xy(road.xy)
    speed = assess_speeds(result)
    assert speed["series_count"] == 1
    assert drive(result, speed, "constant", start_kmh=25) == []
    assert drive(result, speed, "follow") == []
    ignoring = drive(result, speed, "constant")
    assert ignoring[0] == "series" and set(ignoring[1:]) <= {"reminder"}
    # Slows for the first bend only: one series alert, then one step-down alert.
    assert drive(result, speed, "first_bend_only")[:2] == ["series", "step"]
    compact = SpeedProfile.from_series(speed["series"], 90)
    assert drive(result, speed, "constant", profile=compact) == ignoring


def test_bend_targets_are_not_capped_by_reference_speed():
    gentle = analyze_xy(road_with_arc(1500, 30).xy)
    record = max(assess_speeds(gentle)["bends"], key=lambda r: abs(r["core_turn_deg"]))
    assert record["target_speed_kmh"] > 90  # its own upper limit, not the reference
    assert record["target_speed_kmh"] == pytest.approx(
        curve_speed_kmh(record["radius_m"], cap_kmh=200), abs=0.06
    )
    assert not record["requires_slowdown"] and not record["target_is_unbounded"]
    straight_kink = analyze_xy(np.array([[0, 0], [600, 0], [1200, 600 * np.tan(np.radians(0.6))]]))
    assert all(r["target_is_unbounded"] for r in assess_speeds(straight_kink)["bends"])
