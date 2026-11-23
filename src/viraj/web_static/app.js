/* Copilot: map-based routing and angle experiments on the Python analysis core. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const colors = { left: "#db693b", right: "#437bb3" };
  const fields = {
    min_turn_deg: "min-angle", merge_gap_m: "merge-gap", min_length_m: "min-length",
    detection_sigma_m: "detection-sigma", radius_threshold_m: "radius-threshold",
    spacing_m: "spacing", smoothing_m: "smoothing", opposite_tolerance_deg: "opposite-tolerance",
  };
  const speedFields = {
    approach_speed_kmh: "approach-speed", superelevation: "superelevation",
    reaction_time_s: "reaction-time", deceleration_mps2: "deceleration",
  };
  const confidenceName = { high: "yüksek", medium: "orta", low: "düşük" };
  const state = { route: null, analysis: null, signature: null, selected: null, pick: null,
    busy: false, markers: {}, layers: new Map(), defaults: null, detailSignals: null };
  const number = (value, decimals = 0) => Number(value).toLocaleString("tr-TR", {
    minimumFractionDigits: decimals, maximumFractionDigits: decimals,
  });
  const sideName = (side) => side === "left" ? "Sol" : "Sağ";
  const map = L.map("map", { zoomControl: false, preferCanvas: true, renderer: L.canvas({ padding: 0.8 }) }).setView([38.8, 32.8], 7);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> katkıcıları',
  }).addTo(map);
  L.control.zoom({ position: "topright" }).addTo(map);
  L.control.scale({ imperial: false, position: "bottomleft" }).addTo(map);
  const routeLayer = L.layerGroup().addTo(map);
  const bendLayer = L.layerGroup().addTo(map);
  new ResizeObserver(() => map.invalidateSize({pan:false})).observe($("map"));
  let routeLine = null;

  function status(text, pending = false) {
    $("status").classList.toggle("pending", pending);
    $("status").querySelector("span").textContent = text;
  }
  function showError(text) { $("error").textContent = text; $("error").hidden = !text; }
  function parsePoint(id) {
    const text = $(id).value.trim();
    const osm = text.match(/#map=\d+(?:\.\d+)?\/(-?\d+(?:\.\d+)?)\/(-?\d+(?:\.\d+)?)/);
    const parts = osm ? [osm[1], osm[2]] : text.split(/[,;\s]+/).filter(Boolean);
    const lat = Number(parts[0]), lon = Number(parts[1]);
    if (parts.length !== 2 || !Number.isFinite(lat) || !Number.isFinite(lon) || lat < -80 || lat > 84 || lon < -180 || lon > 180) {
      throw new Error("Koordinatı enlem, boylam sırasıyla yaz. Örnek: 38.10124, 32.73005");
    }
    return { lat, lon };
  }
  function endpoints() { return { start: parsePoint("start-coordinates"), end: parsePoint("end-coordinates") }; }
  function config() { return Object.fromEntries(Object.entries(fields).map(([key, id]) => [key, Number($(id).value)])); }
  function speedConfig() { return Object.fromEntries(Object.entries(speedFields).map(([key, id]) => [key, Number($(id).value)])); }
  function signature() { return JSON.stringify({ ...endpoints(), config: config(), speed: speedConfig() }); }
  function speedOf(id) { return state.analysis?.summary.speed?.bends.find((item) => item.bend_id === id); }
  function setExportEnabled(enabled) {
    ["download-json", "download-geojson", "download-csv"].forEach((id) => { $(id).disabled = !enabled; });
  }
  function refreshDirty() {
    let dirty = true;
    try { dirty = signature() !== state.signature; } catch { /* incomplete coordinate edit */ }
    document.querySelector(".workspace").classList.toggle("stale", Boolean(state.analysis && dirty));
    setExportEnabled(Boolean(state.analysis && !dirty && !state.busy));
    state.layers.forEach((layer) => layer.setStyle({ opacity: dirty ? 0.45 : 0.95 }));
    if (state.analysis && dirty && !state.busy) {
      status("Ayar değişti · ekranda önceki testin sonucu var.", true);
      $("run-label").textContent = "Yeniden analiz et";
    } else if (state.analysis && !state.busy) {
      status(`Test tamamlandı · ${state.analysis.elapsed_ms} ms · açı ≥ ${number(state.analysis.summary.config.min_turn_deg, 2)}°`);
      $("run-label").textContent = "Rotayı analiz et";
    }
  }
  function angleUI() {
    const value = Number($("min-angle").value);
    const range = $("angle-slider");
    range.value = Math.min(Number(range.max), Math.max(Number(range.min), value || 0));
    const percent = (Number(range.value) - Number(range.min)) / (Number(range.max) - Number(range.min)) * 100;
    range.style.background = `linear-gradient(to right,var(--teal) ${percent}%,#e5ece6 ${percent}%)`;
    document.querySelectorAll("[data-angle]").forEach((button) => {
      button.classList.toggle("active", Number(button.dataset.angle) === value);
      button.setAttribute("aria-pressed", String(Number(button.dataset.angle) === value));
    });
  }
  function setPick(which) {
    state.pick = which;
    $("pick-hint").hidden = !which;
    document.querySelector(".map-section").classList.toggle("picking", Boolean(which));
    $("pick-start").classList.toggle("active", which === "start");
    $("pick-end").classList.toggle("active", which === "end");
    $("pick-start").setAttribute("aria-pressed", String(which === "start"));
    $("pick-end").setAttribute("aria-pressed", String(which === "end"));
    if (which) {
      $("pick-hint-text").textContent = which === "start" ? "Haritadan başlangıç noktasını seç" : "Haritadan bitiş noktasını seç";
      if (window.innerWidth <= 800) $("map").scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }
  function updateMarkers() {
    let points;
    try { points = endpoints(); } catch { return; }
    for (const [which, point] of Object.entries(points)) {
      if (state.markers[which]) { state.markers[which].setLatLng(point); continue; }
      const marker = L.marker(point, { draggable: true,
        title: which === "start" ? "A · Başlangıç (sürüklenebilir)" : "B · Bitiş (sürüklenebilir)",
        icon: L.divIcon({ className: "", html: `<span class="endpoint-marker ${which === "end" ? "end" : ""}">${which === "start" ? "A" : "B"}</span>`, iconSize: [28, 28], iconAnchor: [14, 14] }),
      }).addTo(map);
      marker.on("dragend", () => {
        const p = marker.getLatLng();
        $(`${which}-coordinates`).value = `${p.lat.toFixed(5)}, ${p.lng.toFixed(5)}`;
        refreshDirty();
      });
      state.markers[which] = marker;
    }
  }
  function setBusy(busy) {
    state.busy = busy;
    document.querySelectorAll("#test-form input,#test-form button").forEach((element) => { element.disabled = busy; });
    $("run").classList.toggle("busy", busy);
    $("run").setAttribute("aria-busy", String(busy));
    Object.values(state.markers).forEach((marker) => busy ? marker.dragging.disable() : marker.dragging.enable());
    if (busy) { setPick(null); setExportEnabled(false); }
  }
  async function api(path, body) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 100000);
    try {
      const response = await fetch(path, { method: body === undefined ? "GET" : "POST",
        headers: body === undefined ? {} : { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body), signal: controller.signal });
      const data = await response.json();
      if (!response.ok) {
        const error = new Error(typeof data.detail === "string" ? data.detail : "Test ayarlarını ve alan sınırlarını kontrol et.");
        error.status = response.status; throw error;
      }
      return data;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("İstek zaman aşımına uğradı. Daha sonra yeniden dene.");
      if (error instanceof TypeError) throw new Error("Yerel analiz servisine ulaşılamadı. Sunucunun açık olduğunu kontrol et.");
      throw error;
    } finally { clearTimeout(timer); }
  }
  function fitRoute() {
    state.selected = null; state.detailSignals = null;
    $("selected-bend").hidden = true;
    styleSelection(); renderRows(); drawChart();
    if (routeLine) map.fitBounds(routeLine.getBounds(), { paddingTopLeft: [35, 105], paddingBottomRight: [35, 55], animate: false });
    else {
      try { const points = endpoints(); map.fitBounds([points.start, points.end], { padding: [50, 50], maxZoom: 13, animate: false }); } catch { /* wait for valid input */ }
    }
  }
  function styleSelection() {
    state.layers.forEach((layer, id) => {
      const selected = id === state.selected?.id;
      layer.setStyle({ weight: selected ? 8 : 5, opacity: state.selected && !selected ? 0.4 : 0.95 });
      if (selected) layer.bringToFront();
    });
  }
  async function selectBend(id, scrollRow = true) {
    if (!state.analysis) return;
    const bend = state.analysis.summary.bends.find((item) => item.id === id);
    if (!bend) return;
    state.selected = bend; state.detailSignals = null;
    styleSelection(); renderRows();
    if (scrollRow) document.querySelector(`[data-bend-id="${id}"]`)?.scrollIntoView({ block: "nearest" });
    const layer = state.layers.get(id);
    if (layer) map.fitBounds(layer.getBounds(), { padding: [55, 55], maxZoom: 17, animate: false });
    const panel = $("selected-bend");
    panel.hidden = false;
    panel.innerHTML = `<button type="button" class="close-selection" aria-label="Viraj seçimini kaldır">×</button><strong style="color:${colors[bend.direction]}">#${String(id).padStart(3, "0")} · ${sideName(bend.direction)} viraj</strong><p>${number(bend.start_s_m / 1000, 2)}–${number(bend.end_s_m / 1000, 2)} km · ${number(bend.length_m)} m<br>${number(Math.abs(bend.turn_angle_deg), 2)}° toplam dönüş · min. yarıçap ≈ ${number(bend.min_radius_m)} m</p>${bend.merged_part_count > 1 ? `<em>${bend.merged_part_count} tespit parçası birleştirildi.</em>` : ""}${bend.geometry_support === "sparse" ? "<em>Kaynak geometri seyrek; sınırlar ve yarıçap yaklaşık.</em>" : ""}${speedDetail(speedOf(id))}`;
    panel.querySelector("button").addEventListener("click", fitRoute);
    drawChart();
    const analysisId = state.analysis.analysis_id;
    const start = Math.max(0, bend.start_s_m - 150), end = Math.min(state.analysis.summary.route_length_m, bend.end_s_m + 150);
    try {
      const detail = await api(`/api/analyses/${analysisId}/signals?start_s_m=${start}&end_s_m=${end}`);
      if (state.analysis?.analysis_id === analysisId && state.selected?.id === id) {
        state.detailSignals = detail.signals; drawChart();
      }
    } catch { /* The global plot remains available if a detail request expires. */ }
  }
  function speedDetail(speed) {
    if (!speed) return "";
    const series = state.analysis.summary.speed.series.find((item) => item.id === speed.series_id);
    const head = series
      ? `<b class="speed-warn">⚠ Hedef ${number(speed.target_speed_kmh)} km/s · seri S${series.id}</b><br>${series.message}<br>Uyarı yalnızca hız aşılıyorsa; ${number(state.analysis.summary.speed.config.approach_speed_kmh)} km/s ile gelene ${number(series.first_core_start_s_m - series.trigger_s_at_approach_m)} m önce`
      : `<b class="speed-ok">Güvenli hız ${speed.target_is_unbounded ? "200+" : number(speed.target_speed_kmh)} km/s · referans hızda yavaşlama gerekmez</b>`;
    return `<p>${head}<br>Hız yarıçapı ≈ ${number(speed.radius_m)} m (${speed.radius_basis}) · güven: ${confidenceName[speed.confidence]}</p>`;
  }
  function speedCell(speed) {
    if (!speed) return "—";
    if (speed.target_is_unbounded) return `<span class="speed-ok">200+</span>`;
    if (!speed.requires_slowdown) return `<span class="speed-ok">${number(speed.target_speed_kmh)}</span>`;
    return `<span class="speed-warn" title="Uyarı serisi S${speed.series_id} · güven: ${confidenceName[speed.confidence]}">⚠ ${number(speed.target_speed_kmh)} km/s · S${speed.series_id}</span>`;
  }
  function renderRows() {
    const rows = $("bend-rows"); rows.replaceChildren();
    if (!state.analysis) return;
    const direction = $("direction").value;
    const bends = state.analysis.summary.bends.filter((bend) => direction === "all" || bend.direction === direction || (direction === "warn" && speedOf(bend.id)?.requires_slowdown));
    $("list-count").textContent = number(bends.length);
    $("empty-state").hidden = bends.length > 0;
    if (!bends.length) {
      const insufficient = state.analysis.summary.quality.detection_valid_sample_fraction === 0;
      $("empty-state").querySelector("p").textContent = insufficient
        ? "Rota bu ayarlarda değerlendirmek için çok kısa." : "Bu ayarlarda viraj adayı bulunamadı.";
      $("empty-state").querySelector("small").textContent = insufficient
        ? "Daha uzun bir rota seç; tespit penceresi için yeterli iç örnek yok."
        : "Dönüş açısını düşürebilir veya yön filtresini değiştirebilirsin.";
    }
    const fragment = document.createDocumentFragment();
    for (const bend of bends) {
      const row = document.createElement("tr");
      row.dataset.bendId = bend.id;
      row.classList.toggle("is-selected", bend.id === state.selected?.id);
      row.innerHTML = `<td><button class="row-select" type="button" aria-label="Viraj ${bend.id} ayrıntıları">${String(bend.id).padStart(3, "0")}</button></td><td><span class="side-tag ${bend.direction}"><i></i>${sideName(bend.direction)}</span></td><td>${number(bend.start_s_m / 1000, 2)}–${number(bend.end_s_m / 1000, 2)} km</td><td>${number(Math.abs(bend.turn_angle_deg), 2)}°</td><td>${number(bend.length_m)} m</td><td>${number(bend.min_radius_m)} m</td><td>${speedCell(speedOf(bend.id))}</td><td>${bend.merged_part_count > 1 ? `<span class="merge-tag">${bend.merged_part_count} parça</span>` : "—"}</td>`;
      row.addEventListener("click", () => selectBend(bend.id, false));
      fragment.append(row);
    }
    rows.append(fragment);
  }
  function showResults(data) {
    state.analysis = data;
    state.selected = null; state.detailSignals = null;
    $("selected-bend").hidden = true;
    state.layers.clear(); bendLayer.clearLayers();
    for (const feature of data.geojson.features) {
      if (feature.properties.kind !== "bend") continue;
      const bend = feature.properties;
      const coordinates = feature.geometry.coordinates.map(([lon, lat]) => [lat, lon]);
      const layer = L.polyline(coordinates, { color: colors[bend.direction], weight: 5, opacity: 0.95, smoothFactor: 0 });
      layer.bindTooltip(`#${bend.id} · ${sideName(bend.direction)} · ${number(Math.abs(bend.turn_angle_deg), 2)}° · ${number(bend.length_m)} m`, { sticky: true });
      layer.on("click", (event) => { if (!state.pick) { L.DomEvent.stopPropagation(event); selectBend(bend.id); } });
      layer.addTo(bendLayer); state.layers.set(bend.id, layer);

    }
    $("route-length").innerHTML = `${number(data.summary.route_length_m / 1000, 1)}<small> km</small>`;
    $("bend-count").textContent = number(data.summary.bend_count);
    $("merge-count").textContent = number(data.summary.bends.filter((bend) => bend.merged_part_count > 1).length);
    $("warning-count").textContent = data.summary.speed ? number(data.summary.speed.series_count) : "—";
    drive.load(data);
    for (const series of data.summary.speed?.series || []) {
      if (!series.trigger_lonlat) continue;
      L.circleMarker([series.trigger_lonlat[1], series.trigger_lonlat[0]], { radius: 6, color: "#b42318", weight: 2, fillColor: "#fff", fillOpacity: 1 })
        .bindTooltip(`S${series.id} · ${series.message}<br>Hız aşılıyorsa burada uyarılır`)
        .addTo(bendLayer);
    }
    const left = data.summary.bends.filter((bend) => bend.direction === "left").length;
    $("result-meta").textContent = `${number(left)} sol · ${number(data.summary.bend_count - left)} sağ · sonuç açısı ≥ ${number(data.summary.config.min_turn_deg, 2)}°`;
    renderRows(); drawChart();
  }
  async function run(event) {
    event?.preventDefault();
    if (state.busy) return;
    showError("");
    document.querySelectorAll(".coordinate-row input").forEach((input) => input.setCustomValidity(""));
    const invalid = $("test-form").querySelector(":invalid");
    if (invalid) { if (invalid.closest("details")) invalid.closest("details").open = true; $("test-form").reportValidity(); return; }
    let points;
    try {
      for (const id of ["start-coordinates", "end-coordinates"]) {
        try { parsePoint(id); } catch (error) { $(id).setCustomValidity(error.message); $(id).reportValidity(); throw error; }
      }
      points = endpoints();
    } catch (error) { showError(error.message); return; }
    const testSignature = signature(), settings = config(), speedSettings = speedConfig();
    setBusy(true); updateMarkers();
    try {
      const routeKey = JSON.stringify(points);
      if (state.route?.key !== routeKey) {
        status("Rota geometrisi alınıyor…"); $("run-label").textContent = "Rota alınıyor…";
        const route = await api("/api/routes", points);
        state.route = { ...route, key: routeKey };
        state.analysis = null; state.layers.clear(); bendLayer.clearLayers(); routeLayer.clearLayers(); drive.reset();
        $("bend-rows").replaceChildren(); $("selected-bend").hidden = true;
        $("route-length").innerHTML = route.metadata.provider_distance_m
          ? `${number(route.metadata.provider_distance_m / 1000, 1)}<small> km</small>` : "—";
        $("bend-count").textContent = "—"; $("merge-count").textContent = "—"; $("warning-count").textContent = "—";
        $("list-count").textContent = "0"; $("result-meta").textContent = "Bu rota için analiz bekleniyor.";
        $("empty-state").hidden = false;
        $("empty-state").querySelector("p").textContent = "Virajlar hesaplanıyor…";
        $("empty-state").querySelector("small").textContent = "Sonuçlar hazır olunca burada listelenecek.";
        routeLine = L.polyline(route.geometry.coordinates.map(([lon, lat]) => [lat, lon]), { color: "#8a9c98", weight: 4, opacity: 0.7, smoothFactor: 0 }).addTo(routeLayer);
        fitRoute();
      }
      status("Yön değişimleri hesaplanıyor, aynı yönlü parçalar birleştiriliyor…");
      $("run-label").textContent = "Virajlar hesaplanıyor…";
      let result;
      try { result = await api("/api/analyze", { route_id: state.route.route_id, config: settings, speed: speedSettings }); }
      catch (error) {
        if (error.status !== 404) throw error;
        // A restarted server or an older tab may have lost its in-memory route ID.
        const refreshed = await api("/api/routes", points);
        state.route = { ...refreshed, key: routeKey };
        result = await api("/api/analyze", { route_id: state.route.route_id, config: settings, speed: speedSettings });
      }
      state.signature = testSignature;
      showResults(result);
    } catch (error) {
      showError(error.message); status("Test tamamlanamadı. Bilgileri kontrol edip yeniden deneyebilirsin.", true);
    } finally {
      setBusy(false); $("run-label").textContent = "Rotayı analiz et";
      if (state.analysis) refreshDirty();
    }
  }

  // Lightweight scientific plot, with per-bend full-resolution requests on selection.
  function plotDomain() {
    if (!state.analysis) return [0, 1];
    const bend = state.selected;
    return bend ? [Math.max(0, bend.start_s_m - 150), Math.min(state.analysis.summary.route_length_m, bend.end_s_m + 150)] : [0, state.analysis.summary.route_length_m];
  }
  function drawChart() {
    if ($("chart-panel").hidden) return;
    const canvas = $("curvature-chart"), rect = canvas.getBoundingClientRect();
    if (rect.width < 10 || rect.height < 10) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(rect.width * dpr); canvas.height = Math.round(rect.height * dpr);
    const context = canvas.getContext("2d"); context.scale(dpr, dpr);
    const w = rect.width, h = rect.height, padding = { left: 50, right: 12, top: 12, bottom: 23 };
    const [start, end] = plotDomain(), signals = state.detailSignals || state.analysis?.signals;
    if (!signals) return;
    const detection = signals.detection.filter(([s]) => s >= start && s <= end);
    const heading = $("show-radius-signal").checked ? signals.heading.filter(([s]) => s >= start && s <= end) : [];
    const maximum = Math.max(0.00001, ...detection.map((p) => Math.abs(p[1])), ...heading.map((p) => Math.abs(p[1]))) * 1.15;
    const pw = w - padding.left - padding.right, ph = h - padding.top - padding.bottom;
    const x = (s) => padding.left + (s - start) / Math.max(1e-8, end - start) * pw;
    const y = (k) => padding.top + ph / 2 - k / maximum * ph / 2;
    context.clearRect(0, 0, w, h); context.font = "9px Segoe UI, sans-serif";
    for (let i = 0; i <= 4; i++) {
      const value = maximum * (1 - i / 2), py = y(value);
      context.strokeStyle = i === 2 ? "#cddbd1" : "#edf1eb"; context.lineWidth = 1;
      context.beginPath(); context.moveTo(padding.left, py); context.lineTo(w - padding.right, py); context.stroke();
      context.fillStyle = "#99ac9c"; context.textAlign = "right";
      context.fillText(Math.abs(value) < 1e-9 ? "0" : value.toExponential(1), padding.left - 7, py + 3);
    }
    if (state.selected) {
      context.fillStyle = `${colors[state.selected.direction]}0d`;
      context.fillRect(x(state.selected.start_s_m), padding.top, x(state.selected.end_s_m) - x(state.selected.start_s_m), ph);
    }
    for (let i = 0; i <= 5; i++) {
      const position = start + (end - start) * i / 5;
      context.fillStyle = "#99ac9c"; context.textAlign = "center";
      context.fillText(`${number(position / 1000, end - start < 3000 ? 2 : 0)} km`, x(position), h - 5);
    }
    if (heading.length) {
      context.strokeStyle = "#b2c6bb"; context.lineWidth = 1;
      context.beginPath(); heading.forEach(([s, k], i) => i ? context.lineTo(x(s), y(k)) : context.moveTo(x(s), y(k))); context.stroke();
    }
    context.lineWidth = 1.4;
    for (let i = 1; i < detection.length; i++) {
      context.strokeStyle = colors[detection[i][1] >= 0 ? "left" : "right"];
      context.beginPath(); context.moveTo(x(detection[i - 1][0]), y(detection[i - 1][1]));
      context.lineTo(x(detection[i][0]), y(detection[i][1])); context.stroke();
    }
    $("chart-scope").textContent = `${state.selected ? `#${state.selected.id} ayrıntısı` : "Tüm rota"} · eğrilik (1/m)`;
  }
  $("curvature-chart").addEventListener("mousemove", (event) => {
    if (!state.analysis) return;
    const canvas = event.currentTarget, rect = canvas.getBoundingClientRect();
    const [start, end] = plotDomain();
    const target = start + Math.max(0, Math.min(1, (event.clientX - rect.left - 50) / (rect.width - 62))) * (end - start);
    const points = (state.detailSignals || state.analysis.signals).detection;
    if (!points.length) return;
    let lo = 0, hi = points.length - 1;
    while (lo < hi) { const mid = Math.floor((lo + hi) / 2); if (points[mid][0] < target) lo = mid + 1; else hi = mid; }
    const point = points[lo], tooltip = $("chart-tooltip");
    tooltip.hidden = false; tooltip.textContent = `${number(point[0] / 1000, 2)} km · κ = ${point[1].toExponential(3)} /m`;
    tooltip.style.left = `${Math.max(3, Math.min(rect.width - 190, event.clientX - rect.left + 10))}px`;
  });
  $("curvature-chart").addEventListener("mouseleave", () => { $("chart-tooltip").hidden = true; });
  new ResizeObserver(() => drawChart()).observe($("curvature-chart").parentElement);
  function switchTab(which) {
    const chart = which === "chart";
    $("chart-panel").hidden = !chart; $("list-panel").hidden = chart;
    for (const name of ["list", "chart"]) {
      $(`${name}-tab`).classList.toggle("active", name === which);
      $(`${name}-tab`).setAttribute("aria-selected", String(name === which));
    }
    requestAnimationFrame(drawChart);
  }
  function saveFile(value, filename, type = "application/json") {
    const url = URL.createObjectURL(new Blob([value], { type }));
    const link = document.createElement("a"); link.href = url; link.download = filename;
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  $("download-json").addEventListener("click", () => state.analysis && saveFile(JSON.stringify(state.analysis.summary, null, 2), "copilot-analysis.json"));
  $("download-geojson").addEventListener("click", () => state.analysis && saveFile(JSON.stringify(state.analysis.geojson), "copilot-bends.geojson", "application/geo+json"));
  $("download-csv").addEventListener("click", async () => {
    if (!state.analysis) return;
    try {
      const response = await fetch(`/api/analyses/${state.analysis.analysis_id}/samples.csv`);
      if (!response.ok) throw new Error("Örnekler indirilemedi. Analizi yeniden çalıştır.");
      saveFile(await response.text(), "copilot-samples.csv", "text/csv;charset=utf-8");
    } catch (error) { showError(error.message); }
  });
  $("test-form").addEventListener("submit", run);
  $("test-form").addEventListener("input", (event) => {
    if (event.target.matches(".coordinate-row input")) { event.target.setCustomValidity(""); updateMarkers(); }
    if (event.target.id === "angle-slider") $("min-angle").value = event.target.value;
    angleUI(); refreshDirty();
  });
  document.querySelectorAll("[data-angle]").forEach((button) => button.addEventListener("click", () => {
    $("min-angle").value = button.dataset.angle; angleUI(); refreshDirty();
  }));
  $("reset-settings").addEventListener("click", () => {
    if (state.defaults) Object.entries(fields).forEach(([key, id]) => { $(id).value = state.defaults.config[key]; });
    if (state.defaults?.speed_config) Object.entries(speedFields).forEach(([key, id]) => { $(id).value = state.defaults.speed_config[key]; });
    angleUI(); refreshDirty();
  });
  $("preset").addEventListener("click", () => {
    const preset = state.defaults?.presets[0]; if (!preset) return;
    for (const which of ["start", "end"]) {
      $(`${which}-coordinates`).value = `${preset[which].lat.toFixed(5)}, ${preset[which].lon.toFixed(5)}`;
      $(`${which}-coordinates`).setCustomValidity("");
    }
    updateMarkers(); refreshDirty();
  });
  $("swap").addEventListener("click", () => {
    const a = $("start-coordinates"), b = $("end-coordinates");
    [a.value, b.value] = [b.value, a.value]; a.setCustomValidity(""); b.setCustomValidity("");
    updateMarkers(); refreshDirty();
  });
  $("pick-start").addEventListener("click", () => setPick(state.pick === "start" ? null : "start"));
  $("pick-end").addEventListener("click", () => setPick(state.pick === "end" ? null : "end"));
  $("cancel-pick").addEventListener("click", () => setPick(null));
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") setPick(null); });
  map.on("click", (event) => {
    if (!state.pick || state.busy) return;
    const which = state.pick;
    $(`${which}-coordinates`).value = `${event.latlng.lat.toFixed(5)}, ${event.latlng.lng.toFixed(5)}`;
    $(`${which}-coordinates`).setCustomValidity("");
    updateMarkers(); refreshDirty(); setPick(which === "start" ? "end" : null);
  });
  $("fit-route").addEventListener("click", fitRoute);
  $("direction").addEventListener("change", renderRows);
  $("list-tab").addEventListener("click", () => switchTab("list"));
  $("chart-tab").addEventListener("click", () => switchTab("chart"));
  $("show-radius-signal").addEventListener("change", drawChart);

  /* ---- Drive simulator: a car follows the route at a chosen speed ---- */
  const drive = (() => {
    const d = { track: null, bends: [], length: 0, s: 0, playing: false, last: null,
      marker: null, highlighted: null, lastPan: 0, frame: null };
    const speedKmh = () => Number($("drive-speed").value);
    const threshold = () => Number($("drive-threshold").value);
    const params = () => {
      const c = state.analysis.summary.speed.config;
      return { a: c.deceleration_mps2, tr: c.reaction_time_s, tol: c.overspeed_tolerance_kmh };
    };
    const distanceText = (m) => m >= 1000 ? `${number(m / 1000, 1)} km` : m >= 200 ? `${number(Math.round(m / 10) * 10)} m` : `${number(Math.round(m))} m`;
    const carIcon = () => L.divIcon({ className: "vehicle-icon", iconSize: [26, 26], iconAnchor: [13, 13],
      html: '<svg viewBox="0 0 26 26"><g id="vehicle-rot"><circle cx="13" cy="13" r="11" fill="#19323b" stroke="#fff" stroke-width="2"/><path d="M13 5 L18.5 17 L13 14.3 L7.5 17 Z" fill="#ffa573"/></g></svg>' });

    function locate(s) {
      const S = d.track.s, P = d.track.lonlat;
      let lo = 0, hi = S.length - 1;
      while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (S[mid] <= s) lo = mid; else hi = mid; }
      const f = S[hi] > S[lo] ? Math.min(1, Math.max(0, (s - S[lo]) / (S[hi] - S[lo]))) : 0;
      const lon = P[lo][0] + f * (P[hi][0] - P[lo][0]), lat = P[lo][1] + f * (P[hi][1] - P[lo][1]);
      const dx = (P[hi][0] - P[lo][0]) * Math.cos(lat * Math.PI / 180), dy = P[hi][1] - P[lo][1];
      return { lat, lon, heading: Math.atan2(dx, dy) * 180 / Math.PI };
    }

    function arrowShape(turnDeg, safeKmh, direction) {
      // Sharper (lower safe speed) -> tighter radius; larger turn -> longer arc.
      const sign = direction === "right" ? 1 : -1;
      const theta = Math.min(Math.abs(turnDeg), 170) * Math.PI / 180;
      const radius = 10 + 55 * Math.min(1, Math.max(0, (safeKmh - 20) / 130));
      let h = -Math.PI / 2, x = 0, y = 0;
      const points = [[x, y]];
      y -= 24; points.push([x, y]);
      const steps = 28;
      for (let i = 0; i < steps; i++) {
        h += sign * theta / steps;
        x += radius * theta / steps * Math.cos(h); y += radius * theta / steps * Math.sin(h);
        points.push([x, y]);
      }
      x += 10 * Math.cos(h); y += 10 * Math.sin(h); points.push([x, y]);
      const tip = [x + 16 * Math.cos(h), y + 16 * Math.sin(h)];
      const wing = (k) => [x + k * 11 * -Math.sin(h), y + k * 11 * Math.cos(h)];
      const head = [wing(1), tip, wing(-1)];
      const all = points.concat(head);
      const xs = all.map((p) => p[0]), ys = all.map((p) => p[1]);
      const size = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)) + 28;
      const cx = (Math.max(...xs) + Math.min(...xs)) / 2, cy = (Math.max(...ys) + Math.min(...ys)) / 2;
      const view = `${(cx - size / 2).toFixed(1)} ${(cy - size / 2).toFixed(1)} ${size.toFixed(1)} ${size.toFixed(1)}`;
      const fmt = (p) => `${p[0].toFixed(1)} ${p[1].toFixed(1)}`;
      return { view, width: (size * 0.085).toFixed(2),
        path: "M" + points.map(fmt).join(" L"), head: `M${head.map(fmt).join(" L")} Z` };
    }

    function setArrow(shape) {
      $("drive-arrow").setAttribute("viewBox", shape.view);
      $("drive-arrow-path").setAttribute("d", shape.path);
      $("drive-arrow-path").setAttribute("stroke-width", shape.width);
      $("drive-arrow-head").setAttribute("d", shape.head);
    }

    function nextBend() {
      return d.bends.find((r) => r.target_speed_kmh < threshold() && r.bend.end_s_m > d.s) || null;
    }

    function assess(vKmh) {
      // Same rule as OverspeedMonitor: compare with the braking envelope of every
      // bend ahead, measured from the end of the reaction distance.
      const { a, tr, tol } = params(), v = vKmh / 3.6, look = d.s + v * tr;
      let binding = null, allowed = Infinity;
      for (const r of d.bends) {
        if (r.core_end_s_m < d.s || r.target_is_unbounded) continue;
        if (r.core_start_s_m - d.s > 4000) break;
        const vt = r.target_speed_kmh / 3.6;
        const room = Math.max(0, r.core_start_s_m - look);
        const limit = Math.sqrt(vt * vt + 2 * a * room) * 3.6;
        if (limit < allowed) { allowed = limit; binding = r; }
      }
      return { allowed, binding, exceeding: vKmh > allowed + tol, tol, a, tr };
    }

    function render() {
      if (!d.track) return;
      const v = speedKmh(), here = locate(d.s);
      d.marker.setLatLng([here.lat, here.lon]);
      const rot = d.marker.getElement()?.querySelector("#vehicle-rot");
      if (rot) rot.setAttribute("transform", `rotate(${here.heading.toFixed(1)} 13 13)`);
      $("drive-position").value = Math.round(d.s);
      $("drive-km").textContent = `${number(d.s / 1000, 1)} km`;
      $("drive-speed-value").innerHTML = `${number(v)} <small>km/s</small>`;
      $("drive-speed").style.setProperty("--fill", `${(v / 200) * 100}%`);

      const next = nextBend(), hud = $("drive-hud"), alert = $("drive-alert");
      highlight(next);
      if (!next) {
        setArrow(arrowShape(0, 200, "left"));
        hud.dataset.state = "idle";
        $("drive-next-label").textContent = "SONRAKİ VİRAJ";
        $("drive-distance").textContent = "—";
        $("drive-bend-info").textContent = `Rotanın kalanında ${threshold()} km/s altı viraj yok`;
        $("drive-safe").textContent = "—";
        alert.className = "drive-alert ok"; alert.textContent = "";
        return;
      }
      const bend = next.bend, inside = d.s >= bend.start_s_m;
      setArrow(arrowShape(bend.turn_angle_deg, next.target_speed_kmh, bend.direction));
      $("drive-next-label").textContent = inside ? "VİRAJDASIN" : "SONRAKİ VİRAJA";
      $("drive-distance").textContent = inside ? `${number(Math.max(0, bend.end_s_m - d.s))} m kaldı` : distanceText(bend.start_s_m - d.s);
      const after = d.bends.filter((r) => r !== next && r.target_speed_kmh < threshold()
        && r.bend.start_s_m > bend.start_s_m && r.bend.start_s_m - bend.end_s_m < 300);
      $("drive-bend-info").textContent = `#${bend.id} · ${sideName(bend.direction)} · ${number(Math.abs(bend.turn_angle_deg))}° · R≈${number(next.radius_m)} m`
        + (after.length ? ` · ardından ${after.length} viraj` : "");
      $("drive-safe").textContent = number(next.target_speed_kmh);

      const check = assess(v);
      if (check.exceeding) {
        const b = check.binding;
        hud.dataset.state = "danger"; alert.className = "drive-alert";
        alert.textContent = `YAVAŞLA → ${number(b.target_speed_kmh)} km/s (#${b.bend_id}, ${distanceText(Math.max(0, b.bend.start_s_m - d.s))})`;
      } else if (v > next.target_speed_kmh + check.tol) {
        const ms = v / 3.6, vt = next.target_speed_kmh / 3.6;
        const need = ms * check.tr + (ms * ms - vt * vt) / (2 * check.a);
        hud.dataset.state = "caution"; alert.className = "drive-alert caution";
        alert.textContent = `Bu hız virajı aşıyor · fren için ~${number(need)} m gerekir`;
      } else {
        hud.dataset.state = "ok"; alert.className = "drive-alert ok";
        alert.textContent = "Bu hızla viraj güvenli";
      }
      if ($("drive-follow").checked && d.playing) followFrame(here, d.dt || 0.016, performance.now());
    }

    function screenAnchor() {
      // Where the car should sit: centred horizontally, in the middle of the
      // map area that the drive panel does not cover.
      const panel = $("drive-panel").getBoundingClientRect(), box = $("map").getBoundingClientRect();
      const free = Math.max(120, Math.min(box.height, panel.top - box.top));
      return L.point(box.width / 2, free / 2);
    }

    function follow(here) {
      // One-off recentre (paused, jumps): normal Leaflet pan.
      const anchor = screenAnchor(), size = map.getSize();
      const point = map.project([here.lat, here.lon], map.getZoom())
        .add(L.point(size.x / 2, size.y / 2).subtract(anchor));
      map.panTo(map.unproject(point, map.getZoom()), { animate: false });
    }

    function followFrame(here, dt, now) {
      // Per-frame camera lock: move the map pane by whole pixels so the car
      // stays fixed on screen and the map glides underneath it. Leaflet's own
      // pan animation works the same way (pane transform + "move" events).
      if (map._animatingZoom || d.dragging) return;
      const pane = map.getPane("mapPane");
      const current = L.DomUtil.getPosition(pane);
      const car = map.latLngToLayerPoint([here.lat, here.lon]).round();
      const target = screenAnchor().subtract(car).round();
      let delta = target.subtract(current);
      const far = Math.hypot(delta.x, delta.y);
      if (far > 600) { follow(here); return; }          // after a jump: snap
      if (far > 2) {                                     // ease in, then lock
        const k = 1 - Math.exp(-dt * 8);
        delta = far > 40 ? delta.multiplyBy(k).round() : delta;
      }
      if (delta.x || delta.y) {
        L.DomUtil.setPosition(pane, current.add(delta));
        map.fire("move");
      }
      // Redraw vector layers and load tiles a few times a second, not per frame.
      if (now - d.lastPan > 200) { d.lastPan = now; map.fire("moveend"); }
    }

    function highlight(next) {
      const id = next ? next.bend_id : null;
      if (id === d.highlighted) return;
      if (d.highlighted !== null) state.layers.get(d.highlighted)?.setStyle({ weight: 5 });
      d.highlighted = id;
      if (id !== null) state.layers.get(id)?.setStyle({ weight: 10 });
    }

    function tick(now) {
      if (!d.playing) return;
      const dt = d.last === null ? 0 : Math.min(0.25, (now - d.last) / 1000);
      d.last = now; d.dt = dt;
      d.s = Math.min(d.length, d.s + speedKmh() / 3.6 * dt * Number($("drive-rate").value));
      render();
      if (d.s >= d.length) { stop(); return; }
      d.frame = requestAnimationFrame(tick);
    }
    function play() {
      if (!d.track) return;
      if (d.s >= d.length) d.s = 0;
      d.playing = true; d.last = null;
      $("drive-play").textContent = "❚❚"; $("drive-play").setAttribute("aria-label", "Sürüşü duraklat");
      if ($("drive-follow").checked && map.getZoom() < 14) {
        map.setZoom(15, { animate: false });
      }
      if ($("drive-follow").checked) follow(locate(d.s));
      d.frame = requestAnimationFrame(tick);
    }
    function stop() {
      const wasPlaying = d.playing;
      d.playing = false; if (d.frame) cancelAnimationFrame(d.frame); d.frame = null;
      if (wasPlaying) map.fire("moveend");
      $("drive-play").textContent = "▶"; $("drive-play").setAttribute("aria-label", "Sürüşü başlat");
    }
    function load(data) {
      stop();
      if (!data.track || !data.summary.speed) { reset(); return; }
      d.track = data.track; d.length = data.summary.route_length_m; d.highlighted = null;
      const byId = new Map(data.summary.bends.map((bend) => [bend.id, bend]));
      d.bends = data.summary.speed.bends.map((r) => ({ ...r, bend: byId.get(r.bend_id) }))
        .sort((x, y) => x.core_start_s_m - y.core_start_s_m);
      d.s = Math.min(d.s, d.length);
      $("drive-position").max = Math.round(d.length);
      if (!d.marker) d.marker = L.marker([0, 0], { icon: carIcon(), keyboard: false, interactive: false, zIndexOffset: 1000 });
      d.marker.addTo(map);
      $("drive-panel").hidden = false;
      render();
    }
    function reset() {
      stop(); d.track = null; d.bends = []; d.highlighted = null; d.s = 0;
      if (d.marker) d.marker.remove();
      $("drive-panel").hidden = true;
    }
    function jumpTo(s) {
      d.s = Math.max(0, Math.min(d.length, s)); render();
      if ($("drive-follow").checked) follow(locate(d.s));
    }
    $("drive-play").addEventListener("click", () => (d.playing ? stop() : play()));
    map.on("dragstart", () => { d.dragging = true; if (d.playing) $("drive-follow").checked = false; });
    map.on("dragend", () => { d.dragging = false; });
    map.on("zoomend", () => { if (d.track && $("drive-follow").checked) follow(locate(d.s)); });
    $("drive-follow").addEventListener("change", () => { if (d.track && $("drive-follow").checked) follow(locate(d.s)); });
    $("drive-speed").addEventListener("input", render);
    $("drive-threshold").addEventListener("change", render);
    $("drive-position").addEventListener("input", (event) => jumpTo(Number(event.target.value)));
    return { load, reset, jumpTo, state: d };
  })();
  window.copilotDrive = drive;
  updateMarkers(); angleUI();
  (async () => {
    try { state.defaults = await api("/api/defaults"); await run(); }
    catch (error) { showError(error.message); status("Test alanı yüklenemedi.", true); }
  })();
})();
