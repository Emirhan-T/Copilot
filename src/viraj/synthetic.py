"""Analytic, tangent-continuous roads with known signed curvature."""

from dataclasses import dataclass

import numpy as np


@dataclass
class SyntheticRoad:
    xy: np.ndarray
    s: np.ndarray
    curvature: np.ndarray
    segments: list[dict]


def make_road(segments, *, step_m=2.0, noise_m=0.0, seed=42):
    """segments: (length_m, signed_curvature_1pm), positive means left."""
    position = np.array([0.0, 0.0])
    heading, distance = 0.0, 0.0
    points, distances, curvatures = [position.copy()], [0.0], [segments[0][1]]
    truth = []
    for length, kappa in segments:
        t = np.linspace(0, length, max(1, int(np.ceil(length / step_m))) + 1)[1:]
        if kappa == 0:
            delta = np.column_stack([t * np.cos(heading), t * np.sin(heading)])
        else:
            angles = heading + kappa * t
            delta = np.column_stack(
                [
                    (np.sin(angles) - np.sin(heading)) / kappa,
                    (np.cos(heading) - np.cos(angles)) / kappa,
                ]
            )
        additions = position + delta
        points.extend(additions)
        distances.extend(distance + t)
        curvatures.extend(np.full(len(t), kappa))
        truth.append({"start_s_m": distance, "end_s_m": distance + length, "curvature_1pm": kappa})
        position = additions[-1]
        heading += kappa * length
        distance += length
    xy = np.asarray(points)
    if noise_m:
        xy = xy + np.random.default_rng(seed).normal(0, noise_m, xy.shape)
    return SyntheticRoad(xy, np.asarray(distances), np.asarray(curvatures), truth)


def demo_road(noise_m=0.0, seed=42):
    return make_road(
        [(200, 0), (np.pi * 100 / 2, 1 / 100), (120, 0), (np.pi * 60 / 2, -1 / 60), (200, 0)],
        noise_m=noise_m,
        seed=seed,
    )


def arc(radius_m, *, sign=1, noise_m=0.0, seed=42):
    return make_road([(radius_m * np.pi, sign / radius_m)], noise_m=noise_m, seed=seed)
