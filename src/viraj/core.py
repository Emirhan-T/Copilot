"""Signed curvature in a local metric plane and distance-based bend extraction."""

from dataclasses import asdict, dataclass, field
from itertools import pairwise
from math import ceil, floor

import numpy as np
from pyproj import CRS, Transformer
from scipy.ndimage import gaussian_filter1d
from scipy.signal import savgol_filter


@dataclass(frozen=True)
class Config:
    spacing_m: float = 5.0
    smoothing_m: float = 45.0
    polyorder: int = 3
    radius_threshold_m: float = 50_000.0
    min_length_m: float = 15.0
    merge_gap_m: float = 100.0
    min_turn_deg: float = 0.5
    circle_span_m: float = 10.0
    detection_sigma_m: float = 20.0
    heading_transition_cap_m: float = 150.0
    release_ratio: float = 0.25
    opposite_tolerance_deg: float = 0.15

    def __post_init__(self):
        for name in (
            "spacing_m",
            "radius_threshold_m",
            "min_length_m",
            "circle_span_m",
            "detection_sigma_m",
            "heading_transition_cap_m",
        ):
            if not np.isfinite(getattr(self, name)) or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be finite and positive")
        for name in ("smoothing_m", "merge_gap_m", "min_turn_deg", "opposite_tolerance_deg"):
            if not np.isfinite(getattr(self, name)) or getattr(self, name) < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if not isinstance(self.polyorder, int) or self.polyorder < 2:
            raise ValueError("polyorder must be an integer >= 2")
        if not np.isfinite(self.release_ratio) or not 0 < self.release_ratio <= 1:
            raise ValueError("release_ratio must be in (0,1]")


@dataclass
class Analysis:
    config: Config
    s: np.ndarray
    xy: np.ndarray
    smooth_xy: np.ndarray
    curvature: np.ndarray
    circle_curvature: np.ndarray
    valid: np.ndarray
    detection_curvature: np.ndarray
    detection_valid: np.ndarray
    bends: list[dict]
    crs: str
    lonlat: np.ndarray | None
    quality: dict
    metadata: dict = field(default_factory=dict)

    def summary(self) -> dict:
        return {
            "schema_version": "1.1.0",
            "detector": "gaussian_heading_hysteresis",
            "coordinate_order": "longitude,latitude",
            "metric_crs": self.crs,
            "distance_axis": "original_projected_route_arclength_m",
            "curvature_unit": "1/m",
            "positive_curvature": "left_relative_to_travel",
            "route_length_m": float(self.s[-1]),
            "actual_spacing_m": float(self.s[1] - self.s[0]),
            "config": asdict(self.config),
            "quality": self.quality,
            "metadata": self.metadata,
            "bend_count": len(self.bends),
            "bends": self.bends,
        }


def clean_points(points) -> np.ndarray:
    xy = np.asarray(points, dtype=float)
    if xy.ndim != 2 or xy.shape[1] != 2 or len(xy) < 2 or not np.isfinite(xy).all():
        raise ValueError("At least two finite 2D coordinates are required")
    keep = np.r_[True, np.linalg.norm(np.diff(xy, axis=0), axis=1) > 1e-8]
    xy = xy[keep]
    if len(xy) < 2:
        raise ValueError("Route has zero length")
    return xy


def resample(xy: np.ndarray, spacing_m: float) -> tuple[np.ndarray, np.ndarray]:
    """Include both endpoints with a uniform step <= requested spacing.

    Using a short final interval would violate Savitzky-Golay's uniform-grid assumption.
    """
    source_s = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(xy, axis=0), axis=1))]
    intervals = max(ceil(float(source_s[-1]) / spacing_m), 2)
    if intervals > 2_000_000:
        raise ValueError("Too many samples; increase spacing_m or split the route")
    s = np.linspace(0.0, float(source_s[-1]), intervals + 1)
    sampled = np.column_stack([np.interp(s, source_s, xy[:, axis]) for axis in (0, 1)])
    return s, sampled


def curvature_estimates(s, xy, config):
    ds = float(s[1] - s[0])
    window = max(3, round(config.smoothing_m / ds) + 1)
    window += 1 - window % 2
    window = min(window, len(s) if len(s) % 2 else len(s) - 1)
    if config.smoothing_m == 0 or window <= config.polyorder:
        window = 1
        smoothed = xy.copy()
    else:
        # Center first to avoid losing precision on UTM northings in polynomial fits.
        smoothed = (
            savgol_filter(xy - xy[0], window, config.polyorder, axis=0, mode="interp") + xy[0]
        )
    velocity = np.gradient(smoothed, s, axis=0, edge_order=2)
    speed = np.linalg.norm(velocity, axis=1)
    heading = np.unwrap(np.arctan2(velocity[:, 1], velocity[:, 0]))
    # s follows the input route; after smoothing it is not exactly the new arclength.
    curvature = np.divide(
        np.gradient(heading, s, edge_order=2),
        speed,
        out=np.zeros_like(s),
        where=speed > 1e-6,
    )
    stride = max(1, round(config.circle_span_m / ds))
    circle = np.full(len(s), np.nan)
    if 2 * stride < len(s):
        a = smoothed[stride:-stride] - smoothed[: -2 * stride]
        b = smoothed[2 * stride :] - smoothed[stride:-stride]
        chord = smoothed[2 * stride :] - smoothed[: -2 * stride]
        denom = np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1)
        denom *= np.linalg.norm(chord, axis=1)
        cross = a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]
        circle[stride:-stride] = np.divide(
            2 * cross, denom, out=np.zeros(len(cross)), where=denom > 1e-12
        )
    # Endpoint derivatives and windows near them are deliberately not classified.
    guard = max(window // 2 + 2, stride)
    valid = (np.arange(len(s)) >= guard) & (np.arange(len(s)) < len(s) - guard)
    valid &= (speed > 1e-6) & np.isfinite(curvature) & np.isfinite(circle)
    return smoothed, curvature, circle, valid, window, guard


def average_heading_on_grid(s, anchor_s, anchor_h):
    """Exact cell averages of a piecewise-linear heading (anti-alias before sampling).

    Point-sampling noisy, dense segment bearings on a coarser grid aliases their
    alternating signs into false turns. Integrate the interpolation over each
    output cell instead; no extra road vertices or detail are assumed.
    """
    anchor_s = np.r_[s[0], anchor_s, s[-1]]
    anchor_h = np.r_[anchor_h[0], anchor_h, anchor_h[-1]]
    anchor_h = anchor_h - anchor_h[0]
    widths = np.diff(anchor_s)
    slopes = np.diff(anchor_h) / widths
    areas = np.r_[0.0, np.cumsum((anchor_h[:-1] + anchor_h[1:]) * widths / 2)]

    def integral_at(points):
        intervals = np.clip(np.searchsorted(anchor_s, points, side="right") - 1, 0, len(widths) - 1)
        offset = points - anchor_s[intervals]
        return areas[intervals] + anchor_h[intervals] * offset + slopes[intervals] * offset**2 / 2

    half = float(s[1] - s[0]) / 2
    left, right = np.maximum(s[0], s - half), np.minimum(s[-1], s + half)
    return (integral_at(right) - integral_at(left)) / (right - left)


def detection_signal(s, xy, config, source=None):
    """Smooth unwrapped *input* heading, avoiding SG's artificial reverse lobes.

    The derivative is per input-route metre, so its integral is a heading angle.
    This signal decides bend extent; the two existing spatial-curvature estimators
    remain separate for radius comparison.
    """
    ds = float(s[1] - s[0])
    source = xy if source is None else source
    edges = np.diff(source, axis=0)
    lengths = np.linalg.norm(edges, axis=1)
    source_s = np.r_[0.0, np.cumsum(lengths)]
    # A segment bearing represents its midpoint, rather than an instantaneous
    # turn at its last vertex. Long straight segments get two equal-heading
    # anchors, bounding the inferred transition on each end (never an unbounded
    # ramp across a kilometre-long straight). This is a boundary model, not new data.
    offset = np.minimum(lengths / 2, config.heading_transition_cap_m)
    anchor_s = np.column_stack([source_s[:-1] + offset, source_s[1:] - offset]).ravel()
    unique = np.r_[True, np.diff(anchor_s) > 1e-8]
    anchor_s = anchor_s[unique]
    # Use a metric chord around each anchor. Two-metre source digitization noise
    # must not become a noisy heading before smoothing; sparse long edges retain
    # their original bearing. The chord's total span equals the detection sigma.
    half_chord = max(ds, config.detection_sigma_m / 2)
    before = np.column_stack(
        [np.interp(anchor_s - half_chord, source_s, source[:, a]) for a in (0, 1)]
    )
    after = np.column_stack(
        [np.interp(anchor_s + half_chord, source_s, source[:, a]) for a in (0, 1)]
    )
    chord = after - before
    degenerate = np.linalg.norm(chord, axis=1) < 1e-8
    chord[degenerate] = np.repeat(edges, 2, axis=0)[unique][degenerate]
    anchor_h = np.unwrap(np.arctan2(chord[:, 1], chord[:, 0]))
    heading = average_heading_on_grid(s, anchor_s, anchor_h)
    sigma = config.detection_sigma_m / ds
    if sigma < 0.5:
        # Finite differences remain well-defined on coarse grids.
        filtered = gaussian_filter1d(heading - heading[0], max(sigma, 0.1), mode="nearest")
        signal = np.gradient(filtered, s, edge_order=2)
    else:
        signal = (
            gaussian_filter1d(heading - heading[0], sigma, order=1, mode="nearest", truncate=4.0)
            / ds
        )
    # 2σ boundary guard preserves short sharp routes; the remaining Gaussian
    # boundary influence decays inward. It is reported separately from SG support.
    guard = max(2, ceil(2 * config.detection_sigma_m / ds) + 1)
    indices = np.arange(len(s))
    valid = (indices >= guard) & (indices < len(s) - guard)
    return signal, valid, guard


def detect_bends(s, xy, curvature, valid, config, lonlat=None, *, signal=None):
    # Optional independent detection signal lets callers retain spatial radius
    # estimators without using their SG ringing to segment the route.
    signal = curvature if signal is None else signal
    high = 1 / config.radius_threshold_m
    low = high * config.release_ratio
    labels = np.where(
        valid & (np.abs(signal) >= low),
        np.sign(signal),
        0,
    ).astype(int)
    changes = np.r_[0, np.flatnonzero(np.diff(labels) != 0) + 1, len(labels)]
    runs = [
        (a, b - 1, int(labels[a]))
        for a, b in pairwise(changes)
        if labels[a] != 0 and np.max(np.abs(signal[a:b])) >= high
    ]
    merged = []
    # Work per side: negligible reverse wiggles may lie between two left (or
    # right) runs. Integrating the *whole* gap prevents swallowing an actual S.
    opposite_limit = min(config.opposite_tolerance_deg, config.min_turn_deg / 2)
    for sign in (-1, 1):
        side = []
        for start, end, run_sign in runs:
            if run_sign != sign:
                continue
            if side:
                old_start, old_end, count = side[-1]
                gap = slice(old_end, start + 1)
                opposite_deg = float(
                    np.degrees(np.trapezoid(np.maximum(-sign * signal[gap], 0), s[gap]))
                )
                if (
                    s[start] - s[old_end] <= config.merge_gap_m
                    and valid[gap].all()
                    and opposite_deg <= opposite_limit + 1e-10
                ):
                    side[-1] = (old_start, end, count + 1)
                    continue
            side.append((start, end, 1))
        merged.extend((start, end, sign, count) for start, end, count in side)
    merged.sort()
    bends = []
    for start, end, sign, part_count in merged:
        length = float(s[end] - s[start])
        if length < config.min_length_m:
            continue
        sl = slice(start, end + 1)
        if signal is curvature:
            smooth_speed = np.linalg.norm(np.gradient(xy[sl], s[sl], axis=0), axis=1)
            angle = float(np.degrees(np.trapezoid(signal[sl] * smooth_speed, s[sl])))
        else:
            angle = float(np.degrees(np.trapezoid(signal[sl], s[sl])))
        if abs(angle) < config.min_turn_deg:
            continue
        # Flat bridges and opposite lobes are excluded from radius summaries.
        active = (sign * signal[sl] >= low) & (sign * curvature[sl] >= low)
        if not active.any():
            continue
        active_indices = np.arange(start, end + 1)[active]
        magnitudes = sign * curvature[active_indices]
        # A sensitive detector deliberately extends into shallow transition tails.
        # Estimate the typical radius on the curved core, not noisy near-straight tails.
        core = sign * signal[active_indices] >= max(low, 0.25 * np.max(sign * signal[sl]))
        typical_magnitudes = magnitudes[core] if core.any() else magnitudes
        apex = int(active_indices[np.argmax(magnitudes)])
        props = {
            "id": len(bends) + 1,
            "start_s_m": float(s[start]),
            "end_s_m": float(s[end]),
            "direction": "left" if sign > 0 else "right",
            "min_radius_m": float(1 / np.max(magnitudes)),
            "p50_radius_m": float(1 / np.median(typical_magnitudes)),
            "length_m": length,
            "turn_angle_deg": angle,
            "apex_s_m": float(s[apex]),
            "start_index": int(start),
            "end_index": int(end),
            "apex_index": int(apex),
            "merged_part_count": part_count,
            "detection_min_radius_m": float(1 / np.max(sign * signal[sl])),
        }
        if lonlat is not None:
            props.update(
                {
                    "start_lonlat": lonlat[start].tolist(),
                    "end_lonlat": lonlat[end].tolist(),
                    "apex_lonlat": lonlat[apex].tolist(),
                }
            )
        bends.append(props)
    return bends


def analyze_xy(points, config=None, *, crs="local_metric", inverse=None, metadata=None):
    config = config or Config()
    source = clean_points(points)
    s, xy = resample(source, config.spacing_m)
    smooth, curvature, circle, valid, window, guard = curvature_estimates(s, xy, config)
    detection, detection_valid, detection_guard = detection_signal(s, xy, config, source)
    detection_valid &= valid
    lonlat = None
    if inverse is not None:
        lon, lat = inverse.transform(xy[:, 0], xy[:, 1])
        lonlat = np.column_stack([lon, lat])
    source_spacing = np.linalg.norm(np.diff(source, axis=0), axis=1)
    quality = {
        "source_vertex_count": len(source),
        "source_segment_p50_m": float(np.median(source_spacing)),
        "source_segment_p95_m": float(np.percentile(source_spacing, 95)),
        "source_segment_max_m": float(np.max(source_spacing)),
        "smoothing_window_samples": window,
        "effective_smoothing_span_m": float((window - 1) * (s[1] - s[0])),
        "excluded_start_m": min(float(guard * (s[1] - s[0])), float(s[-1])),
        "excluded_end_m": min(float(guard * (s[1] - s[0])), float(s[-1])),
        "valid_sample_fraction": float(np.mean(valid)),
        "detection_valid_sample_fraction": float(np.mean(detection_valid)),
        "detection_excluded_endpoint_m": min(float(detection_guard * (s[1] - s[0])), float(s[-1])),
        "estimator_mae_1pm": float(np.mean(np.abs(curvature[valid] - circle[valid])))
        if valid.any()
        else None,
        "notes": [
            "Geometry candidates include junction turns; road semantics are not classified.",
            "Resampling adds no new detail to sparse source geometry.",
            "Minimum radius is sensitive to noise and smoothing; p50 is also reported.",
            "p50 radius uses same-direction samples with detection curvature >= 25% of the bend detection peak; flat bridges excluded.",
            "Detection boundaries interpolate source segment bearings at bounded midpoint anchors; inferred transitions are not independent road measurements.",
        ],
    }
    bends = detect_bends(s, smooth, curvature, detection_valid, config, lonlat, signal=detection)
    source_s = np.r_[0.0, np.cumsum(source_spacing)]
    for bend in bends:
        start, end = bend["start_s_m"], bend["end_s_m"]
        count = int(np.count_nonzero((source_s >= start) & (source_s <= end)))
        overlaps = (source_s[:-1] <= end) & (source_s[1:] >= start)
        bend["source_vertex_count"] = count
        bend["source_max_segment_m"] = float(np.max(source_spacing[overlaps]))
        # A descriptive support flag, not a calibrated statistical confidence score.
        bend["geometry_support"] = "sparse" if count < 3 else "multi_vertex"
    quality["sparse_geometry_bend_count"] = sum(b["geometry_support"] == "sparse" for b in bends)
    return Analysis(
        config,
        s,
        xy,
        smooth,
        curvature,
        circle,
        valid,
        detection,
        detection_valid,
        bends,
        crs,
        lonlat,
        quality,
        metadata or {},
    )


def analyze_lonlat(points, config=None, *, metadata=None):
    """Input/GeoJSON order is always [longitude, latitude]."""
    coords = clean_points(points)
    lon, lat = coords[:, 0], coords[:, 1]
    if (np.abs(lon) > 180).any() or (lat < -80).any() or (lat > 84).any():
        raise ValueError("Longitude must be [-180,180], latitude within UTM limits [-80,84]")
    center_lon, center_lat = float(np.mean(lon)), float(np.mean(lat))
    zone = min(60, max(1, floor((center_lon + 180) / 6) + 1))
    central_meridian = zone * 6 - 183
    if np.max(np.abs(lon - central_meridian)) > 6 or np.ptp(lat) > 8:
        raise ValueError("Route spans too wide an area for one local UTM CRS; split the route")
    crs = CRS.from_epsg((32600 if center_lat >= 0 else 32700) + zone)
    forward = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    inverse = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    x, y = forward.transform(lon, lat)
    return analyze_xy(
        np.column_stack([x, y]), config, crs=crs.to_string(), inverse=inverse, metadata=metadata
    )
