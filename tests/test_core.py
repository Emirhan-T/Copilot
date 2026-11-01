import numpy as np
import pytest
from pyproj import Transformer

from viraj.core import Config, analyze_lonlat, analyze_xy, detect_bends
from viraj.synthetic import arc, demo_road, make_road


@pytest.mark.parametrize("radius", [50, 100, 200, 500])
@pytest.mark.parametrize("sign", [-1, 1])
def test_known_circle_radius_and_direction(radius, sign):
    result = analyze_xy(arc(radius, sign=sign).xy)
    for signal in (result.curvature, result.circle_curvature):
        measured = np.median(signal[result.valid])
        assert measured == pytest.approx(sign / radius, rel=0.015)
    assert len(result.bends) == 1
    if result.bends:
        assert result.bends[0]["direction"] == ("left" if sign > 0 else "right")


@pytest.mark.parametrize("seed", range(5))
def test_noisy_straight_false_bends(seed):
    road = make_road([(1500, 0)], noise_m=0.15, seed=seed)
    assert analyze_xy(road.xy).bends == []


def test_noise_radius_estimate_and_composite_road():
    result = analyze_xy(arc(100, noise_m=0.15).xy)
    assert 1 / np.median(result.curvature[result.valid]) == pytest.approx(100, rel=0.10)
    composite = analyze_xy(demo_road(noise_m=0.15).xy)
    assert [b["direction"] for b in composite.bends] == ["left", "right"]
    assert [b["p50_radius_m"] for b in composite.bends] == pytest.approx([100, 60], rel=0.10)


def test_reversing_route_reverses_order_and_side():
    road = demo_road().xy
    forward, backward = analyze_xy(road), analyze_xy(road[::-1])
    assert len(forward.bends) == len(backward.bends) == 2
    for first, second in zip(forward.bends, backward.bends[::-1]):
        assert first["direction"] != second["direction"]
        assert first["min_radius_m"] == pytest.approx(second["min_radius_m"], rel=1e-5)
        assert first["start_s_m"] == pytest.approx(backward.s[-1] - second["end_s_m"])


def test_uniform_grid_preserves_endpoints_and_handles_duplicates():
    result = analyze_xy([[0, 0], [0, 0], [11, 0], [12, 0]])
    assert result.s[-1] == 12
    assert np.max(np.diff(result.s)) <= 5
    assert np.ptp(np.diff(result.s)) < 1e-10
    np.testing.assert_allclose(result.xy[[0, -1]], [[0, 0], [12, 0]])
    assert not result.valid.any()  # insufficient support, not evidence of a straight road


def test_large_utm_offset_does_not_change_curvature():
    xy = arc(100).xy
    a, b = analyze_xy(xy), analyze_xy(xy + [500_000, 4_200_000])
    np.testing.assert_allclose(a.curvature[a.valid], b.curvature[b.valid], atol=1e-8)


def test_turkey_utm_and_lonlat_order():
    xy = arc(100).xy + [450_000, 4_200_000]
    inverse = Transformer.from_crs(32636, 4326, always_xy=True)
    lon, lat = inverse.transform(*xy.T)
    coords = np.column_stack([lon, lat])
    result = analyze_lonlat(coords)
    assert result.crs == "EPSG:32636"
    np.testing.assert_allclose(result.lonlat[[0, -1]], coords[[0, -1]], atol=1e-8)
    assert result.bends[0]["direction"] == "left"
    assert result.bends[0]["min_radius_m"] == pytest.approx(100, rel=0.03)


@pytest.mark.parametrize("middle_sign,expected", [(0, 1), (-0.001, 2)])
def test_gap_merging_does_not_cross_opposite_curvature(middle_sign, expected):
    s = np.arange(0, 101, 5.0)
    kappa = np.zeros(len(s))
    kappa[2:8] = 0.01
    kappa[8] = middle_sign
    kappa[9:15] = 0.01
    bends = detect_bends(
        s,
        np.column_stack([s, np.zeros(len(s))]),
        kappa,
        np.ones(len(s), dtype=bool),
        Config(min_turn_deg=0),
    )
    assert len(bends) == expected


def test_adjacent_s_bends_are_not_merged():
    road = make_road([(100, 0), (120, 1 / 80), (120, -1 / 80), (100, 0)])
    result = analyze_xy(road.xy)
    assert [b["direction"] for b in result.bends] == ["left", "right"]


@pytest.mark.parametrize("points", [[], [[1, 2]], [[1, 2], [1, 2]], [[0, 0], [float("nan"), 1]]])
def test_bad_geometry(points):
    with pytest.raises(ValueError):
        analyze_xy(points)


@pytest.mark.parametrize(
    "kwargs",
    [{"spacing_m": 0}, {"smoothing_m": -1}, {"radius_threshold_m": float("inf")}, {"polyorder": 1}],
)
def test_bad_config(kwargs):
    with pytest.raises(ValueError):
        Config(**kwargs)


def test_wide_route_requires_local_projection_split():
    with pytest.raises(ValueError, match="split"):
        analyze_lonlat([[26, 38], [45, 40]])
