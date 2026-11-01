"""Locate an explicitly supplied map point on the route and near detected bends."""

import numpy as np
from pyproj import Transformer


def check_point(analysis, lonlat, name="Bildirilen nokta"):
    point = np.asarray(lonlat, dtype=float)
    if analysis.lonlat is None:
        raise ValueError("Point inspection requires a geographic route")
    if (
        point.shape != (2,)
        or not np.isfinite(point).all()
        or abs(point[0]) > 180
        or abs(point[1]) > 90
    ):
        raise ValueError("Inspection point must be longitude,latitude")
    transformer = Transformer.from_crs(4326, analysis.crs, always_xy=True)
    target = np.asarray(transformer.transform(*point))
    edges = np.diff(analysis.xy, axis=0)
    squared = np.sum(edges * edges, axis=1)
    fractions = np.divide(
        np.sum((target - analysis.xy[:-1]) * edges, axis=1),
        squared,
        out=np.zeros(len(edges)),
        where=squared > 1e-12,
    )
    fractions = np.clip(fractions, 0, 1)
    closest = analysis.xy[:-1] + fractions[:, None] * edges
    distances = np.linalg.norm(closest - target, axis=1)
    index = int(np.argmin(distances))
    route_s = float(
        analysis.s[index] + fractions[index] * (analysis.s[index + 1] - analysis.s[index])
    )
    result = {
        "name": name,
        "lonlat": point.tolist(),
        "route_s_m": route_s,
        "distance_to_route_m": float(distances[index]),
        "inside_bend_id": None,
        "nearest_bend_id": None,
        "nearest_bend_offset_m": None,
    }
    if analysis.bends:
        offsets = [max(b["start_s_m"] - route_s, route_s - b["end_s_m"], 0) for b in analysis.bends]
        nearest = int(np.argmin(offsets))
        result["nearest_bend_id"] = analysis.bends[nearest]["id"]
        result["nearest_bend_offset_m"] = float(offsets[nearest])
        if offsets[nearest] == 0:
            result["inside_bend_id"] = analysis.bends[nearest]["id"]
    return result
