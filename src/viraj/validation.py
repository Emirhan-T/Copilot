"""Reproducible synthetic benchmarks and parameter sensitivity, without network calls."""

import csv
from dataclasses import asdict, replace
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from .core import Config, analyze_xy
from .export import export_analysis, write_json
from .synthetic import arc, demo_road, make_road


def save_csv(path, rows):
    with Path(path).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def radius_metrics(analysis, radius):
    result = {}
    for method, signal in (("heading", analysis.curvature), ("circle", analysis.circle_curvature)):
        kappa = np.abs(signal[analysis.valid])
        estimate = float(1 / np.median(kappa))
        minimum = float(1 / np.max(kappa))
        result[f"{method}_median_radius_m"] = estimate
        result[f"{method}_median_error_pct"] = abs(estimate - radius) / radius * 100
        result[f"{method}_min_radius_m"] = minimum
        result[f"{method}_min_error_pct"] = abs(minimum - radius) / radius * 100
    return result


def run_validation(output_dir, config=None):
    folder = Path(output_dir)
    folder.mkdir(parents=True, exist_ok=True)
    config = config or Config()
    cases = []
    for radius in (50.0, 100.0, 200.0, 500.0):
        for noise in (0.0, 0.15, 0.5):
            for seed in range(5):
                road = arc(radius, noise_m=noise, seed=seed)
                analysis = analyze_xy(road.xy, config)
                cases.append(
                    {
                        "truth_radius_m": radius,
                        "noise_sigma_m": noise,
                        "seed": seed,
                        **radius_metrics(analysis, radius),
                        "bend_count": len(analysis.bends),
                    }
                )
    save_csv(folder / "radius_benchmark.csv", cases)
    gentle = []
    for radius in (500, 1000, 5000, 20000, 30000):
        for sign in (-1, 1):
            for noise in (0.0, 0.15):
                road = make_road([(200, 0), (600, sign / radius), (200, 0)], noise_m=noise)
                result = analyze_xy(road.xy, config)
                expected = "left" if sign > 0 else "right"
                gentle.append(
                    {
                        "radius_m": radius,
                        "noise_sigma_m": noise,
                        "expected_direction": expected,
                        "bend_count": len(result.bends),
                        "detected_expected_bend": len(result.bends) == 1
                        and result.bends[0]["direction"] == expected,
                    }
                )
    save_csv(folder / "gentle_bends.csv", gentle)
    short_gap = analyze_xy(
        make_road([(200, 0), (200, 0.001), (150, 0), (200, 0.001), (200, 0)]).xy, config
    )
    long_gap = analyze_xy(
        make_road([(200, 0), (200, 0.001), (600, 0), (200, 0.001), (200, 0)]).xy, config
    )
    s_bend = analyze_xy(
        make_road([(200, 0), (200, 1 / 5000), (150, -1 / 5000), (200, 0)]).xy, config
    )
    straight = []
    for noise in (0.0, 0.15, 0.5):
        for seed in range(5):
            road = make_road([(1500, 0)], noise_m=noise, seed=seed)
            result = analyze_xy(road.xy, config)
            detected_length = sum(b["length_m"] for b in result.bends)
            if np.count_nonzero(result.detection_valid) < 2:
                raise ValueError("Validation configuration leaves insufficient detection support")
            evaluable_length = float(
                result.s[result.detection_valid][-1] - result.s[result.detection_valid][0]
            )
            straight.append(
                {
                    "noise_sigma_m": noise,
                    "seed": seed,
                    "bend_count": len(result.bends),
                    "false_bend_length_fraction": detected_length / evaluable_length,
                }
            )
    save_csv(folder / "straight_benchmark.csv", straight)
    sensitivity = []
    # Same noisy geometry for every combination: only the parameters change.
    fixed_road = arc(100, noise_m=0.15, seed=42)
    combined = demo_road(noise_m=0.15, seed=42)
    for spacing in (2.5, 5.0, 10.0):
        for smoothing in (25.0, 45.0, 65.0, 85.0):
            cfg = replace(config, spacing_m=spacing, smoothing_m=smoothing)
            result = analyze_xy(fixed_road.xy, cfg)
            combo = analyze_xy(combined.xy, cfg)
            sensitivity.append(
                {
                    "spacing_m": spacing,
                    "smoothing_m": smoothing,
                    **radius_metrics(result, 100),
                    "combined_bend_count": len(combo.bends),
                }
            )
    save_csv(folder / "sensitivity.csv", sensitivity)
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5), layout="constrained")
    for spacing in (2.5, 5.0, 10.0):
        selected = [r for r in sensitivity if r["spacing_m"] == spacing]
        x = [r["smoothing_m"] for r in selected]
        for ax, field in zip(
            axes, ("heading_median_error_pct", "heading_min_error_pct", "combined_bend_count")
        ):
            ax.plot(x, [r[field] for r in selected], "o-", label=f"step ≤ {spacing:g} m")
    for ax, title, ylabel in zip(
        axes,
        ("Median-radius error", "Minimum-radius error", "Straight + L + R road"),
        ("Error (%)", "Error (%)", "Bend count (truth: 2)"),
    ):
        ax.set(title=title, xlabel="Requested smoothing span (m)", ylabel=ylabel)
        ax.grid(alpha=0.2)
        ax.legend()
    fig.savefig(folder / "sensitivity.png", dpi=160)
    plt.close(fig)
    clean = [r["heading_median_error_pct"] for r in cases if r["noise_sigma_m"] == 0]
    noisy = [r["heading_median_error_pct"] for r in cases if r["noise_sigma_m"] == 0.15]
    clean_min = [r["heading_min_error_pct"] for r in cases if r["noise_sigma_m"] == 0]
    noisy_min = [r["heading_min_error_pct"] for r in cases if r["noise_sigma_m"] == 0.15]
    straight_max = max(
        r["false_bend_length_fraction"] for r in straight if r["noise_sigma_m"] <= 0.15
    )
    summary = {
        "config": asdict(config),
        "arc_cases": len(cases),
        "straight_cases": len(straight),
        "gentle_cases": len(gentle),
        "gentle_cases_detected": sum(r["detected_expected_bend"] for r in gentle),
        "noise_definition": "Independent Gaussian noise per x/y coordinate on 2 m source vertices",
        "clean_arc_max_median_radius_error_pct": max(clean),
        "noise_015m_arc_max_median_radius_error_pct": max(noisy),
        "clean_arc_max_min_radius_error_pct": max(clean_min),
        "noise_015m_arc_max_min_radius_error_pct": max(noisy_min),
        "noise_at_most_015m_straight_max_false_bend_length_fraction": straight_max,
        "synthetic_acceptance": {
            "clean_error_below_3pct": max(clean) < 3,
            "noise_015m_error_below_15pct": max(noisy) < 15,
            "straight_false_fraction_below_1pct": straight_max < 0.01,
            "gentle_bends_detected": all(r["detected_expected_bend"] for r in gentle),
            "short_gap_merged": len(short_gap.bends) == 1,
            "long_straight_kept_separate": len(long_gap.bends) == 2,
            "gentle_s_bend_kept_separate": [b["direction"] for b in s_bend.bends]
            == ["left", "right"],
        },
        "real_world_validation": "PENDING: independent satellite/survey measurements required",
    }
    write_json(folder / "summary.json", summary)
    export_analysis(
        analyze_xy(demo_road().xy, config, metadata={"title": "Synthetic L/R"}),
        folder / "synthetic_demo",
    )
    report = f"""# Doğrulama sonuçları

Konfigürasyon: {config.spacing_m:g} m örnekleme üst sınırı, {config.smoothing_m:g} m
yumuşatma, R ≤ {config.radius_threshold_m:g} m, L ≥ {config.min_length_m:g} m.
Tespit: kaynak segment yönlerinden enterpolasyon, Gaussian σ={config.detection_sigma_m:g} m;
başlatma/devam eşik oranı {config.release_ratio:g}, toplam dönüş ≥{config.min_turn_deg:g}°,
birleştirme boşluğu ≤{config.merge_gap_m:g} m ve ters dönüş bütçesi ≤{config.opposite_tolerance_deg:g}°.
Uzun kaynak segment uçlarındaki yön geçişi en fazla {config.heading_transition_cap_m:g} m'de modellenir.

## Hafif viraj ve birleştirme regresyonları

R=500/1000/5000/20000/30000 m, iki yön ve σ=0/0.15 m gürültü ile
{len(gentle)} geniş yay denemesinin {sum(r["detected_expected_bend"] for r in gentle)} tanesi beklenen tek virajı verdi.
Detaylar `gentle_bends.csv` içinde. 150 m düzleşmeli iki sol yay {len(short_gap.bends)} aday,
600 m düz yol ile ayrılan yaylar {len(long_gap.bends)} aday verdi.
Hafif S örneğinin yönleri: {[b["direction"] for b in s_bend.bends]}.
Birleştirme mesafesi, düşük eşikle bulunan aday uçları arasındaki mesafedir;
ham yoldaki düzleşmenin uzunluğuyla aynı olmak zorunda değildir.

## Yarıçap ve düz yol hataları

60 yay deneyi: R=50/100/200/500 m; x/y eksenlerinde σ=0/0.15/0.5 m gürültü;
her kombinasyonda 5 sabit rastgele tohum. Kaynak noktalar 2 m aralıklıdır.
Yarıçap hatası, geçerli iç örneklerde 1/medyan(|κ|) ile hesaplanır; en küçük
yarıçap hatası da CSV'de ayrıca bulunur. Bu iki ölçü birbirinin yerine geçmez.

| Ölçü | Sonuç |
|---|---:|
| Gürültüsüz yaylarda en büyük medyan yarıçap hatası | %{max(clean):.3f} |
| σ=0.15 m yaylarda en büyük medyan yarıçap hatası | %{max(noisy):.3f} |
| Gürültüsüz yaylarda en büyük minimum yarıçap hatası | %{max(clean_min):.3f} |
| σ=0.15 m yaylarda en büyük minimum yarıçap hatası | %{max(noisy_min):.3f} |
| σ≤0.15 m düz yolda en büyük yanlış viraj uzunluğu oranı | %{straight_max * 100:.3f} |

**Minimum yarıçap, bu deneylerde medyan yarıçap kadar kararlı değildir.**
Sentetik kabul ölçütleri medyan yarıçap ve yanlış viraj oranını sınar;
minimum yarıçap için doğruluk garantisi vermez. Mobilde keskinlik
sunumuna geçmeden önce bu ölçünün iyileştirilmesi ve bağımsız ölçüm gerekir.

![Parametre hassasiyeti](sensitivity.png)

Hassasiyet grafiğinde aynı gürültülü R=100 m yay ve düz+sol+sağ yol kullanılır;
yalnızca örnekleme ve yumuşatma değişir. Detaylar `sensitivity.csv` içindedir.
Gürültülü girdilerde kat edilen geometrik mesafe de değişebilir. Yumuşatma
keskin geçişleri yayar ve viraj sınırlarını kaydırabilir.

## Gerçek yol doğrulaması: bekliyor

Konya–Ankara haritasındaki adayların uydu görüntüsünde bağımsız ölçümü henüz
yapılmadı. İki eğrilik yönteminin yakın sonuç vermesi gerçek yarıçapı doğrulamaz:
ikisi de aynı OSM geometrisini kullanır.

`docs/real_measurements.csv` dosyasını aday ID, görüntü kaynağı/tarihi, ölçülen
kiriş uzunluğu c ve ok yüksekliği h ile doldur. Dairesel yay varsayımı altında
R=c²/(8h)+h/2. Viraj boyunca yarıçap değişiyorsa aynı yay bölgesini karşılaştır;
algoritmanın minimum yarıçapı ile tüm viraja oturtulan tek çember eşdeğer değildir.
En az 3–5 aday, düz yol ve kavşak dönüşü ayrıca incelenmeli.
"""
    (folder / "report.md").write_text(report, encoding="utf-8")
    return summary


def route_sensitivity(coords, output_dir, config=None):
    from .core import analyze_lonlat

    config = config or Config()
    folder = Path(output_dir)
    folder.mkdir(parents=True, exist_ok=True)
    rows = []
    for spacing in (2.5, 5.0, 10.0):
        for smoothing in (25.0, 45.0, 65.0, 85.0):
            result = analyze_lonlat(
                coords, replace(config, spacing_m=spacing, smoothing_m=smoothing)
            )
            rows.append(
                {
                    "spacing_m": spacing,
                    "smoothing_m": smoothing,
                    "bend_count": len(result.bends),
                    "total_bend_length_m": sum(b["length_m"] for b in result.bends),
                    "median_of_min_radii_m": float(
                        np.median([b["min_radius_m"] for b in result.bends])
                    )
                    if result.bends
                    else None,
                }
            )
    save_csv(folder / "route_sensitivity.csv", rows)
    fig, axes = plt.subplots(1, 2, figsize=(11, 4), layout="constrained")
    for spacing in (2.5, 5.0, 10.0):
        subset = [r for r in rows if r["spacing_m"] == spacing]
        for ax, key in zip(axes, ("bend_count", "total_bend_length_m")):
            ax.plot(
                [r["smoothing_m"] for r in subset],
                [r[key] for r in subset],
                "o-",
                label=f"step ≤ {spacing:g} m",
            )
    for ax, ylabel in zip(axes, ("Bend candidates", "Total bend length (m)")):
        ax.set(xlabel="Requested smoothing span (m)", ylabel=ylabel)
        ax.grid(alpha=0.2)
        ax.legend()
    fig.savefig(folder / "route_sensitivity.png", dpi=160)
    plt.close(fig)
    detection_rows = []
    for sigma in (10.0, 20.0, 30.0):
        for gap in (50.0, 100.0, 150.0):
            result = analyze_lonlat(
                coords, replace(config, detection_sigma_m=sigma, merge_gap_m=gap)
            )
            detection_rows.append(
                {
                    "detection_sigma_m": sigma,
                    "merge_gap_m": gap,
                    "bend_count": len(result.bends),
                    "merged_bend_count": sum(b["merged_part_count"] > 1 for b in result.bends),
                }
            )
    save_csv(folder / "detection_sensitivity.csv", detection_rows)
    fig, ax = plt.subplots(figsize=(7, 4), layout="constrained")
    for sigma in (10.0, 20.0, 30.0):
        selected = [r for r in detection_rows if r["detection_sigma_m"] == sigma]
        ax.plot(
            [r["merge_gap_m"] for r in selected],
            [r["bend_count"] for r in selected],
            "o-",
            label=f"detection sigma = {sigma:g} m",
        )
    ax.set(xlabel="Gap between detected parts (m)", ylabel="Bend candidates")
    ax.grid(alpha=0.2)
    ax.legend()
    fig.savefig(folder / "detection_sensitivity.png", dpi=160)
    plt.close(fig)
    return rows
