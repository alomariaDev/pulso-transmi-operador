// Global Data State
let appData = null;
let currentStationId = "07111"; // Ricaurte - NQS (Highest demand)
let mapInstance = null;
let markers = {};
let activeCorridor = "ALL";

// Chart Instances
let miniHourlyChart = null;
let timeSeriesChart = null;
let dailyTrendChart = null;

// Initialize when DOM is ready
document.addEventListener("DOMContentLoaded", async () => {
  // Initialize Lucide Icons
  if (window.lucide) lucide.createIcons();

  // Setup Theme Toggle
  setupThemeToggle();

  // Load Data (From synchronous data.js or fetch fallback)
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
  if (window.PULSO_DASHBOARD_DATA) {
    appData = window.PULSO_DASHBOARD_DATA;
  } else {
    try {
      const res = await fetch("data/summary_data.json");
      if (res.ok) {
        appData = await res.json();
      }
    } catch (err) {
      console.warn("Using inline fallback data", err);
    }
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
  if (!toggleBtn) return;
  toggleBtn.addEventListener("click", () => {
    document.documentElement.classList.toggle("dark");
    if (window.lucide) lucide.createIcons();
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
      setTimeout(() => mapInstance.invalidateSize(), 250);
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

// Corridor Filter Handler
function filterCorridor(corridor) {
  activeCorridor = corridor;

  // Update button styles
  document.querySelectorAll(".corridor-chip").forEach(chip => {
    if (chip.dataset.corridor === corridor) {
      chip.classList.add("bg-brand-600", "text-white");
      chip.classList.remove("bg-slate-800", "text-slate-300");
    } else {
      chip.classList.remove("bg-brand-600", "text-white");
      chip.classList.add("bg-slate-800", "text-slate-300");
    }
  });

  // Filter Select Options
  renderStationsSelect();

  // Show/Hide Markers on Map
  if (appData && appData.stations && mapInstance) {
    const visibleCoords = [];
    appData.stations.forEach(st => {
      const marker = markers[st.station_id];
      if (!marker) return;
      if (corridor === "ALL" || st.corridor === corridor) {
        if (!mapInstance.hasLayer(marker)) marker.addTo(mapInstance);
        visibleCoords.push([st.latitude, st.longitude]);
      } else {
        if (mapInstance.hasLayer(marker)) mapInstance.removeLayer(marker);
      }
    });

    if (visibleCoords.length > 0) {
      const bounds = L.latLngBounds(visibleCoords);
      mapInstance.fitBounds(bounds, { padding: [30, 30], maxZoom: 13 });
    }
  }

  // Select first available station in this corridor
  const availableStations = appData.stations.filter(s => corridor === "ALL" || s.corridor === corridor);
  if (availableStations.length > 0 && !availableStations.find(s => s.station_id === currentStationId)) {
    updateStationSpotlight(availableStations[0].station_id);
  }
}

// Initialize Leaflet Map (100% Free OpenStreetMap - ZERO API Keys Required)
function initMap() {
  const mapElement = document.getElementById("map");
  if (!mapElement || !appData || !appData.stations) return;

  // Center on Bogota TransMilenio coverage
  mapInstance = L.map("map", {
    center: [4.648, -74.095],
    zoom: 11,
    zoomControl: true,
    scrollWheelZoom: false
  });

  // 100% OpenStreetMap Standard Tiles (No token or API key required)
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors | TransMilenio',
    maxZoom: 19
  }).addTo(mapInstance);

  const allLatLngs = [];

  // Add all 12 station markers
  appData.stations.forEach(st => {
    const isHighDemand = st.mean_demand > 400;
    const color = isHighDemand ? "#f43f5e" : "#10b981";

    const customIcon = L.divIcon({
      className: "custom-station-icon",
      html: `
        <div class="relative flex items-center justify-center cursor-pointer" title="${st.station_name} (${st.corridor})">
          <span class="animate-ping absolute inline-flex h-5 w-5 rounded-full opacity-60" style="background-color: ${color}"></span>
          <span class="relative inline-flex rounded-full h-4 w-4 border-2 border-white shadow-lg" style="background-color: ${color}"></span>
        </div>
      `,
      iconSize: [16, 16],
      iconAnchor: [8, 8]
    });

    const marker = L.marker([st.latitude, st.longitude], { icon: customIcon }).addTo(mapInstance);

    marker.bindPopup(`
      <div class="p-1 min-w-[160px]">
        <span class="text-[10px] font-bold uppercase tracking-wider text-rose-500 block">Troncal ${st.corridor}</span>
        <h4 class="font-bold text-sm text-slate-900">${st.station_name}</h4>
        <div class="mt-2 pt-1.5 border-t border-slate-200 text-xs space-y-1">
          <div class="flex justify-between">
            <span class="text-slate-600">Demanda Media:</span>
            <b class="text-rose-600 font-mono">${st.mean_demand}</b>
          </div>
          <div class="flex justify-between">
            <span class="text-slate-600">Demanda Pico:</span>
            <b class="text-slate-900 font-mono">${st.max_demand}</b>
          </div>
        </div>
      </div>
    `);

    marker.on("click", () => {
      document.getElementById("station-select").value = st.station_id;
      updateStationSpotlight(st.station_id);
    });

    markers[st.station_id] = marker;
    allLatLngs.push([st.latitude, st.longitude]);
  });

  // Auto-fit all 12 stations into map view
  if (allLatLngs.length > 0) {
    mapInstance.fitBounds(L.latLngBounds(allLatLngs), { padding: [25, 25] });
  }
}

// Render Station Selector Options
function renderStationsSelect() {
  const select = document.getElementById("station-select");
  if (!select || !appData || !appData.stations) return;

  const filteredStations = appData.stations.filter(s => activeCorridor === "ALL" || s.corridor === activeCorridor);

  select.innerHTML = filteredStations.map(st => `
    <option value="${st.station_id}" ${st.station_id === currentStationId ? "selected" : ""}>
      ${st.station_name} — Troncal ${st.corridor}
    </option>
  `).join("");

  select.onchange = (e) => {
    updateStationSpotlight(e.target.value);
  };
}

// Update Station Details & Charts
function updateStationSpotlight(stationId) {
  currentStationId = stationId;
  const st = appData.stations.find(s => s.station_id === stationId) || appData.stations[0];

  document.getElementById("st-badge").textContent = `ID: ${st.station_id}`;
  document.getElementById("st-name").textContent = st.station_name;
  document.getElementById("st-corridor").innerHTML = `
    <i data-lucide="git-commit" class="w-3.5 h-3.5 text-slate-500"></i>
    <span>Troncal: <b>${st.corridor}</b></span>
  `;
  document.getElementById("st-mean").textContent = st.mean_demand.toLocaleString();
  document.getElementById("st-max").textContent = st.max_demand.toLocaleString();
  document.getElementById("ts-station-name").textContent = `${st.station_name} (Troncal ${st.corridor})`;

  if (window.lucide) lucide.createIcons();

  // Center map on selected station
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
        <div class="w-full bg-slate-700/60 rounded-full h-2 overflow-hidden">
          <div class="bg-emerald-400 h-2 rounded-full transition-all duration-500" style="width: ${(d.psi / d.threshold) * 100}%"></div>
        </div>
      </div>
    </div>
  `).join("");
}
