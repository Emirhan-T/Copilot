"""Physics-based curve target speed and advance warning distance.

This layer reads finished bend candidates; it never changes detection. A
candidate becomes a *warning* only when its target speed is meaningfully below
the approach speed, so the sensitive geometric detector can stay sensitive.

Point-mass curve model (AASHTO Green Book):  v^2 = g R (e + f(v))
Advance warning distance:  d = v0 t_r + (v0^2 - vt^2) / (2 a)
"""

from dataclasses import asdict, dataclass
from math import sqrt

import numpy as np

G = 9.81
KMH = 3.6
MAX_REPORTED_KMH = 200.0
MPH_TO_KMH = 1.609344

# AASHTO "A Policy on Geometric Design of Highways and Streets", 7th ed. (2018),
# Table 3-7: maximum side friction factor by design speed (mph). These are
# driver-comfort design limits, far below tyre-road peak friction on purpose.
AASHTO_FMAX_MPH = (
    (15, 0.32), (20, 0.27), (25, 0.23), (30, 0.20), (35, 0.18), (40, 0.16), (45, 0.15),
    (50, 0.14), (55, 0.13), (60, 0.12), (65, 0.11), (70, 0.10), (75, 0.09), (80, 0.08),
)  # fmt: skip
_FMAX_KMH = np.array([mph * MPH_TO_KMH for mph, _ in AASHTO_FMAX_MPH])
_FMAX = np.array([f for _, f in AASHTO_FMAX_MPH])

METHOD_NOTES = [
    "Target speed: point-mass model v^2 = g R (e + f(v)); f from AASHTO 2018 Table 3-7.",
    "f is a comfort design limit, not tyre peak friction; it is clamped outside 24-129 km/h.",
    "Superelevation e is unknown in OSM; one assumed value is used for every bend.",
    (
        "Radius: heading change over the bend core (smoothing-independent); a circle fitted to "
        ">= 4 source vertices in the core lowers it when tighter."
    ),
    (
        "Small deflections: radius is at least 8 w / theta^2 (lane slack w, total turn theta), "
        "the flattest path that stays within w of the polyline corner."
    ),
    (
        "Warning distance: v0 t_r + (v0^2 - vt^2)/(2a); defaults t_r = 2.5 s, a = 3.4 m/s^2 "
        "(AASHTO stopping sight distance)."
    ),
    (
        "Allowed-speed profile: per-sample limit (approach speed, bend targets on cores), then a "
        "backward braking pass v(s) <= sqrt(v(s+ds)^2 + 2 a ds)."
    ),
    (
        "Bends form one warning series while a driver accelerating at the assumed rate after a "
        "bend cannot get back near the approach speed before the next slowdown."
    ),
    (
        "Live rule: warn only when GPS speed exceeds the allowed speed within the reaction "
        "distance ahead by more than the tolerance; one alert per series, again only if the "
        "driver is not slowing down or a further step down is needed."
    ),
    (
        "Bend targets are uncapped physical upper limits (reported up to 200 km/h). The "
        "reference speed only decides which bends count as slowdowns and how they group."
    ),
    "Not validated against measured vehicle speeds; prototype guidance only.",
]


@dataclass(frozen=True)
class SpeedConfig:
    # Reference speed for the slowdown flag and series grouping only; it does
    # not cap bend targets. 90 km/h = TR two-way intercity car limit.
    approach_speed_kmh: float = 90.0
    superelevation: float = 0.0
    # Reserved for the weather stage: scales f (1.0 = dry design value).
    friction_scale: float = 1.0
    reaction_time_s: float = 2.5
    deceleration_mps2: float = 3.4
    # MUTCD 2009 Table 2C-5: advance curve warning is recommended from a
    # 5 mph (~8 km/h) speed-limit vs advisory difference.
    warning_margin_kmh: float = 8.0
    core_fraction: float = 0.7
    # Lateral room a driver may use inside the lane to flatten a small
    # deflection (m). A corner of total angle theta can be driven on a path
    # radius of about 8 w / theta^2 without leaving that band.
    lane_slack_m: float = 0.5
    min_circle_vertices: int = 4
    # Assumed comfortable acceleration after a bend (m/s^2). Only used to decide
    # whether two bends are far enough apart to be separate warnings.
    acceleration_mps2: float = 1.0
    # Live warnings fire only above the allowed speed by more than this (GPS noise).
    overspeed_tolerance_kmh: float = 3.0
    alert_cooldown_s: float = 4.0
    envelope_spacing_m: float = 10.0

    def __post_init__(self):
        positive = (
            "approach_speed_kmh",
            "friction_scale",
            "reaction_time_s",
            "deceleration_mps2",
            "acceleration_mps2",
            "envelope_spacing_m",
        )
        for name in positive:
            value = getattr(self, name)
            if not np.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if not np.isfinite(self.superelevation) or not 0 <= self.superelevation <= 0.12:
            raise ValueError("superelevation must be within [0, 0.12]")
        if not np.isfinite(self.warning_margin_kmh) or self.warning_margin_kmh < 0:
            raise ValueError("warning_margin_kmh must be finite and non-negative")
        for name in ("overspeed_tolerance_kmh", "alert_cooldown_s"):
            if not np.isfinite(getattr(self, name)) or getattr(self, name) < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if not np.isfinite(self.lane_slack_m) or self.lane_slack_m < 0:
            raise ValueError("lane_slack_m must be finite and non-negative")
        if not 0 < self.core_fraction < 1:
            raise ValueError("core_fraction must be in (0,1)")
        if not isinstance(self.min_circle_vertices, int) or self.min_circle_vertices < 3:
            raise ValueError("min_circle_vertices must be an integer >= 3")


def side_friction(speed_kmh):
    """AASHTO design side friction factor, linearly interpolated, ends clamped."""
    return np.interp(speed_kmh, _FMAX_KMH, _FMAX)


def curve_speed_kmh(radius_m, *, superelevation=0.0, friction_scale=1.0, cap_kmh=250.0):
    """Solve v^2 = g R (e + s f(v)). f falls with speed, so the root is unique."""
    if not np.isfinite(radius_m) or radius_m <= 0:
        return float(cap_kmh) if radius_m == np.inf else 0.0

    def excess(v_kmh):
        v = v_kmh / KMH
        demand = superelevation + friction_scale * side_friction(v_kmh)
        return G * radius_m * demand - v * v

    if excess(cap_kmh) >= 0:
        return float(cap_kmh)
    low, high = 0.0, float(cap_kmh)
    for _ in range(60):
        mid = (low + high) / 2
        low, high = (mid, high) if excess(mid) >= 0 else (low, mid)
    return low


def warning_distance_m(approach_kmh, target_kmh, *, reaction_time_s=2.5, deceleration_mps2=3.4):
    v0, vt = approach_kmh / KMH, min(target_kmh, approach_kmh) / KMH
    return v0 * reaction_time_s + (v0 * v0 - vt * vt) / (2 * deceleration_mps2)


def fit_circle_radius(points):
    """Algebraic (Kasa) least-squares circle; centred first for UTM precision."""
    p = np.asarray(points, dtype=float)
    if len(p) < 3:
        return np.nan
    p = p - p.mean(axis=0)
    a = np.column_stack([2 * p, np.ones(len(p))])
    b = (p * p).sum(axis=1)
    (cx, cy, c), *_ = np.linalg.lstsq(a, b, rcond=None)
    r2 = c + cx * cx + cy * cy
    if not np.isfinite(r2) or r2 <= 0:
        return np.nan
    # Near-collinear points produce a huge, meaningless radius.
    return float(sqrt(r2)) if sqrt(r2) < 1e6 else np.nan


def speed_radius(analysis, bend, config=SpeedConfig()):
    """Radius used for speed, plus the evidence behind it.

    The bend core is where the detection heading signal reaches
    ``core_fraction`` of its peak. Heading change over that core is a property
    of the input geometry, so it does not move with the radius smoothing span.
    """
    sign = 1 if bend["direction"] == "left" else -1
    start, end = bend["start_index"], bend["end_index"]
    signal = sign * analysis.detection_curvature[start : end + 1]
    s = analysis.s[start : end + 1]
    core = np.flatnonzero(signal >= config.core_fraction * np.max(signal))
    i0, i1 = int(core[0]), int(core[-1])
    core_angle = float(np.trapezoid(signal[i0 : i1 + 1], s[i0 : i1 + 1]))
    core_length = float(s[i1] - s[i0])
    if core_length <= 0 or core_angle <= 0:
        # Single-sample core: use the peak detection curvature.
        heading_radius = float(1 / np.max(signal))
    else:
        heading_radius = core_length / core_angle
    circle_radius, vertex_count = np.nan, 0
    source = getattr(analysis, "source_xy", None)
    if source is not None:
        source_s = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(source, axis=0), axis=1))]
        inside = (source_s >= s[i0]) & (source_s <= s[i1])
        vertex_count = int(np.count_nonzero(inside))
        if vertex_count >= config.min_circle_vertices:
            circle_radius = fit_circle_radius(source[inside])
    has_circle = bool(np.isfinite(circle_radius))
    geometric = min(heading_radius, circle_radius) if has_circle else heading_radius
    # Small total deflections need not be driven at the geometric radius.
    total_turn = np.radians(abs(bend["turn_angle_deg"]))
    slack_radius = 8 * config.lane_slack_m / total_turn**2 if total_turn > 0 else np.inf
    radius = max(geometric, slack_radius)
    if has_circle and abs(circle_radius / heading_radius - 1) <= 0.25:
        confidence = "high"
    elif has_circle or vertex_count >= 3:
        confidence = "medium"
    else:
        confidence = "low"
    return {
        "radius_m": float(radius),
        "heading_core_radius_m": float(heading_radius),
        "circle_fit_radius_m": float(circle_radius) if has_circle else None,
        "core_start_s_m": float(s[i0]),
        "core_end_s_m": float(s[i1]),
        "core_turn_deg": float(np.degrees(core_angle)),
        "core_source_vertex_count": vertex_count,
        "lane_slack_radius_m": float(slack_radius) if np.isfinite(slack_radius) else None,
        "radius_basis": (
            "lane_slack"
            if slack_radius > geometric
            else ("min(heading_core, circle_fit)" if has_circle else "heading_core")
        ),
        "confidence": confidence,
    }


def assess_bend(analysis, bend, config=SpeedConfig()):
    evidence = speed_radius(analysis, bend, config)
    # No cap: every bend reports its own physical upper limit. Above
    # MAX_REPORTED_KMH the bend does not constrain a car in practice.
    target = curve_speed_kmh(
        evidence["radius_m"],
        superelevation=config.superelevation,
        friction_scale=config.friction_scale,
        cap_kmh=MAX_REPORTED_KMH,
    )
    reduction = max(0.0, config.approach_speed_kmh - target)
    return {
        "bend_id": bend["id"],
        "direction": bend["direction"],
        **evidence,
        "target_speed_kmh": round(target, 1),
        "target_is_unbounded": bool(target >= MAX_REPORTED_KMH),
        "speed_reduction_kmh": round(reduction, 1),
        "requires_slowdown": bool(reduction >= config.warning_margin_kmh),
        "series_id": None,
    }


@dataclass
class SpeedProfile:
    """Allowed speed along the route axis ``s``; approach speed elsewhere."""

    s: np.ndarray
    allowed_kmh: np.ndarray
    approach_speed_kmh: float

    def allowed_at(self, s_m):
        return np.interp(s_m, self.s, self.allowed_kmh)

    def minimum_between(self, start_m, end_m):
        start_m, end_m = min(start_m, end_m), max(start_m, end_m)
        inside = (self.s >= start_m) & (self.s <= end_m)
        values = [self.allowed_at(start_m), self.allowed_at(end_m)]
        if inside.any():
            values.append(np.min(self.allowed_kmh[inside]))
        return float(np.min(values))

    @classmethod
    def from_series(cls, series, approach_speed_kmh):
        """Rebuild from the compact JSON envelopes, as a phone would."""
        points = [(0.0, approach_speed_kmh)]
        for item in sorted(series, key=lambda x: x["slowdown_start_s_m"]):
            envelope = item["envelope"]
            points.extend((s, v) for s, v in envelope)
            # Allowed speed steps back up right after the last bend core.
            points.append((envelope[-1][0] + 0.01, approach_speed_kmh))
        points = sorted(points)
        s, v = np.array(points).T
        # Overlapping envelopes: keep the stricter value at duplicate positions.
        order = np.lexsort((v, s))
        s, v = s[order], v[order]
        unique = np.r_[True, np.diff(s) > 0]
        s, v = s[unique], v[unique]
        return cls(s, v, approach_speed_kmh)


def braking_envelope(s, limit_kmh, deceleration_mps2):
    """Highest speed at each sample that can still brake down to every later limit."""
    v = np.asarray(limit_kmh, dtype=float) / KMH
    out = v.copy()
    ds = np.diff(s)
    for i in range(len(s) - 2, -1, -1):
        out[i] = min(v[i], sqrt(out[i + 1] ** 2 + 2 * deceleration_mps2 * ds[i]))
    return out * KMH


def acceleration_limited(s, allowed_kmh, acceleration_mps2):
    """What a driver obeying ``allowed`` can actually reach when accelerating gently."""
    v = np.asarray(allowed_kmh, dtype=float) / KMH
    out = v.copy()
    ds = np.diff(s)
    for i in range(1, len(s)):
        out[i] = min(v[i], sqrt(out[i - 1] ** 2 + 2 * acceleration_mps2 * ds[i - 1]))
    return out * KMH


def build_profile(analysis, records, config):
    s = analysis.s
    limit = np.full(len(s), float(config.approach_speed_kmh))
    # The profile is capped at the reference speed only for grouping; bend
    # records keep their uncapped targets.
    for record in records:
        core = (s >= record["core_start_s_m"]) & (s <= record["core_end_s_m"])
        limit[core] = np.minimum(limit[core], record["target_speed_kmh"])
    allowed = braking_envelope(s, limit, config.deceleration_mps2)
    return SpeedProfile(s.copy(), allowed, float(config.approach_speed_kmh))


def _runs(mask):
    edges = np.flatnonzero(np.diff(np.r_[0, mask.astype(int), 0]))
    return list(zip(edges[::2], edges[1::2] - 1, strict=True))


def _envelope(profile, start, end, records, spacing_m):
    s = profile.s
    step = max(1, round(spacing_m / float(s[1] - s[0]))) if len(s) > 1 else 1
    keep = set(range(start, end + 1, step)) | {start, end}
    # Core edges are exact steps of the profile; keep them so linear
    # interpolation between kept points never rises above the true profile.
    for record in records:
        for key in ("core_start_s_m", "core_end_s_m"):
            index = int(np.searchsorted(s, record[key]))
            for j in (index - 1, index, index + 1):
                if start <= j <= end:
                    keep.add(j)
    return [[round(float(s[i]), 3), round(float(profile.allowed_kmh[i]), 1)] for i in sorted(keep)]


def find_series(analysis, records, profile, config):
    """Group slowdown bends that a driver cannot treat as separate events."""
    v0 = config.approach_speed_kmh
    reachable = acceleration_limited(profile.s, profile.allowed_kmh, config.acceleration_mps2)
    slow = reachable < v0 - config.warning_margin_kmh
    series = []
    for start, end in _runs(slow):
        s0, s1 = profile.s[start], profile.s[end]
        members = [
            r
            for r in records
            if r["requires_slowdown"] and r["core_end_s_m"] >= s0 and r["core_start_s_m"] <= s1
        ]
        if not members:
            continue
        # Where the allowed speed first dips noticeably below the approach speed.
        first = start
        while first > 0 and profile.allowed_kmh[first - 1] < v0 - config.overspeed_tolerance_kmh:
            first -= 1
        # The stored envelope also covers the shallow start of the braking ramp.
        envelope_first = first
        while envelope_first > 0 and profile.allowed_kmh[envelope_first - 1] < v0 - 0.5:
            envelope_first -= 1
        if envelope_first > 0:
            envelope_first -= 1
        last = int(np.searchsorted(profile.s, max(r["core_end_s_m"] for r in members)))
        last = min(max(last, first), len(profile.s) - 1)
        critical = min(members, key=lambda r: r["target_speed_kmh"])
        trigger = float(profile.s[first]) - v0 / KMH * config.reaction_time_s
        number = len(series) + 1
        first_core = min(r["core_start_s_m"] for r in members)
        last_core = max(r["core_end_s_m"] for r in members)
        if len(members) == 1:
            message = f"Viraj: {critical['target_speed_kmh']:.0f} km/s'e yavaşla"
        else:
            message = (
                f"{len(members)} viraj arka arkaya ({last_core - first_core:.0f} m): "
                f"en düşük {critical['target_speed_kmh']:.0f} km/s"
            )
        item = {
            "id": number,
            "bend_ids": [r["bend_id"] for r in members],
            "critical_bend_id": critical["bend_id"],
            "min_target_kmh": critical["target_speed_kmh"],
            "first_core_start_s_m": round(first_core, 1),
            "last_core_end_s_m": round(last_core, 1),
            "slowdown_start_s_m": round(float(profile.s[first]), 1),
            "trigger_s_at_approach_m": round(max(0.0, trigger), 1),
            "trigger_clipped_to_route_start": bool(trigger < 0),
            "message": message,
            "envelope": _envelope(
                profile, envelope_first, last, members, config.envelope_spacing_m
            ),
        }
        if analysis.lonlat is not None:
            index = min(int(np.searchsorted(profile.s, max(0.0, trigger))), len(profile.s) - 1)
            item["trigger_lonlat"] = analysis.lonlat[index].tolist()
        for record in members:
            record["series_id"] = number
        series.append(item)
    return series


class OverspeedMonitor:
    """Live warning rule for a phone: feed (route position, GPS speed, time).

    Warns only when the driver is faster than the allowed speed anywhere
    within the reaction distance ahead. One alert per bend series; a repeat
    only if the driver is not slowing down after the cooldown, or obeyed and
    then exceeds a further step down inside the same series.
    """

    def __init__(self, profile, series, config=None):
        self.profile = profile
        self.series = sorted(series, key=lambda x: x["slowdown_start_s_m"])
        self.config = config or SpeedConfig()
        self.state = {}

    def _series_at(self, s_m, look_m):
        for item in self.series:
            if item["slowdown_start_s_m"] <= look_m and s_m <= item["last_core_end_s_m"]:
                return item
        return None

    def update(self, s_m, speed_kmh, t_s):
        config = self.config
        look = s_m + speed_kmh / KMH * config.reaction_time_s
        allowed = self.profile.minimum_between(s_m, look)
        exceeding = speed_kmh > allowed + config.overspeed_tolerance_kmh
        item = self._series_at(s_m, look)
        if item is None:
            return None
        state = self.state.get(item["id"])
        if not exceeding:
            if state is not None:
                state["complied"] = True
            return None
        ahead_min = self.profile.minimum_between(s_m, item["last_core_end_s_m"])
        event = {
            "series_id": item["id"],
            "s_m": float(s_m),
            "t_s": float(t_s),
            "speed_kmh": float(speed_kmh),
            "allowed_kmh": round(allowed, 1),
            "target_kmh": round(ahead_min, 1),
        }
        if state is None:
            event.update(kind="series", message=item["message"])
        elif t_s - state["t_s"] < config.alert_cooldown_s:
            return None
        elif state["complied"]:
            event.update(kind="step", message=f"Daha da yavaşla: {ahead_min:.0f} km/s")
        elif speed_kmh >= state["speed_kmh"] - 2:
            event.update(kind="reminder", message=f"Hâlâ hızlısın: {ahead_min:.0f} km/s")
        else:
            return None  # slowing down already
        self.state[item["id"]] = {"t_s": t_s, "speed_kmh": speed_kmh, "complied": False}
        return event


def assess_speeds(analysis, config=None):
    """Attach a ``speed`` block to the analysis and return it."""
    config = config or SpeedConfig()
    records = [assess_bend(analysis, bend, config) for bend in analysis.bends]
    profile = build_profile(analysis, records, config)
    series = find_series(analysis, records, profile, config)
    analysis.speed_profile = profile
    analysis.speed = {
        "model": "point_mass_aashto_side_friction",
        "config": asdict(config),
        "notes": METHOD_NOTES,
        "slowdown_bend_count": sum(r["requires_slowdown"] for r in records),
        "series_count": len(series),
        "multi_bend_series_count": sum(len(x["bend_ids"]) > 1 for x in series),
        "low_confidence_slowdown_count": sum(
            r["requires_slowdown"] and r["confidence"] == "low" for r in records
        ),
        "series": series,
        "bends": records,
    }
    return analysis.speed
