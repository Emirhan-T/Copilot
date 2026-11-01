"""Local test workbench: one Python algorithm for CLI and browser experiments."""

import csv
import hashlib
import io
import threading
import time
from collections import OrderedDict
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from pyproj import Geod
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .core import Config, analyze_lonlat
from .export import geojson
from .routing import ANKARA, KONYA, fetch_osrm


class Point(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    lat: float = Field(ge=-80, le=84)
    lon: float = Field(ge=-180, le=180)

    def lonlat(self):
        return self.lon, self.lat


class RouteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: Point
    end: Point


class WebConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    min_turn_deg: float = Field(default=0.5, ge=0.05, le=180)
    merge_gap_m: float = Field(default=100, ge=0, le=1000)
    min_length_m: float = Field(default=15, ge=5, le=500)
    detection_sigma_m: float = Field(default=20, ge=5, le=100)
    radius_threshold_m: float = Field(default=50000, ge=50, le=1000000)
    spacing_m: float = Field(default=5, ge=2.5, le=25)
    smoothing_m: float = Field(default=45, ge=0, le=200)
    opposite_tolerance_deg: float = Field(default=0.15, ge=0, le=5)


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    route_id: str = Field(min_length=1, max_length=64)
    config: WebConfig = Field(default_factory=WebConfig)


def signal_points(s, signal, valid, budget=2400):
    """Keep both extrema of every bucket, preserving short sharp peaks in the plot."""
    indices = np.flatnonzero(valid & np.isfinite(signal))
    if not len(indices):
        return []
    if len(indices) <= budget:
        selected = indices
    else:
        selected = [indices[0], indices[-1]]
        for bucket in np.array_split(indices, max(1, budget // 2 - 1)):
            selected.extend((bucket[np.argmin(signal[bucket])], bucket[np.argmax(signal[bucket])]))
        selected = np.unique(selected)
    return [[round(float(s[i]), 3), float(signal[i])] for i in selected]


def samples_csv(analysis):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(
        [
            "s_m",
            "longitude",
            "latitude",
            "curvature_1pm",
            "circle_curvature_1pm",
            "radius_valid",
            "detection_curvature_1pm",
            "detection_valid",
        ]
    )
    yield stream.getvalue()
    stream.seek(0)
    stream.truncate(0)
    for i, s in enumerate(analysis.s):
        writer.writerow(
            [
                s,
                *analysis.lonlat[i],
                analysis.curvature[i],
                analysis.circle_curvature[i] if np.isfinite(analysis.circle_curvature[i]) else "",
                bool(analysis.valid[i]),
                analysis.detection_curvature[i],
                bool(analysis.detection_valid[i]),
            ]
        )
        if i % 1000 == 999:
            yield stream.getvalue()
            stream.seek(0)
            stream.truncate(0)
    if stream.tell():
        yield stream.getvalue()


def create_app(
    *, cache_dir="cache", route_fetcher=None, osrm_url="https://router.project-osrm.org"
):
    app = FastAPI(title="Viraj Lab", version="0.1.0")
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"]
    )
    static = Path(__file__).with_name("web_static")
    app.mount("/static", StaticFiles(directory=static), name="static")
    routes = OrderedDict()
    analyses = OrderedDict()
    cache_lock = threading.RLock()
    fetch_lock = threading.Lock()
    last_fetch = [0.0]

    @app.middleware("http")
    async def same_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        if (
            request.method == "POST"
            and origin
            and origin.rstrip("/") != str(request.base_url).rstrip("/")
        ):
            from fastapi.responses import JSONResponse

            return JSONResponse({"detail": "Analizi bu test sayfasından başlat."}, status_code=403)
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    @app.get("/api/defaults")
    def defaults():
        return {
            "config": WebConfig().model_dump(),
            "presets": [
                {
                    "name": "Konya → Ankara",
                    "start": {"lat": KONYA[1], "lon": KONYA[0]},
                    "end": {"lat": ANKARA[1], "lon": ANKARA[0]},
                }
            ],
            "attribution": "© OpenStreetMap contributors · Rota: OSRM",
        }

    @app.post("/api/routes")
    def get_route(body: RouteRequest):
        _, _, distance = Geod(ellps="WGS84").inv(*body.start.lonlat(), *body.end.lonlat())
        if distance < 25:
            raise HTTPException(422, "Başlangıç ve bitiş arasında en az 25 metre olmalı.")
        key = hashlib.sha256(body.model_dump_json().encode()).hexdigest()[:24]
        with fetch_lock:
            with cache_lock:
                if key in routes:
                    record = routes[key]
                    routes.move_to_end(key)
                    return {"route_id": key, "geometry": record[0], "metadata": record[1]}
            # Limit requests from this local app; persistent OSRM cache also avoids downloads.
            pause = 1 - (time.monotonic() - last_fetch[0])
            if pause > 0:
                time.sleep(pause)
            try:
                if route_fetcher is None:
                    coords, metadata = fetch_osrm(
                        body.start.lonlat(),
                        body.end.lonlat(),
                        cache_dir=cache_dir,
                        base_url=osrm_url,
                    )
                else:
                    coords, metadata = route_fetcher(body.start.lonlat(), body.end.lonlat())
            except (ValueError, OSError) as exc:
                raise HTTPException(502, f"Rota alınamadı. {exc}") from exc
            finally:
                last_fetch[0] = time.monotonic()
            geometry = {"type": "LineString", "coordinates": np.asarray(coords).tolist()}
            metadata = {
                **metadata,
                "requested_start_lonlat": list(body.start.lonlat()),
                "requested_end_lonlat": list(body.end.lonlat()),
            }
            with cache_lock:
                routes[key] = (geometry, metadata)
                while len(routes) > 16:
                    routes.popitem(last=False)
        return {"route_id": key, "geometry": geometry, "metadata": metadata}

    @app.post("/api/analyze")
    def analyze(body: AnalyzeRequest):
        with cache_lock:
            record = routes.get(body.route_id)
        if record is None:
            raise HTTPException(404, "Rota bellekte bulunamadı. Rotayı yeniden seçip analiz et.")
        identifier = hashlib.sha256(body.model_dump_json().encode()).hexdigest()[:24]
        with cache_lock:
            cached = analyses.get(identifier)
            if cached is not None:
                analyses.move_to_end(identifier)
                return cached[1]
        started = time.perf_counter()
        try:
            result = analyze_lonlat(
                record[0]["coordinates"], Config(**body.config.model_dump()), metadata=record[1]
            )
        except ValueError as exc:
            raise HTTPException(422, f"Bu rota analiz edilemedi: {exc}") from exc
        collection = geojson(result)
        # Keep the original full provider geometry for the background route layer.
        collection["features"][0]["geometry"] = record[0]
        data = {
            "analysis_id": identifier,
            "summary": result.summary(),
            "geojson": collection,
            "signals": {
                "detection": signal_points(
                    result.s, result.detection_curvature, result.detection_valid
                ),
                "heading": signal_points(result.s, result.curvature, result.valid),
                "circle": signal_points(result.s, result.circle_curvature, result.valid),
            },
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        }
        with cache_lock:
            analyses[identifier] = (result, data)
            while len(analyses) > 8:
                analyses.popitem(last=False)
        return data

    @app.get("/api/analyses/{identifier}/samples.csv")
    def download_csv(identifier: str):
        with cache_lock:
            cached = analyses.get(identifier)
        if cached is None:
            raise HTTPException(404, "Bu analizin süresi doldu. Yeniden analiz et.")
        return StreamingResponse(
            samples_csv(cached[0]),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="viraj-samples.csv"'},
        )

    @app.get("/api/analyses/{identifier}/signals")
    def detail_signals(identifier: str, start_s_m: float = 0, end_s_m: float = 1000):
        with cache_lock:
            cached = analyses.get(identifier)
        if cached is None:
            raise HTTPException(404, "Bu analizin süresi doldu. Yeniden analiz et.")
        if not np.isfinite([start_s_m, end_s_m]).all() or start_s_m >= end_s_m:
            raise HTTPException(422, "Grafik mesafe aralığı geçersiz.")
        result = cached[0]
        window = (result.s >= start_s_m) & (result.s <= end_s_m)
        return {
            "signals": {
                "detection": signal_points(
                    result.s, result.detection_curvature, result.detection_valid & window
                ),
                "heading": signal_points(result.s, result.curvature, result.valid & window),
                "circle": signal_points(result.s, result.circle_curvature, result.valid & window),
            }
        }

    return app


def run_server(*, port=8766, cache_dir="cache", osrm_url="https://router.project-osrm.org"):
    import uvicorn

    print(f"Viraj Lab: http://127.0.0.1:{port}")
    uvicorn.run(create_app(cache_dir=cache_dir, osrm_url=osrm_url), host="127.0.0.1", port=port)
