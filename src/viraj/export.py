"""Stable machine output plus an inspectable map/curvature report."""

import base64
import csv
import html
import json
from pathlib import Path

import folium
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from branca.element import Element

COLORS = {"left": "#e76f35", "right": "#2563eb"}


def write_json(path, data):
    Path(path).write_text(
        json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8"
    )


def geojson(analysis):
    if analysis.lonlat is None:
        raise ValueError("GeoJSON export requires geographic coordinates")
    features = [
        {
            "type": "Feature",
            "properties": {"kind": "route", **analysis.metadata},
            "geometry": {"type": "LineString", "coordinates": analysis.lonlat.tolist()},
        }
    ]
    for bend in analysis.bends:
        start, end = bend["start_index"], bend["end_index"]
        features.append(
            {
                "type": "Feature",
                "properties": {"kind": "bend", **bend},
                "geometry": {
                    "type": "LineString",
                    "coordinates": analysis.lonlat[start : end + 1].tolist(),
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


def draw_analysis(analysis, path):
    with plt.rc_context({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False}):
        fig, axes = plt.subplots(1, 2, figsize=(14, 5), layout="constrained")
        relative = analysis.xy - analysis.xy[0]
        axes[0].plot(*relative.T, color="#aab4c5", linewidth=2, label="Route")
        for bend in analysis.bends:
            start, end = bend["start_index"], bend["end_index"]
            sl = slice(start, end + 1)
            color = COLORS[bend["direction"]]
            axes[0].plot(*relative[sl].T, color=color, linewidth=3)
        axes[0].scatter(*relative[0], color="#16866c", s=40, zorder=5, label="Start")
        axes[0].scatter(*relative[-1], color="#222b40", s=40, zorder=5, label="End")
        axes[0].set(
            xlabel="East from start (m)",
            ylabel="North from start (m)",
            title="Route and bend candidates",
            aspect="equal",
        )
        axes[0].legend(loc="best")
        valid_k = np.where(analysis.valid, analysis.curvature, np.nan)
        circle_k = np.where(analysis.valid, analysis.circle_curvature, np.nan)
        axes[1].plot(analysis.s / 1000, valid_k, color="#172d46", lw=1, label="Heading derivative")
        axes[1].plot(
            analysis.s / 1000,
            circle_k,
            color="#38a390",
            lw=0.8,
            alpha=0.7,
            label="Three-point circle",
        )
        axes[1].plot(
            analysis.s / 1000,
            np.where(analysis.detection_valid, analysis.detection_curvature, np.nan),
            color="#e76f35",
            lw=1,
            label="Detection: filtered heading",
        )
        threshold = 1 / analysis.config.radius_threshold_m
        for value in (-threshold, threshold):
            axes[1].axhline(value, color="#9ba4b3", ls="--", lw=1)
        for bend in analysis.bends:
            axes[1].axvspan(
                bend["start_s_m"] / 1000,
                bend["end_s_m"] / 1000,
                alpha=0.12,
                color=COLORS[bend["direction"]],
            )
        axes[1].set(
            xlabel="Distance along input route (km)",
            ylabel="Signed curvature (1/m)",
            title="Positive: left · Negative: right",
        )
        axes[1].legend(loc="best")
        axes[1].grid(alpha=0.15)
        fig.savefig(path, dpi=160)
        plt.close(fig)


def draw_map(analysis, path, chart_path, comparison=None):
    coords = analysis.lonlat[:, ::-1].tolist()
    # Render source detail without Leaflet screen-space geometry simplification.
    m = folium.Map(
        location=coords[0],
        tiles="OpenStreetMap",
        zoom_start=8,
        control_scale=True,
        prefer_canvas=True,
    )
    folium.PolyLine(
        coords, color="#7a8a9e", weight=4, opacity=0.65, smooth_factor=0, tooltip="Rota"
    ).add_to(m)
    folium.Marker(coords[0], tooltip="Başlangıç", icon=folium.Icon(color="green")).add_to(m)
    folium.Marker(coords[-1], tooltip="Bitiş", icon=folium.Icon(color="darkblue")).add_to(m)
    rows = []
    review_buttons = []
    for review in analysis.metadata.get("review_points", []):
        lon, lat = review["lonlat"]
        name = html.escape(str(review["name"]))
        folium.Marker(
            [lat, lon], tooltip=name, icon=folium.Icon(color="purple", icon="info-sign")
        ).add_to(m)
        nearby = np.abs(analysis.s - review["route_s_m"]) <= 1200
        area = np.vstack([analysis.lonlat[nearby, ::-1], [[lat, lon]]])
        bounds = json.dumps([area.min(axis=0).tolist(), area.max(axis=0).tolist()])
        offset = review.get("nearest_bend_offset_m")
        text = (
            f"Aday #{review['nearest_bend_id']}, sınırına {offset:.0f} m"
            if offset is not None
            else "Yakın aday bulunamadı"
        )
        review_buttons.append(
            f"<button class=\"bend review\" data-bounds='{bounds}'><span>"
            f"<b>{name} ↗</b><small>{review['route_s_m'] / 1000:.2f} km · {text}</small>"
            "</span></button>"
        )
    current_layer = folium.FeatureGroup(name="Güncel virajlar", show=True).add_to(m)
    if comparison is not None:
        baseline_layer = folium.FeatureGroup(name="Önceki tespitler (v1)", show=False).add_to(m)
        for feature in comparison.get("features", []):
            if feature.get("properties", {}).get("kind") == "bend":
                previous_coords = feature["geometry"]["coordinates"]
                folium.PolyLine(
                    [[lat, lon] for lon, lat in previous_coords],
                    color="#a21caf",
                    weight=4,
                    dash_array="7 5",
                    smooth_factor=0,
                    tooltip=f"Önceki tespit #{feature['properties'].get('id', '')}",
                ).add_to(baseline_layer)
        folium.LayerControl(collapsed=False).add_to(m)
    for bend in analysis.bends:
        start, end = bend["start_index"], bend["end_index"]
        direction = "Sol" if bend["direction"] == "left" else "Sağ"
        color = COLORS[bend["direction"]]
        title = (
            f"#{bend['id']} · {direction} · R min ≈ {bend['min_radius_m']:.0f} m"
            f" · {bend['length_m']:.0f} m uzunluk"
        )
        support = " · seyrek geometri" if bend["geometry_support"] == "sparse" else ""
        merged_note = (
            f" · {bend['merged_part_count']} parça birleşti"
            if bend["merged_part_count"] > 1
            else ""
        )
        title += support + merged_note
        folium.PolyLine(
            coords[start : end + 1],
            color=color,
            weight=7,
            smooth_factor=0,
            tooltip=title,
            popup=title,
        ).add_to(current_layer)
        bounds = analysis.lonlat[start : end + 1, ::-1]
        data_bounds = json.dumps([bounds.min(axis=0).tolist(), bounds.max(axis=0).tolist()])
        rows.append(
            f"<button class=\"bend\" data-bounds='{data_bounds}'>"
            f'<span class="number">{bend["id"]:02}</span>'
            f'<span><b style="color:{color}">{direction} viraj</b>'
            f"<small>{bend['start_s_m'] / 1000:.2f}–{bend['end_s_m'] / 1000:.2f} km{support}{merged_note}</small></span>"
            f'<span class="radius">≈{bend["min_radius_m"]:.0f}<small>R min · metre</small></span></button>'
        )
    m.fit_bounds(
        [np.min(coords, axis=0).tolist(), np.max(coords, axis=0).tolist()], padding=(32, 32)
    )
    title = html.escape(str(analysis.metadata.get("title", "Rota analizi")))
    m.get_root().header.add_child(Element(f"<title>Viraj · {title}</title>"))
    provider = html.escape(str(analysis.metadata.get("provider", "GeoJSON")))
    image_data = base64.b64encode(Path(chart_path).read_bytes()).decode()
    panel = f"""
    <aside id="panel">
      <div class="eyebrow">VİRAJ / ARAŞTIRMA PROTOTİPİ</div>
      <h1>{title}</h1><p class="subtitle">Rota geometrisinden yaklaşık viraj analizi</p>
      <div class="stats"><div><b>{analysis.s[-1] / 1000:.1f}</b><small>km rota</small></div>
      <div><b>{len(analysis.bends)}</b><small>viraj adayı</small></div>
      <div><b>{analysis.config.spacing_m:g}</b><small>m örnekleme üst sınırı</small></div></div>
      <div class="legend"><i style="background:#e76f35"></i> Sol
      <i style="background:#2563eb"></i> Sağ <span>Gidiş yönüne göre</span></div>
      {"".join(review_buttons)}
      <h2>Virajlar <small>Yaklaşmak için seç</small></h2>
      <div class="bend-list">{"".join(rows) or "<p>Eşiği geçen viraj bulunamadı.</p>"}</div>
      <h2>Eğrilik karşılaştırması</h2>
      <a href="analysis.png" target="_blank"><img class="chart" alt="Rota ve eğrilik grafiği"
        src="data:image/png;base64,{image_data}"></a>
      <p class="method">Hassas tespit · toplam dönüş ≥ {analysis.config.min_turn_deg:g}° ·
      L ≥ {analysis.config.min_length_m:g} m<br>
      Tespit yarıçap eşiği {analysis.config.radius_threshold_m:g} m · devam oranı {analysis.config.release_ratio:g}<br>
      Aynı yön birleştirme ≤ {analysis.config.merge_gap_m:g} m · ters dönüş toleransı {analysis.config.opposite_tolerance_deg:g}°<br>
      {provider} / OSM · {html.escape(analysis.crs)}</p>
      <p class="note">Yol geometrisinden tahmin; kavşak dönüşleri de adaylara dahildir.
      {analysis.quality["sparse_geometry_bend_count"]} adayda geometri seyrek (aday içinde 3'ten az kaynak noktası).
      Uydu ölçümüyle henüz doğrulanmadı. Harita altlığı ve kütüphaneleri internet gerektirir.</p>
      <div class="downloads"><a href="analysis.json">JSON</a>
      <a href="bends.geojson">GeoJSON</a><a href="samples.csv">Örnekler CSV</a></div>
      <div class="downloads"><a href="https://www.openstreetmap.org/fixthemap" target="_blank">OSM haritasını düzelt</a></div>
    </aside>
    """
    css = """
    <style>
    :root {font-family: Segoe UI, Arial, sans-serif; color:#182b42; background:#f5f7fa}
    * {box-sizing:border-box} body {margin:0}
    .folium-map {position:absolute!important;left:380px!important;top:0!important;
      width:calc(100% - 380px)!important;height:100%!important}
    #panel {position:absolute;left:0;top:0;width:380px;height:100%;overflow:auto;
      padding:28px 24px;background:#fff;z-index:1000;box-shadow:3px 0 18px #14233317}
    .eyebrow {font-size:10px;font-weight:700;letter-spacing:2px;color:#16866c}
    h1 {font-size:28px;letter-spacing:-1px;margin:12px 0 6px;font-weight:700}
    .subtitle {font-size:13px;color:#6a7889;margin:0 0 22px}
    .stats {display:flex;gap:12px;padding:18px 0;border-top:1px solid #e6ebf1;
      border-bottom:1px solid #e6ebf1}.stats div{flex:1}.stats b{font-size:24px;font-weight:650}
    small {display:block;font-size:10px;color:#748295;font-weight:400;margin-top:3px}
    .legend {display:flex;align-items:center;gap:7px;font-size:11px;padding:18px 0;color:#516078}
    .legend i {width:8px;height:8px;border-radius:50%;display:inline-block;margin-left:5px}
    .legend span {margin-left:auto;font-size:10px;color:#748295}
    h2 {font-size:14px;display:flex;justify-content:space-between;margin:12px 0 10px}
    h2 small {font-size:10px}.bend-list {max-height:38vh;overflow:auto}
    .bend {display:flex;align-items:center;width:100%;text-align:left;gap:12px;
      border:0;border-bottom:1px solid #edf0f5;background:#fff;padding:13px 5px;cursor:pointer;
      font-family:inherit;font-size:12px}.bend:hover {background:#f5f8fb}
    .number {color:#96a2b1;font-size:11px}.radius {margin-left:auto;text-align:right;font-weight:650}
    .review {background:#f5f0ff;border:1px solid #e9ddfb;border-radius:8px;padding:12px;color:#6b21a8}
    .chart {width:100%;border-radius:8px;border:1px solid #edf0f5;margin-top:4px}
    .method,.note {font-size:11px;color:#758195;line-height:1.7;margin:14px 0}
    .note {padding:12px;background:#f5f7fa;border-radius:8px}
    .downloads {display:flex;gap:16px;font-size:11px;padding:8px 0}
    .downloads a {color:#247b68;text-decoration:none}
    @media(max-width:700px) {.folium-map {left:0!important;width:100%!important;height:54%!important}
    #panel {top:54%;width:100%;height:46%;padding:20px}.bend-list {max-height:180px}
    h1{font-size:22px}.stats{padding:12px 0}.stats b{font-size:20px}}
    </style>
    """
    m.get_root().header.add_child(
        Element('<meta name="viewport" content="width=device-width, initial-scale=1">' + css)
    )
    m.get_root().html.add_child(Element(panel))
    m.get_root().script.add_child(
        Element(f"""
    document.querySelectorAll('.bend').forEach(button => button.addEventListener('click', () => {{
      {m.get_name()}.fitBounds(JSON.parse(button.dataset.bounds), {{padding:[40,40], maxZoom:17, animate:false}});
    }}));
    """)
    )
    m.save(str(path))


def export_analysis(analysis, output_dir, *, comparison=None):
    folder = Path(output_dir)
    folder.mkdir(parents=True, exist_ok=True)
    write_json(folder / "analysis.json", analysis.summary())
    draw_analysis(analysis, folder / "analysis.png")
    with (folder / "samples.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "s_m",
                "x_m",
                "y_m",
                "longitude",
                "latitude",
                "curvature_1pm",
                "circle_curvature_1pm",
                "valid",
                "detection_curvature_1pm",
                "detection_valid",
            ]
        )
        for i, distance in enumerate(analysis.s):
            ll = analysis.lonlat[i] if analysis.lonlat is not None else ("", "")
            writer.writerow(
                [
                    distance,
                    *analysis.xy[i],
                    *ll,
                    analysis.curvature[i],
                    analysis.circle_curvature[i]
                    if np.isfinite(analysis.circle_curvature[i])
                    else "",
                    bool(analysis.valid[i]),
                    analysis.detection_curvature[i],
                    bool(analysis.detection_valid[i]),
                ]
            )
    if analysis.lonlat is not None:
        write_json(folder / "bends.geojson", geojson(analysis))
        draw_map(analysis, folder / "map.html", folder / "analysis.png", comparison=comparison)
    return folder
