let dashboardData = null;
let selectedStationId = null;
let stationMap = null;
let markers = new Map();
const charts = new Map();

document.addEventListener("DOMContentLoaded", () => {
  setupTheme();
  setupTabs();
  loadDashboard();
});

async function loadDashboard() {
  try {
    const response = await fetch("data/summary_data.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`No se pudo cargar el resumen (${response.status})`);
    dashboardData = await response.json();
    renderDashboard();
  } catch (error) {
    const box = document.getElementById("load-error");
    box.textContent = `${error.message}. Ejecuta la actualización del dashboard para generar sus datos.`;
    box.classList.remove("hidden");
    document.getElementById("data-status").classList.add("status-error");
    document.querySelector("#data-status span").textContent = "Sin datos";
  }
}

function setupTheme() {
  const root = document.documentElement;
  const savedTheme = localStorage.getItem("pulso-theme");
  if (savedTheme) root.dataset.theme = savedTheme;
  document.getElementById("theme-toggle").addEventListener("click", () => {
    root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
    localStorage.setItem("pulso-theme", root.dataset.theme);
    charts.forEach(chart => chart.update());
    if (stationMap) setTimeout(() => stationMap.invalidateSize(), 150);
  });
}

function setupTabs() {
  document.querySelectorAll(".tab-button").forEach(button => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".tab-button").forEach(item => item.classList.toggle("active", item === button));
      document.querySelectorAll(".tab-panel").forEach(panel => panel.classList.add("hidden"));
      document.getElementById(`tab-${button.dataset.tab}`).classList.remove("hidden");
      if (stationMap) setTimeout(() => stationMap.invalidateSize(), 120);
    });
  });
}

function renderDashboard() {
  renderFreshness();
  renderKpis();
  renderStations();
  renderDailyTrend();
  renderMLOps();
  renderLeaderboard();
  renderErrors();
  renderDrift();
}

function renderFreshness() {
  const metadata = dashboardData.metadata;
  const status = document.getElementById("data-status");
  status.classList.toggle("status-warning", metadata.data_stale);
  status.classList.toggle("status-good", !metadata.data_stale);
  status.querySelector("span").textContent = metadata.data_stale ? "Datos antiguos" : "Datos actualizados";
  document.getElementById("refreshed-at").textContent = `Resumen: ${formatDate(metadata.generated_at)}`;
  document.getElementById("station-count").textContent = `${metadata.total_stations} estaciones`;
  if (metadata.data_stale) {
    const warning = document.getElementById("data-warning");
    warning.textContent = `La API no publica observaciones recientes. Último dato: ${formatDate(metadata.latest_observation_at)} (${formatNumber(metadata.data_age_hours, 1)} h de antigüedad). Las gráficas reflejan el último corte disponible.`;
    warning.classList.remove("hidden");
  }
}

function renderKpis() {
  const cumulative = dashboardData.leaderboard.cumulative;
  const rolling = dashboardData.leaderboard.rolling_24h;
  setText("kpi-cumulative", formatPercent(cumulative?.accuracy));
  setText("kpi-cumulative-note", cumulative ? `WAPE ${formatPercent(cumulative.wape == null ? null : cumulative.wape * 100)} · cobertura ${formatPercent(cumulative.coverage == null ? null : cumulative.coverage * 100)}` : "Sin resultado oficial disponible");
  setText("kpi-rolling", formatPercent(rolling?.accuracy));
  setText("kpi-rolling-note", rolling ? `WAPE ${formatPercent(rolling.wape == null ? null : rolling.wape * 100)} · cobertura ${formatPercent(rolling.coverage == null ? null : rolling.coverage * 100)}` : "Sin resultado oficial disponible");
  setText("kpi-rank", cumulative?.rank ? `#${cumulative.rank}` : "—");
  setText("kpi-rank-note", cumulative ? `${cumulative.resolved_cycles ?? "—"} ciclos evaluados` : "Leaderboard oficial");
  setText("kpi-coverage", formatPercent(rolling?.coverage * 100));
  setText("kpi-coverage-note", rolling ? `Calculada ${formatDate(rolling.calculated_at)}` : "Sin cobertura calculada");
}

function renderStations() {
  const stations = dashboardData.stations || [];
  const select = document.getElementById("station-select");
  if (!stations.length) return;
  selectedStationId = selectedStationId || stations[0].station_id;
  select.innerHTML = stations.map(station => `<option value="${escapeHtml(station.station_id)}">${escapeHtml(station.station_name)} · ${escapeHtml(station.corridor)}</option>`).join("");
  select.value = selectedStationId;
  select.addEventListener("change", () => selectStation(select.value));
  renderMap();
  selectStation(selectedStationId);
}

function renderMap() {
  const stations = dashboardData.stations || [];
  if (!window.L || !stations.length) return;
  stationMap = L.map("map", { scrollWheelZoom: false }).setView([4.648, -74.095], 11);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 18,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(stationMap);
  const points = [];
  const demands = stations.map(station => station.latest_demand).filter(Number.isFinite).sort((a, b) => a - b);
  const median = demands[Math.floor(demands.length / 2)] || 0;
  stations.forEach(station => {
    const point = [Number(station.latitude), Number(station.longitude)];
    if (!point.every(Number.isFinite)) return;
    const value = Number(station.latest_demand || 0);
    const color = value > median * 1.2 ? "#e0574f" : value > median * 0.7 ? "#d59b39" : "#2e9c81";
    const marker = L.circleMarker(point, {
      radius: 8, color: "#f8fafc", weight: 2, fillColor: color, fillOpacity: 0.95,
    }).addTo(stationMap);
    marker.bindPopup(`<strong>${escapeHtml(station.station_name)}</strong><br>${escapeHtml(station.corridor)} · ${escapeHtml(station.station_id)}<br>Última demanda: ${formatNumber(station.latest_demand, 0)}`);
    marker.on("click", () => selectStation(station.station_id));
    markers.set(station.station_id, marker);
    points.push(point);
  });
  if (points.length) stationMap.fitBounds(L.latLngBounds(points), { padding: [24, 24], maxZoom: 13 });
}

function selectStation(stationId) {
  const station = dashboardData.stations.find(item => item.station_id === stationId);
  if (!station) return;
  selectedStationId = stationId;
  document.getElementById("station-select").value = stationId;
  setText("station-code", station.station_id);
  setText("station-name", station.station_name);
  setText("station-corridor", `Troncal ${station.corridor}`);
  setText("station-mean", formatNumber(station.mean_demand, 1));
  setText("station-latest", formatNumber(station.latest_demand, 0));
  setText("station-latest-at", formatDate(station.latest_at));
  drawChart("hourly", "hourly-chart", "line", {
    labels: Array.from({ length: 24 }, (_, hour) => `${String(hour).padStart(2, "0")}:00`),
    datasets: [{ label: "Demanda media", data: station.hourly_curve, borderColor: "#48aa91", backgroundColor: "#48aa9128", fill: true, tension: 0.3, pointRadius: 0 }],
  }, { compact: true });
  const series = dashboardData.time_series?.[stationId] || { actual: [], predicted: [] };
  const byTime = new Map();
  series.actual.forEach(item => byTime.set(item.timestamp, { actual: item.value, predicted: null }));
  series.predicted.forEach(item => {
    const point = byTime.get(item.timestamp) || { actual: item.actual, predicted: null };
    point.predicted = item.value;
    if (item.actual !== null && item.actual !== undefined) point.actual = item.actual;
    byTime.set(item.timestamp, point);
  });
  const timeline = [...byTime.entries()].sort(([a], [b]) => a.localeCompare(b));
  drawChart("series", "station-series-chart", "line", {
    labels: timeline.map(([time]) => formatDate(time, true)),
    datasets: [
      { label: "Real observada", data: timeline.map(([, point]) => point.actual), borderColor: "#56a9ce", backgroundColor: "#56a9ce18", tension: 0.25, pointRadius: 0, spanGaps: false },
      { label: "Predicción enviada", data: timeline.map(([, point]) => point.predicted), borderColor: "#e0a849", borderDash: [5, 4], tension: 0.2, pointRadius: 2, spanGaps: false },
    ],
  });
  setText("series-caption", series.predicted.length ? `${series.predicted.length} predicciones registradas; los valores reales aparecen cuando Supabase los evalúa.` : "Aún no hay predicciones de esta estación en Supabase.");
  const marker = markers.get(stationId);
  if (marker && stationMap) marker.openPopup();
}

function renderDailyTrend() {
  const rows = dashboardData.daily_trend || [];
  drawChart("daily", "daily-chart", "bar", {
    labels: rows.map(row => row.date.slice(5)),
    datasets: [{ label: "Demanda media por observación", data: rows.map(row => row.avg_demand), backgroundColor: "#557aabbb", borderRadius: 3 }],
  });
}

function renderMLOps() {
  const mlops = dashboardData.mlops || {};
  const model = mlops.active_model;
  if (model) {
    setText("model-algorithm", model.algorithm);
    setText("model-id", model.model_id);
    setText("model-trained-at", formatDate(model.trained_at));
    setText("model-cutoff", formatDate(model.data_cutoff));
    setText("model-rows", formatNumber(model.training_rows, 0));
    setText("model-commit", String(model.code_commit || "—").slice(0, 12));
    setText("model-cycle", model.cycle_id || model.run_id);
    const state = model.submission?.status || model.submission?.detail || "Sin recibo";
    setText("submission-state", state);
  }
  const action = mlops.last_pipeline_action;
  if (action) {
    setText("action-name", action.name || "Pipeline Pulso TransMi");
    setText("action-time", formatDate(action.created_at));
    setText("action-event", action.event || "—");
    setText("action-number", action.run_number ?? "—");
    const statusPill = document.getElementById("action-status");
    statusPill.classList.add(action.status === "success" ? "status-good" : "status-warning");
    statusPill.querySelector("span").textContent = action.status || "—";
    setLink("action-link", action.html_url);
  } else {
    setText("action-name", "No se encontró una ejecución reciente.");
  }
}

function renderLeaderboard() {
  const leaderboard = dashboardData.leaderboard;
  const cumulative = leaderboard.cumulative;
  setText("leaderboard-updated", cumulative ? `Actualizado ${formatDate(cumulative.calculated_at)}` : "Sin datos oficiales");
  const body = document.getElementById("leaderboard-body");
  const rows = leaderboard.top || [];
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="4" class="empty-cell">No se pudo obtener el leaderboard oficial.</td></tr>';
    return;
  }
  body.innerHTML = rows.map(row => `<tr class="${row.is_self ? "self-row" : ""}"><td>#${escapeHtml(row.rank ?? "—")}</td><td>${escapeHtml(row.name || "—")}${row.is_self ? " <span class=\"you-tag\">Tu equipo</span>" : ""}</td><td>${formatPercent(row.accuracy)}</td><td>${formatPercent(Number(row.coverage) * 100)}</td></tr>`).join("");
}

function renderErrors() {
  const horizons = dashboardData.errors?.by_horizon || [];
  const hasEvaluation = horizons.some(item => item.accuracy !== null);
  drawChart("horizon", "horizon-chart", "bar", {
    labels: horizons.map(item => `${item.horizon} min`),
    datasets: [
      { label: "Accuracy", data: horizons.map(item => item.accuracy), backgroundColor: "#48aa91bb", yAxisID: "y" },
      { label: "Cobertura", data: horizons.map(item => item.coverage_pct), backgroundColor: "#557aab88", yAxisID: "y1" },
    ],
  }, { dualAxis: true });
  setText("horizon-caption", hasEvaluation ? "Métricas calculadas con predicciones etiquetadas en Supabase; la cobertura indica cuántas ya tienen valor real." : "Aún no hay predicciones evaluadas. Una submission aceptada puede seguir pendiente de etiquetas.");

  const errors = dashboardData.errors?.absolute_error_sample || [];
  const buckets = makeHistogram(errors, 10);
  drawChart("error", "error-chart", "bar", {
    labels: buckets.labels,
    datasets: [{ label: "Predicciones", data: buckets.counts, backgroundColor: "#d28361bb", borderRadius: 3 }],
  });
  setText("error-caption", errors.length ? `${formatNumber(errors.length, 0)} errores absolutos con etiqueta real.` : "Sin errores calculables todavía: no hay etiquetas reales disponibles.");

  const stationRows = dashboardData.errors?.by_station || [];
  const body = document.getElementById("station-errors-body");
  if (!stationRows.length) {
    body.innerHTML = '<tr><td colspan="6" class="empty-cell">Aún no hay evaluaciones etiquetadas.</td></tr>';
  } else {
    body.innerHTML = stationRows.map(row => {
      const station = dashboardData.stations.find(item => item.station_id === row.station_id);
      return `<tr><td>${escapeHtml(station?.station_name || row.station_id)}</td><td>${formatNumber(row.prediction_count, 0)}</td><td>${formatNumber(row.resolved_count, 0)}</td><td>${formatPercent(row.coverage_pct)}</td><td>${formatPercent(row.wape === null ? null : row.wape * 100)}</td><td>${formatPercent(row.accuracy)}</td></tr>`;
    }).join("");
  }
}

function renderDrift() {
  const drift = dashboardData.drift || [];
  const grid = document.getElementById("drift-grid");
  const available = drift.filter(item => item.psi !== null && item.psi !== undefined);
  document.getElementById("drift-empty").classList.toggle("hidden", drift.length > 0);
  if (!drift.length) return;
  const last = [...drift].sort((a, b) => String(b.observed_at).localeCompare(String(a.observed_at)))[0];
  setText("drift-window", `PSI · umbral 0,20 · actualizado ${formatDate(last.observed_at)}`);
  grid.innerHTML = drift.map(item => {
    const psi = item.psi;
    const unavailableReason = item.details?.reason_unavailable;
    const warning = item.status === "warning" || (psi !== null && psi >= item.threshold);
    const status = unavailableReason ? "No disponible" : warning ? "Alerta" : "Estable";
    const width = psi === null ? 0 : Math.min(100, Math.max(3, psi / item.threshold * 45));
    return `<article class="drift-card ${warning ? "drift-alert" : ""}"><div class="drift-card-top"><strong>${escapeHtml(item.feature)}</strong><span class="small-badge ${warning ? "badge-warning" : "badge-good"}">${status}</span></div><p class="drift-value">${psi === null ? "—" : Number(psi).toFixed(3)} <small>/ ${Number(item.threshold).toFixed(2)}</small></p>${psi === null ? `<p class="muted-text">${escapeHtml(unavailableReason || "Métrica no disponible")}</p>` : `<div class="progress-track"><span style="width:${width}%"></span></div>`}</article>`;
  }).join("");
}

function makeHistogram(values, count) {
  if (!values.length) return { labels: ["Sin datos"], counts: [0] };
  const max = Math.max(...values);
  const min = Math.min(...values);
  const step = (max - min) / count || 1;
  const counts = Array(count).fill(0);
  values.forEach(value => counts[Math.min(count - 1, Math.floor((value - min) / step))]++);
  return { labels: counts.map((_, index) => `${Math.round(min + step * index)}–${Math.round(min + step * (index + 1))}`), counts };
}

function drawChart(key, canvasId, type, data, options = {}) {
  const canvas = document.getElementById(canvasId);
  if (!canvas || !window.Chart) return;
  charts.get(key)?.destroy();
  const dark = document.documentElement.dataset.theme === "dark";
  const tick = dark ? "#aab6c3" : "#667381";
  const grid = dark ? "#ffffff12" : "#10192312";
  const scales = {
    x: { grid: { display: false }, ticks: { color: tick, maxTicksLimit: 8 } },
    y: { beginAtZero: true, grid: { color: grid }, ticks: { color: tick } },
  };
  if (options.dualAxis) {
    scales.y1 = { position: "right", min: 0, max: 100, grid: { drawOnChartArea: false }, ticks: { color: tick, callback: value => `${value}%` } };
    data.datasets[0].backgroundColor = "#48aa91bb";
  }
  charts.set(key, new Chart(canvas, {
    type,
    data,
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { display: type === "line" || data.datasets.length > 1, labels: { color: tick, usePointStyle: true, boxWidth: 8 } } },
      scales,
    },
  }));
}

function setText(id, value) { document.getElementById(id).textContent = value ?? "—"; }
function formatPercent(value) { return value === null || value === undefined || !Number.isFinite(Number(value)) ? "—" : `${Number(value).toFixed(2)}%`; }
function formatNumber(value, digits = 0) { return value === null || value === undefined || !Number.isFinite(Number(value)) ? "—" : Number(value).toLocaleString("es-CO", { maximumFractionDigits: digits, minimumFractionDigits: digits }); }
function formatDate(value, timeOnly = false) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return String(value);
  return new Intl.DateTimeFormat("es-CO", timeOnly ? { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" } : { dateStyle: "medium", timeStyle: "short", timeZone: "America/Bogota" }).format(date);
}
function setLink(id, href) {
  const link = document.getElementById(id);
  if (!href) return;
  link.href = href;
  link.classList.remove("hidden");
}
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);
}
