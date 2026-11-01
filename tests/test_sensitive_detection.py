"""Regression tests for missed gentle bends and fragmented same-side bends."""

from pathlib import Path

import numpy as np
import pytest

from viraj import Config, analyze_lonlat, analyze_xy
from viraj.core import detect_bends
from viraj.inspection import check_point
from viraj.routing import load_route
from viraj.synthetic import make_road


@pytest.mark.parametrize("radius", [500, 1000, 5000, 20000, 30000])
@pytest.mark.parametrize("sign", [-1, 1])
def test_wide_gentle_bends_are_detected(radius, sign):
    road = make_road([(200, 0), (600, sign / radius), (200, 0)])
    result = analyze_xy(road.xy)
    assert len(result.bends) == 1
    bend = result.bends[0]
    assert bend["direction"] == ("left" if sign > 0 else "right")
    # Finite grid + hysteresis truncates the very shallow tails of the transition.
    assert bend["turn_angle_deg"] == pytest.approx(np.degrees(sign * 600 / radius), abs=0.1)


@pytest.mark.parametrize("step", [25, 75, 150])
def test_sparse_arc_is_one_bend_instead_of_corner_spikes(step):
    road = make_road([(200, 0), (600, 1 / 1000), (200, 0)], step_m=step)
    result = analyze_xy(road.xy)
    assert len(result.bends) == 1
    assert result.bends[0]["turn_angle_deg"] == pytest.approx(np.degrees(0.6), abs=0.1)
    assert result.bends[0]["length_m"] > 600


def test_same_direction_across_short_straight_is_merged():
    road = make_road([(200, 0), (200, 0.001), (150, 0), (200, 0.001), (200, 0)])
    result = analyze_xy(road.xy)
    assert len(result.bends) == 1
    bend = result.bends[0]
    assert bend["merged_part_count"] == 2
    assert bend["turn_angle_deg"] == pytest.approx(np.degrees(0.4), abs=0.1)
    assert np.isfinite(bend["p50_radius_m"])


def test_long_straight_keeps_same_side_bends_separate():
    road = make_road([(200, 0), (200, 0.001), (600, 0), (200, 0.001), (200, 0)])
    result = analyze_xy(road.xy)
    assert [b["direction"] for b in result.bends] == ["left", "left"]


def test_gentle_s_bend_keeps_both_sides():
    road = make_road([(200, 0), (200, 1 / 5000), (150, -1 / 5000), (200, 0)])
    result = analyze_xy(road.xy)
    assert [b["direction"] for b in result.bends] == ["left", "right"]


def test_small_reverse_wiggle_can_be_bridged_but_a_real_reverse_turn_cannot():
    s = np.arange(0, 501, 5.0)
    xy = np.column_stack([s, np.zeros(len(s))])
    curvature = np.zeros(len(s))
    curvature[20:40] = curvature[45:65] = 0.001
    curvature[40:45] = -0.00005  # ~0.06°, below the opposite-turn budget
    valid = np.ones(len(s), dtype=bool)
    bends = detect_bends(s, xy, curvature, valid, Config())
    assert len(bends) == 1 and bends[0]["merged_part_count"] == 2
    curvature[40:45] = -0.001  # >1°: a real reverse bend must survive
    bends = detect_bends(s, xy, curvature, valid, Config())
    assert [b["direction"] for b in bends] == ["left", "right", "left"]


def test_hysteresis_extends_seed_into_weak_shoulders_but_requires_seed():
    s = np.arange(0, 201, 5.0)
    xy = np.column_stack([s, np.zeros(len(s))])
    curvature = np.zeros(len(s))
    curvature[4:36] = 0.0004
    curvature[12:28] = 0.002
    config = Config(radius_threshold_m=1000)
    valid = np.ones(len(s), dtype=bool)
    bends = detect_bends(s, xy, curvature, valid, config)
    assert len(bends) == 1
    assert bends[0]["start_s_m"] == s[4]
    assert bends[0]["end_s_m"] == s[35]
    curvature[12:28] = 0.0004
    assert detect_bends(s, xy, curvature, valid, config) == []


@pytest.mark.parametrize("seed", [100, 117, 130, 177, 199])
def test_sensitive_detection_rejects_held_out_noisy_straights(seed):
    road = make_road([(1500, 0)], noise_m=0.15, seed=seed)
    assert analyze_xy(road.xy).bends == []


def test_reported_konya_location_is_next_to_one_wide_left_bend():
    path = Path(__file__).parent / "fixtures/konya-reported-bend.geojson"
    coords, metadata = load_route(path)
    analysis = analyze_lonlat(coords)
    review = check_point(analysis, metadata["reported_point_lonlat"])
    assert review["distance_to_route_m"] < 15
    assert review["nearest_bend_offset_m"] < 25  # boundary inferred on sparse input
    bend = analysis.bends[review["nearest_bend_id"] - 1]
    assert bend["direction"] == "left"
    assert 40 < bend["turn_angle_deg"] < 60
    assert bend["length_m"] > 800
    assert bend["source_max_segment_m"] > 1000
    assert bend["detection_min_radius_m"] > 300


@pytest.mark.parametrize(
    "kwargs",
    [
        {"release_ratio": 0},
        {"release_ratio": 1.1},
        {"detection_sigma_m": 0},
        {"heading_transition_cap_m": -1},
        {"opposite_tolerance_deg": -1},
    ],
)
def test_invalid_detection_configuration(kwargs):
    with pytest.raises(ValueError):
        Config(**kwargs)
