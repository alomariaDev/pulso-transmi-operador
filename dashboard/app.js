// Global Data State
let appData = null;
let currentStationId = "07107";
let mapInstance = null;
let markers = {};

// Chart Instances
let miniHourlyChart = null;
let timeSeriesChart = null;
let dailyTrendChart = null;

// Initialize when DOM is ready
document.addEventListener("DOMContentLoaded", async () => {
  // Initialize Lucide Icons
  lucide.createIcons();

  // Setup Theme Toggle
  setupThemeToggle();

  // Load Data
  await loadDashboardData();

  // Initialize Map
  initMap();

  // Initialize Visualizations
  renderStationsSelect();
  updateStationSpotlight(currentStationId);
  renderModelsTable();
  renderDriftCards();
  renderDailyTrendChart();
});

// Load Dashboard Data
async function loadDashboardData() {
  try {
    const res = await fetch("data/summary_data.json");
    if (!res.ok) throw new Error("HTTP error " + res.status);
    appData = await res.json();
  } catch (err) {
    console.warn("Could not load summary_data.json via fetch, using fallback data:", err);
    // Provide robust fallback in case of local file:// execution without http server
    appData = getFallbackData();
  }

  // Update KPIs
  if (appData && appData.metadata) {
    document.getElementById("kpi-accuracy").textContent = `${appData.metadata.accuracy}%`;
    document.getElementById("kpi-wape").textContent = appData.metadata.wape;
    document.getElementById("kpi-stations").textContent = appData.metadata.total_stations;
    document.getElementById("kpi-observations").textContent = Number(appData.metadata.total_observations).toLocaleString();
  }
}

// Setup Theme Toggle
function setupThemeToggle() {
  const toggleBtn = document.getElementById("theme-toggle");
  toggleBtn.addEventListener("click", () => {
    document.documentElement.classList.toggle("dark");
    lucide.createIcons();
    // Refresh charts if needed
    if (timeSeriesChart) timeSeriesChart.update();
    if (dailyTrendChart) dailyTrendChart.update();
    if (miniHourlyChart) miniHourlyChart.update();
  });
}

// Switch Navigation Tabs
function switchTab(tabId) {
  const overview = document.getElementById("tab-content-overview");
  const models = document.getElementById("tab-content-models");
  const drift = document.getElementById("tab-content-drift");

  // Reset tab buttons
  document.querySelectorAll(".tab-btn, .mobile-nav-btn").forEach(btn => {
    if (btn.dataset.tab === tabId) {
      btn.classList.add("active", "text-brand-500", "bg-slate-800/80");
      btn.classList.remove("text-slate-400");
    } else {
      btn.classList.remove("active", "text-brand-500", "bg-slate-800/80");
      btn.classList.add("text-slate-400");
    }
  });

  if (tabId === "overview" || tabId === "map-stations") {
    overview.classList.remove("hidden");
    models.classList.add("hidden");
    drift.classList.add("hidden");
    if (tabId === "map-stations") {
      document.getElementById("map").scrollIntoView({ behavior: "smooth" });
    }
    if (mapInstance) {
      setTimeout(() => mapInstance.invalidateSize(), 200);
    }
  } else if (tabId === "models") {
    overview.classList.add("hidden");
    models.classList.remove("hidden");
    drift.classList.add("hidden");
  } else if (tabId === "drift") {
    overview.classList.add("hidden");
    models.classList.add("hidden");
    drift.classList.remove("hidden");
  }
}

// Initialize Leaflet Map
function initMap() {
  const mapElement = document.getElementById("map");
  if (!mapElement || !appData || !appData.stations) return;

  // Center on Bogota TransMilenio core
  mapInstance = L.map("map", {
    center: [4.635, -74.105],
    zoom: 11,
    zoomControl: true,
    scrollWheelZoom: false
  });

  // Dark Tiles (CartoDB DarkMatter)
  L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
    attribution: '&copy; <a href="https://carto.com/">CARTO</a>, TransMilenio',
    maxZoom: 19
  }).addTo(mapInstance);

  // Add station markers
  appData.stations.forEach(st => {
    const isHighDemand = st.mean_demand > 400;
    const color = isHighDemand ? "#f43f5e" : "#10b981";

    const customIcon = L.divIcon({
      className: "custom-station-icon",
      html: `
        <div class="relative flex items-center justify-center">
          <span class="animate-ping absolute inline-flex h-full w-full rounded-full opacity-75" style="background-color: ${color}"></span>
          <span class="relative inline-flex rounded-full h-4 w-4 border-2 border-white shadow-md" style="background-color: ${color}"></span>
        </div>
      `,
      iconSize: [16, 16],
      iconAnchor: [8, 8]
    });

    const marker = L.marker([st.latitude, st.longitude], { icon: customIcon }).addTo(mapInstance);

    marker.bindPopup(`
      <div class="p-1 text-slate-900">
        <h4 class="font-bold text-sm text-slate-900">${st.station_name}</h4>
        <p class="text-xs text-slate-600">Troncal: ${st.corridor}</p>
        <div class="mt-1 pt-1 border-t border-slate-200 text-xs flex justify-between">
          <span>Demanda Media:</span>
          <b class="text-rose-600">${st.mean_demand}</b>
        </div>
      </div>
    `);

    marker.on("click", () => {
      document.getElementById("station-select").value = st.station_id;
      updateStationSpotlight(st.station_id);
    });

    markers[st.station_id] = marker;
  });
}

// Render Station Selector Options
function renderStationsSelect() {
  const select = document.getElementById("station-select");
  if (!select || !appData || !appData.stations) return;

  select.innerHTML = appData.stations.map(st => `
    <option value="${st.station_id}" ${st.station_id === currentStationId ? "selected" : ""}>
      ${st.station_name} (${st.corridor})
    </option>
  `).join("");

  select.addEventListener("change", (e) => {
    updateStationSpotlight(e.target.value);
  });
}

// Update Station Details & Charts
function updateStationSpotlight(stationId) {
  currentStationId = stationId;
  const st = appData.stations.find(s => s.station_id === stationId) || appData.stations[0];

  document.getElementById("st-badge").textContent = `ID: ${st.station_id}`;
  document.getElementById("st-name").textContent = st.station_name;
  document.getElementById("st-corridor").innerHTML = `
    <i data-lucide="git-commit" class="w-3.5 h-3.5 text-slate-500"></i>
    <span>Troncal: ${st.corridor}</span>
  `;
  document.getElementById("st-mean").textContent = st.mean_demand.toLocaleString();
  document.getElementById("st-max").textContent = st.max_demand.toLocaleString();
  document.getElementById("ts-station-name").textContent = st.station_name;

  lucide.createIcons();

  // Center map on station
  if (mapInstance && st.latitude && st.longitude) {
    mapInstance.panTo([st.latitude, st.longitude], { animate: true, duration: 0.8 });
    if (markers[stationId]) {
      markers[stationId].openPopup();
    }
  }

  // Render Mini Hourly Curve
  renderMiniHourlyChart(st.hourly_curve || []);

  // Render Time Series
  renderTimeSeriesChart(stationId);
}

// Render Mini Hourly Profile Chart
function renderMiniHourlyChart(hourlyCurve) {
  const ctx = document.getElementById("miniHourlyChart");
  if (!ctx) return;

  const labels = Array.from({ length: 24 }, (_, i) => `${i}h`);

  if (miniHourlyChart) {
    miniHourlyChart.destroy();
  }

  miniHourlyChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: labels,
      datasets: [{
        data: hourlyCurve,
        borderColor: "#f43f5e",
        backgroundColor: "rgba(244, 63, 94, 0.15)",
        borderWidth: 2,
        fill: true,
        tension: 0.4,
        pointRadius: 0
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => `Demanda promedio: ${ctx.raw} pas/15m`
          }
        }
      },
      scales: {
        x: {
          grid: { display: false },
          ticks: { color: "#94a3b8", font: { size: 9 }, maxTicksLimit: 6 }
        },
        y: {
          grid: { color: "rgba(148, 163, 184, 0.1)" },
          ticks: { color: "#94a3b8", font: { size: 9 } }
        }
      }
    }
  });
}

// Render Time Series (Validation Actual vs Predicted)
function renderTimeSeriesChart(stationId) {
  const ctx = document.getElementById("timeSeriesChart");
  if (!ctx || !appData || !appData.timeline_series) return;

  const seriesData = appData.timeline_series[stationId] || [];
  const labels = seriesData.map(d => `${d.date.slice(5)} ${d.time}`);
  const actuals = seriesData.map(d => d.actual);
  const predicteds = seriesData.map(d => d.predicted);

  if (timeSeriesChart) {
    timeSeriesChart.destroy();
  }

  timeSeriesChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: labels,
      datasets: [
        {
          label: "Demanda Real",
          data: actuals,
          borderColor: "#6366f1",
          backgroundColor: "rgba(99, 102, 241, 0.1)",
          borderWidth: 2,
          pointRadius: 2,
          pointHoverRadius: 5,
          tension: 0.3
        },
        {
          label: "ExtraTrees Predicción",
          data: predicteds,
          borderColor: "#34d399",
          borderDash: [4, 4],
          borderWidth: 2,
          pointRadius: 1,
          pointHoverRadius: 5,
          tension: 0.3
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          padding: 10,
          backgroundColor: "rgba(15, 23, 42, 0.9)",
          titleColor: "#f8fafc",
          bodyColor: "#cbd5e1"
        }
      },
      scales: {
        x: {
          grid: { display: false },
          ticks: { color: "#94a3b8", font: { size: 10 }, maxTicksLimit: 8 }
        },
        y: {
          grid: { color: "rgba(148, 163, 184, 0.1)" },
          ticks: { color: "#94a3b8", font: { size: 10 } }
        }
      }
    }
  });
}

// Render Daily Historical Aggregated Trend Chart
function renderDailyTrendChart() {
  const ctx = document.getElementById("dailyTrendChart");
  if (!ctx || !appData || !appData.daily_trend) return;

  const labels = appData.daily_trend.map(d => d.date.slice(5));
  const values = appData.daily_trend.map(d => d.avg_demand);

  if (dailyTrendChart) {
    dailyTrendChart.destroy();
  }

  dailyTrendChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels: labels,
      datasets: [{
        label: "Demanda Media Diaria",
        data: values,
        backgroundColor: "rgba(245, 158, 11, 0.65)",
        hoverBackgroundColor: "rgba(245, 158, 11, 0.9)",
        borderRadius: 4
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => `Demanda media del sistema: ${ctx.raw} pas/15m`
          }
        }
      },
      scales: {
        x: {
          grid: { display: false },
          ticks: { color: "#94a3b8", font: { size: 9 }, maxTicksLimit: 12 }
        },
        y: {
          grid: { color: "rgba(148, 163, 184, 0.1)" },
          ticks: { color: "#94a3b8", font: { size: 9 } }
        }
      }
    }
  });
}

// Render Models Leaderboard Table
function renderModelsTable() {
  const tbody = document.getElementById("models-table-body");
  if (!tbody || !appData || !appData.models_leaderboard) return;

  tbody.innerHTML = appData.models_leaderboard.map(m => `
    <tr class="hover:bg-slate-800/40 transition">
      <td class="py-3 px-4 font-bold text-slate-300">#${m.rank}</td>
      <td class="py-3 px-4 font-semibold text-white flex items-center space-x-2">
        <span class="w-2.5 h-2.5 rounded-full bg-${m.color}-400 inline-block"></span>
        <span>${m.model_name}</span>
      </td>
      <td class="py-3 px-4 font-mono text-slate-200">${m.wape.toFixed(4)}</td>
      <td class="py-3 px-4 font-bold text-emerald-400">${m.accuracy.toFixed(2)}%</td>
      <td class="py-3 px-4 text-slate-400">${m.training_time}</td>
      <td class="py-3 px-4 text-slate-400">${m.features_count}</td>
      <td class="py-3 px-4">
        <span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-${m.color}-500/10 text-${m.color}-400 border border-${m.color}-500/20">
          ${m.status}
        </span>
      </td>
    </tr>
  `).join("");
}

// Render Drift Indicator Cards
function renderDriftCards() {
  const container = document.getElementById("drift-cards-grid");
  if (!container || !appData || !appData.drift_metrics) return;

  container.innerHTML = appData.drift_metrics.map(d => `
    <div class="bg-slate-800/50 border border-slate-700/60 rounded-xl p-4 flex flex-col justify-between">
      <div>
        <div class="flex items-center justify-between mb-2">
          <span class="text-xs font-mono text-slate-400">${d.category}</span>
          <span class="inline-flex items-center px-2 py-0.5 rounded text-[11px] font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
            <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 mr-1.5"></span>
            ${d.status}
          </span>
        </div>
        <h4 class="font-bold text-sm text-white font-mono mb-2">${d.feature}</h4>
      </div>
      <div>
        <div class="flex justify-between text-xs text-slate-400 mb-1">
          <span>PSI Calculado: <b class="text-slate-200 font-mono">${d.psi.toFixed(3)}</b></span>
          <span>Umbral: 0.20</span>
        </div>
        <!-- Progress Bar -->
        <div class="w-full bg-slate-700/60 rounded-full h-2 overflow-hidden">
          <div class="bg-emerald-400 h-2 rounded-full transition-all duration-500" style="width: ${(d.psi / d.threshold) * 100}%"></div>
        </div>
      </div>
    </div>
  `).join("");
}

// Fallback Data in case summary_data.json cannot be fetched
function getFallbackData() {
  return {
    metadata: {
      accuracy: 87.21,
      wape: 0.1279,
      total_stations: 12,
      total_observations: 51840,
      active_model: "ExtraTreesRegressor"
    },
    stations: [
      { station_id: "07107", station_name: "Ricaurte - NQS", corridor: "NQS Central", latitude: 4.615, longitude: -74.098, mean_demand: 482.6, max_demand: 1240, hourly_curve: [20, 15, 10, 8, 45, 180, 520, 890, 710, 480, 410, 390, 440, 460, 490, 560, 780, 940, 850, 520, 310, 190, 95, 40] },
      { station_id: "02000", station_name: "Portal Norte", corridor: "Autopista Norte", latitude: 4.755, longitude: -74.045, mean_demand: 430.1, max_demand: 1120, hourly_curve: [18, 12, 8, 15, 60, 220, 680, 950, 620, 420, 380, 360, 400, 420, 450, 510, 690, 880, 760, 480, 280, 160, 80, 35] }
    ],
    daily_trend: [],
    models_leaderboard: [
      { rank: 1, model_name: "ExtraTreesRegressor", wape: 0.1279, accuracy: 87.21, training_time: "12.4s", features_count: 17, status: "Activo en Producción", color: "emerald" },
      { rank: 2, model_name: "HistGradientBoostingRegressor", wape: 0.1298, accuracy: 87.02, training_time: "8.1s", features_count: 17, status: "Evaluado", color: "blue" }
    ],
    drift_metrics: [
      { feature: "demand_lag_15m", category: "Autocorrelación", psi: 0.042, status: "Estable", threshold: 0.20 },
      { feature: "temperature_c", category: "Clima", psi: 0.071, status: "Estable", threshold: 0.20 }
    ],
    timeline_series: {}
  };
}
