'use strict';

const STORAGE_KEY = 'bpmonitor_v1';
const DEFAULT_API_BASE = 'http://127.0.0.1:8000';

const TYPES = {
  cuffless: {
    id: 'cuffless',
    label: 'Manschettenloses Gerät',
    short: 'Manschettenlos',
    color: '#6366f1',
  },
  phone: {
    id: 'phone',
    label: 'Telefonmessung',
    short: 'Telefon',
    color: '#10b981',
  },
  cuff: {
    id: 'cuff',
    label: 'Manschettenmessung',
    short: 'Manschette',
    color: '#f59e0b',
  },
};

// Backend measurement_type -> Dashboard column
const IMPORT_TYPE_TO_COLUMN = {
  cuff_calibration: 'cuffless',
  armband: 'cuffless',
  phone_measurement: 'phone',
  cuff_measurement: 'cuff',
};

// Dashboard column -> backend measurement_type (used when creating/updating)
const COLUMN_TO_IMPORT_TYPE = {
  cuffless: 'armband',
  phone: 'phone_measurement',
  cuff: 'cuff_measurement',
};

const IMPORT_TYPE_LABELS = {
  cuff_calibration: 'Kalibrierung',
  armband: 'Armband',
  phone_measurement: 'Telefon',
  cuff_measurement: 'Manschette',
  unknown: 'Unknown',
};

const STATUS_LABELS = {
  original: 'Original',
  manual: 'Manuell',
  edited: 'Bearbeitet',
  restored_original: 'Wiederhergestellt',
};

const _charts = {};
let _currentFilter = 'all';
let _lastServerPayload = null;
let _unifiedPayload = null;
let _reportsList = { reports: [], current_report_id: null };
let _serverMeasurementsCache = { sourceRef: null, value: [] };
let _filteredMeasurementsCache = { key: null, value: [] };
let _mlState = {
  status: 'idle',
  started_at: null,
  finished_at: null,
  last_error: null,
  last_result: null,
  sources: [],
  model_available: false,
  pollTimer: null,
  initialized: false,
};
let _mlModelInfo = null;

const ML_STATUS_LABELS = {
  idle: 'Bereit',
  running: 'Training läuft...',
  finished: 'Training abgeschlossen',
  failed: 'Training fehlgeschlagen',
};

const CLASSIFIER_METHOD_LABELS = {
  keras_constrained: 'Keras-Modell + Monatszähler-Constraint',
  score_threshold: 'Keras-Modell (Score-Schwelle)',
  pdf_constrained: 'Heuristik + Monatszähler-Constraint',
  monthly_constraint: 'Heuristik + Monatszähler-Constraint',
  heuristic: 'Regelbasierte Heuristik',
};

function classifierMethodLabel(method) {
  if (!method) return 'unbekannt';
  return CLASSIFIER_METHOD_LABELS[method] || method;
}

const DAY_MS = 24 * 60 * 60 * 1000;
const MONTH_NAMES_DE = [
  'Januar', 'Februar', 'März', 'April', 'Mai', 'Juni',
  'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember',
];

function reportLabel(report) {
  if (!report) return 'Unbekannt';
  const month = Number.isFinite(report.report_month) ? MONTH_NAMES_DE[report.report_month - 1] : null;
  const year = report.report_year;
  if (month && year) return `${month} ${year}`;
  if (month) return month;
  if (year) return String(year);
  return report.file_name || report.id || 'Bericht';
}

// --- local (manual-only) storage -------------------------------------

function getLocalData() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { cuffless: [], phone: [], cuff: [] };
    return JSON.parse(raw);
  } catch {
    return { cuffless: [], phone: [], cuff: [] };
  }
}

function saveLocalData(data) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
}

function addLocalEntry(type, sys, dia, pulse, datetime) {
  const data = getLocalData();
  if (!data[type]) data[type] = [];
  const entry = {
    id: `${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
    datetime: datetime || new Date().toISOString(),
    sys: parseInt(sys, 10),
    dia: parseInt(dia, 10),
    pulse: parseInt(pulse, 10),
    source: 'manual_local',
    entry_status: 'manual',
  };
  data[type].push(entry);
  data[type].sort((a, b) => new Date(a.datetime) - new Date(b.datetime));
  saveLocalData(data);
  return entry;
}

function deleteLocalEntry(type, id) {
  const data = getLocalData();
  if (!data[type]) return;
  data[type] = data[type].filter((entry) => entry.id !== id);
  saveLocalData(data);
}

// --- API helpers -----------------------------------------------------

function getApiBase() {
  return DEFAULT_API_BASE;
}

async function apiRequest(path, options = {}) {
  const primary = getApiBase();
  const bases = [primary];
  if (
    window.location.protocol.startsWith('http') &&
    window.location.origin &&
    window.location.origin !== primary &&
    !bases.includes(window.location.origin)
  ) {
    bases.push(window.location.origin);
  }

  let lastError = null;

  for (const base of bases) {
    let response;
    try {
      response = await fetch(`${base}${path}`, options);
    } catch (networkError) {
      lastError = new Error(
        `Backend unter ${base} nicht erreichbar (${networkError.message}). Läuft uvicorn auf diesem Port?`
      );
      continue;
    }

    const text = await response.text().catch(() => '');
    let body = null;
    if (text) {
      try {
        body = JSON.parse(text);
      } catch {
        body = text;
      }
    }

    if (response.ok) {
      return body;
    }

    const detail =
      body && typeof body === 'object' && body.detail
        ? body.detail
        : typeof body === 'string' && body
        ? body
        : `HTTP ${response.status}`;
    lastError = new Error(detail);
    if (base === primary) break;
  }

  throw lastError || new Error('API-Aufruf fehlgeschlagen.');
}

// --- formatters ------------------------------------------------------

function fmtDatetime(iso) {
  const d = new Date(iso);
  const date = d.toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit', year: 'numeric' });
  const time = d.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
  return { date, time, full: `${date} ${time}` };
}

// Gibt eine naive lokale ISO-Zeit ("YYYY-MM-DDTHH:MM:SS") zurück.
// Das Backend speichert PDF-Messungen ohne Zeitzone; per ``toISOString()``
// erzeugte UTC-Strings (Suffix Z) würden beim späteren Sortieren gegen
// naive PDF-Zeitstempel einen TypeError auslösen und die Sleep-Banden im
// Unified View stillschweigend ausblenden.
function localIsoFromInput(value) {
  if (!value) return '';
  if (value.length === 16) return `${value}:00`;
  if (value.length === 19) return value;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function bpCategory(sys, dia) {
  if (sys < 120 && dia < 80) return { label: 'Optimal', cls: 'optimal' };
  if (sys < 130 && dia < 85) return { label: 'Normal', cls: 'normal' };
  if (sys < 140 && dia < 90) return { label: 'Erhöht', cls: 'elevated' };
  return { label: 'Hoch', cls: 'high' };
}

function showToast(msg, type = 'success') {
  let el = document.getElementById('toast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'toast';
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.style.background = type === 'error' ? '#ef4444' : '#10b981';
  el.classList.add('show');
  clearTimeout(el._timeout);
  el._timeout = setTimeout(() => el.classList.remove('show'), 2800);
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function statusBadge(status) {
  if (!status) return '';
  const label = STATUS_LABELS[status] || status;
  return `<span class="status-badge status-${status}">${label}</span>`;
}

// --- chart rendering -------------------------------------------------

function createBPChart(canvasId, entries) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;

  if (_charts[canvasId]) {
    _charts[canvasId].destroy();
    delete _charts[canvasId];
  }

  const wrap = canvas.parentElement;
  const empty = wrap.querySelector('.chart-empty');

  if (entries.length === 0) {
    canvas.style.display = 'none';
    if (empty) empty.style.display = 'flex';
    return;
  }

  canvas.style.display = 'block';
  if (empty) empty.style.display = 'none';

  const mkDataset = (label, key, color) => ({
    label,
    data: entries.map((entry) => ({ x: new Date(entry.datetime), y: entry[key] })),
    borderColor: color,
    backgroundColor: `${color}18`,
    pointBackgroundColor: color,
    pointBorderColor: '#0f172a',
    pointBorderWidth: 1.5,
    pointRadius: entries.length < 30 ? 5 : 3,
    pointHoverRadius: 7,
    tension: 0.35,
    fill: false,
    borderWidth: 2,
  });

  const timestamps = entries.map((e) => new Date(e.datetime).getTime());
  const fullMin = Math.min(...timestamps);
  const fullMax = Math.max(...timestamps);
  const zoomOptions = fullMax > fullMin
    ? buildZoomOptions({ fullMin, fullMax, chartId: canvasId })
    : undefined;

  const chart = new Chart(canvas.getContext('2d'), {
    type: 'line',
    data: {
      datasets: [
        mkDataset('SYS', 'sys', '#ef4444'),
        mkDataset('DIA', 'dia', '#f59e0b'),
        mkDataset('Puls', 'pulse', '#10b981'),
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: {
          display: true,
          position: 'top',
          align: 'end',
          labels: {
            color: '#94a3b8',
            usePointStyle: true,
            pointStyleWidth: 7,
            boxHeight: 7,
            padding: 10,
            font: { size: 11, family: 'Inter, system-ui' },
          },
        },
        ...(zoomOptions ? { zoom: zoomOptions } : {}),
      },
      scales: {
        x: {
          type: 'time',
          time: {
            displayFormats: {
              minute: 'HH:mm',
              hour: 'dd.MM HH:mm',
              day: 'dd.MM',
              month: 'MM.yy',
              year: 'yyyy',
            },
            tooltipFormat: 'dd.MM.yyyy HH:mm',
          },
          grid: { color: 'rgba(255,255,255,0.04)' },
          ticks: { color: '#64748b', font: { size: 10 }, maxRotation: 0, maxTicksLimit: 7 },
          border: { color: 'rgba(255,255,255,0.06)' },
        },
        y: {
          min: 40,
          suggestedMax: 180,
          grid: { color: 'rgba(255,255,255,0.04)' },
          ticks: { color: '#64748b', font: { size: 10 }, stepSize: 20 },
          border: { color: 'rgba(255,255,255,0.06)' },
        },
      },
    },
  });

  chart.$fullRange = { min: fullMin, max: fullMax };
  _charts[canvasId] = chart;
}

function renderList(type, entries) {
  const list = document.getElementById(`list-${type}`);
  const count = document.getElementById(`count-${type}`);
  if (!list) return;

  if (count) count.textContent = `${entries.length} ${entries.length === 1 ? 'Eintrag' : 'Einträge'}`;

  if (entries.length === 0) {
    list.innerHTML = `<div class="empty-state">Noch keine Messungen.<br><a href="configurator.html" style="color:${TYPES[type].color}">Jetzt eintragen -></a></div>`;
    return;
  }

  const sorted = [...entries].reverse();
  list.innerHTML = sorted.map((entry) => {
    const dt = fmtDatetime(entry.datetime);
    const badge = entry.entry_status && entry.entry_status !== 'original' ? statusBadge(entry.entry_status) : '';
    return `<div class="data-entry">
      <div class="entry-values">
        <div class="entry-bp">
          <span class="sys">${entry.sys}</span><span class="sep">/</span><span class="dia">${entry.dia}</span>
        </div>
        <div class="entry-pulse">HR ${entry.pulse}</div>
      </div>
      <div class="entry-meta">
        <div class="t-date">${dt.date}</div>
        <div class="t-time">${dt.time}</div>
        ${entry.typeLabel ? `<div class="t-type">${entry.typeLabel}</div>` : ''}
        ${badge}
      </div>
    </div>`;
  }).join('');
}

function updateStats(type, entries) {
  const set = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
  };

  if (entries.length === 0) {
    set(`last-sys-${type}`, '--');
    set(`last-dia-${type}`, '--');
    set(`last-pulse-${type}`, '--');
    return;
  }

  const last = entries[entries.length - 1];
  set(`last-sys-${type}`, last.sys);
  set(`last-dia-${type}`, last.dia);
  set(`last-pulse-${type}`, last.pulse);
}

// --- view-model builders --------------------------------------------

function buildManualViewModel() {
  const data = getLocalData();
  const columns = {};
  Object.keys(TYPES).forEach((type) => {
    columns[type] = (data[type] || []).map((entry) => ({
      ...entry,
      typeLabel: 'Manuell (lokal)',
      entry_status: 'manual',
    }));
  });

  return {
    columns,
    report: null,
    patient: null,
    summary: {
      measurement_count_total: Object.values(columns).reduce((sum, entries) => sum + entries.length, 0),
      measurement_count_day_rest: 0,
      measurement_count_night: 0,
      measurement_count_by_type: {
        cuff_calibration: 0,
        armband: columns.cuffless.length,
        cuff_measurement: columns.cuff.length,
        phone_measurement: columns.phone.length,
        unknown: 0,
      },
      measurement_count_by_bp_category: computeLocalBpDistribution(columns),
    },
    warnings: [],
    mode: 'manual',
  };
}

function computeLocalBpDistribution(columns) {
  const breakdown = {
    hypotension: 0, optimal: 0, normal: 0, high_normal: 0,
    hypertension_grade_1: 0, hypertension_grade_2: 0, hypertension_grade_3: 0,
  };
  const classify = (sys, dia) => {
    if (sys < 90 || dia < 60) return 'hypotension';
    if (sys >= 180 || dia >= 110) return 'hypertension_grade_3';
    if (sys >= 160 || dia >= 100) return 'hypertension_grade_2';
    if (sys >= 140 || dia >= 90) return 'hypertension_grade_1';
    if (sys >= 130 || dia >= 85) return 'high_normal';
    if (sys >= 120 || dia >= 80) return 'normal';
    return 'optimal';
  };
  for (const entries of Object.values(columns)) {
    for (const entry of entries) {
      const sys = Number(entry.sys);
      const dia = Number(entry.dia);
      if (!Number.isFinite(sys) || !Number.isFinite(dia)) continue;
      breakdown[classify(sys, dia)] += 1;
    }
  }
  return breakdown;
}

function mapImportedPayload(payload) {
  const columns = { cuffless: [], phone: [], cuff: [] };

  for (const measurement of payload.measurements || []) {
    const target = IMPORT_TYPE_TO_COLUMN[measurement.measurement_type];
    if (!target) continue;
    columns[target].push({
      entry_id: measurement.entry_id,
      id: measurement.entry_id,
      datetime: measurement.datetime,
      sys: measurement.systolic,
      dia: measurement.diastolic,
      pulse: measurement.heart_rate,
      typeLabel: IMPORT_TYPE_LABELS[measurement.measurement_type] || measurement.measurement_type,
      measurement_type: measurement.measurement_type,
      entry_status: measurement.entry_status,
      source: measurement.source,
      source_file: measurement.source_file,
      original_values: measurement.original_values,
    });
  }

  Object.values(columns).forEach((entries) => entries.sort((a, b) => new Date(a.datetime) - new Date(b.datetime)));

  return {
    columns,
    report: payload.report || null,
    patient: payload.patient || null,
    summary: payload.summary || {},
    warnings: payload.warnings || [],
    mode: 'import',
    raw: payload,
  };
}

// --- dashboard render -----------------------------------------------

function renderReportSummary(viewModel) {
  const patient = viewModel.patient || {};
  const report = viewModel.report || {};
  const summary = viewModel.summary || {};
  const byType = summary.measurement_count_by_type || {};

  setText('reportMonthLabel', report.report_month && report.report_year ? `${report.report_month}/${report.report_year}` : 'Kein Bericht');
  setText('summaryName', patient.full_name || '-');
  setText('summaryAge', patient.age_at_report_date ?? '-');
  setText('summaryGender', patient.gender || '-');
  setText('summaryHeight', patient.height_cm ? `${patient.height_cm} cm` : '-');
  setText('summaryWeight', patient.weight_kg ? `${patient.weight_kg} kg` : '-');
  setText('summaryEmail', patient.email || '-');

  setText('summaryTotal', summary.measurement_count_total ?? 0);
  setText('summaryDay', summary.measurement_count_day_rest ?? 0);
  setText('summaryNight', summary.measurement_count_night ?? 0);
  setText('summaryUnknown', byType.unknown ?? 0);
  setText('summaryArmband', byType.armband ?? 0);
  setText('summaryCalibration', byType.cuff_calibration ?? 0);
  setText('summaryCuff', byType.cuff_measurement ?? 0);
  setText('summaryPhone', byType.phone_measurement ?? 0);

  renderBpAverages(summary);
  renderBpDistribution(summary);

  const warnings = viewModel.warnings || [];
  const warningInline = document.getElementById('warningInline');
  const warningList = document.getElementById('warningList');
  if (warningList && warningInline) {
    if (warnings.length > 0) {
      warningList.innerHTML = warnings.map((warning) => `<li>${warning}</li>`).join('');
      warningInline.hidden = false;
    } else {
      warningList.innerHTML = '';
      warningInline.hidden = true;
    }
  }
}

// --- Blutdruckkategorien (ESH/ESC 2023) ------------------------------

// Zeichnet den prozentualen Anteil eines aktiv gehoverten Segments mittig
// in den Donut. Ohne Hover bleibt die Mitte leer — so ersetzt der Plugin
// den früheren statischen „Keine Daten"-Text beim Datenzustand.
const BP_DONUT_CENTER_PLUGIN = {
  id: 'bpDonutCenter',
  afterDraw(chart) {
    const idx = chart.$bpActiveIndex;
    const total = chart.$bpTotal || 0;
    if (idx == null || total <= 0) return;
    const dataset = chart.data.datasets?.[0];
    if (!dataset) return;
    const value = Number(dataset.data?.[idx] ?? 0);
    if (!Number.isFinite(value) || value <= 0) return;
    const share = (value / total) * 100;
    const label = chart.data.labels?.[idx] ?? '';

    const { ctx, chartArea } = chart;
    const cx = (chartArea.left + chartArea.right) / 2;
    const cy = (chartArea.top + chartArea.bottom) / 2;

    ctx.save();
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = '#f8fafc';
    ctx.font = '700 1.35rem Inter, system-ui, sans-serif';
    ctx.fillText(`${share.toFixed(1)} %`, cx, cy - 8);
    ctx.fillStyle = '#94a3b8';
    ctx.font = '500 0.72rem Inter, system-ui, sans-serif';
    ctx.fillText(String(label), cx, cy + 12);
    ctx.restore();
  },
};

const BP_CATEGORY_META = [
  { key: 'optimal', label: 'Optimal', color: '#10b981', range: 'SYS < 120 und DIA < 80',  sourceKeys: ['hypotension', 'optimal'] },
  { key: 'normal',  label: 'Normal',  color: '#84cc16', range: 'SYS 120–129 und DIA 80–84', sourceKeys: ['normal'] },
  { key: 'erhoeht', label: 'Erhöht',  color: '#f59e0b', range: 'SYS 130–139 oder DIA 85–89', sourceKeys: ['high_normal'] },
  { key: 'hoch',    label: 'Hoch',    color: '#ef4444', range: 'SYS ≥ 140 oder DIA ≥ 90',    sourceKeys: ['hypertension_grade_1', 'hypertension_grade_2', 'hypertension_grade_3'] },
];

function renderBpDistribution(summary) {
  const canvas = document.getElementById('bpDistributionChart');
  const emptyNode = document.getElementById('bpDistributionEmpty');
  const legendNode = document.getElementById('bpDistributionLegend');
  const totalNode = document.getElementById('bpDistributionTotal');
  if (!canvas || !emptyNode || !legendNode) return;

  const breakdown = (summary && summary.measurement_count_by_bp_category) || {};
  const data = BP_CATEGORY_META.map((meta) => {
    const count = meta.sourceKeys.reduce((sum, key) => sum + Number(breakdown[key] ?? 0), 0);
    return { ...meta, count };
  });
  const total = data.reduce((acc, item) => acc + item.count, 0);

  if (totalNode) {
    totalNode.textContent = total > 0 ? `${total} Messungen` : 'Keine Daten';
  }

  if (total === 0) {
    emptyNode.hidden = false;
    canvas.style.display = 'none';
    legendNode.innerHTML = '';
    if (_charts.bpDistribution) {
      _charts.bpDistribution.destroy();
      delete _charts.bpDistribution;
    }
    return;
  }

  emptyNode.hidden = true;
  emptyNode.style.display = 'none';
  canvas.style.display = '';

  const labels = data.map((item) => item.label);
  const values = data.map((item) => item.count);
  const colors = data.map((item) => item.color);

  if (_charts.bpDistribution) {
    _charts.bpDistribution.$bpTotal = total;
    _charts.bpDistribution.data.labels = labels;
    _charts.bpDistribution.data.datasets[0].data = values;
    _charts.bpDistribution.data.datasets[0].backgroundColor = colors;
    _charts.bpDistribution.update();
  } else {
    _charts.bpDistribution = new Chart(canvas.getContext('2d'), {
      type: 'doughnut',
      data: {
        labels,
        datasets: [{
          data: values,
          backgroundColor: colors,
          borderColor: 'rgba(15,23,42,0.9)',
          borderWidth: 2,
          hoverOffset: 10,
          hoverBorderColor: '#f8fafc',
          hoverBorderWidth: 2,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: '62%',
        layout: { padding: 4 },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: 'rgba(15,23,42,0.95)',
            titleColor: '#f8fafc',
            bodyColor: '#e2e8f0',
            borderColor: 'rgba(148,163,184,0.4)',
            borderWidth: 1,
            padding: 10,
            displayColors: true,
            callbacks: {
              label: (ctx) => {
                const chartTotal = ctx.chart.$bpTotal || 0;
                const value = Number(ctx.parsed ?? 0);
                const share = chartTotal > 0 ? ((value / chartTotal) * 100).toFixed(1) : '0.0';
                return `${ctx.label}: ${value} (${share} %)`;
              },
            },
          },
        },
        onHover: (event, activeElements, chart) => {
          const target = event?.native?.target;
          if (target && target.style) {
            target.style.cursor = activeElements.length ? 'pointer' : 'default';
          }
          chart.$bpActiveIndex = activeElements.length ? activeElements[0].index : null;
          chart.draw();
        },
      },
      plugins: [BP_DONUT_CENTER_PLUGIN],
    });
    _charts.bpDistribution.$bpTotal = total;
  }

  legendNode.innerHTML = data.map((item) => {
    if (item.count === 0) return '';
    const share = ((item.count / total) * 100).toFixed(1);
    return `
      <li class="bp-legend-item" title="${item.range}">
        <span class="bp-legend-dot" style="background:${item.color}"></span>
        <span class="bp-legend-label">${item.label}</span>
        <span class="bp-legend-value">${item.count} <em>(${share} %)</em></span>
      </li>`;
  }).join('');
}

function renderBpAverages(currentSummary) {
  // Bevorzugt die übergreifenden Cross-Report-Mittelwerte (alle importierten
  // Berichte zusammen). Fällt auf den aktuellen Bericht zurück, falls die
  // Unified-View noch nicht geladen wurde — so funktioniert die Anzeige
  // sowohl direkt nach einem Import als auch im Multi-Bericht-Modus.
  const cross = _unifiedPayload?.cross_report_averages || null;
  const reportCount = cross?.report_count || 0;
  const useCross = cross && reportCount > 0;

  const summary = currentSummary || {};
  const night = summary.night || {};
  const day = summary.day_rest || {};

  const nightSys = useCross ? cross.night_systolic : night.mean;
  const nightDia = useCross ? cross.night_diastolic : night.mean_diastolic;
  const nightHr = useCross ? cross.night_heart_rate : night.mean_heart_rate;
  const daySys = useCross ? cross.day_rest_systolic : day.mean;
  const dayDia = useCross ? cross.day_rest_diastolic : day.mean_diastolic;
  const dayHr = useCross ? cross.day_rest_heart_rate : day.mean_heart_rate;

  setText('avgNightBp', formatBpPair(nightSys, nightDia));
  setText('avgNightHr', formatBpSingle(nightHr, 'bpm'));
  setText('avgDayBp', formatBpPair(daySys, dayDia));
  setText('avgDayHr', formatBpSingle(dayHr, 'bpm'));

  const scopeLabel = useCross
    ? `Quelle: ${reportCount} importierte Berichte (gewichtet nach Messanzahl)`
    : 'Quelle: aktueller Bericht';
  setText('avgScopeLabel', scopeLabel);
  renderOverviewTable(summary);
}

function renderOverviewTable(summary) {
  const day = summary?.day_rest || {};
  const night = summary?.night || {};
  const all = summary?.all_measurements || {};

  setText('overviewMeanDay', formatBpPair(day.mean, day.mean_diastolic));
  setText('overviewMeanNight', formatBpPair(night.mean, night.mean_diastolic));
  setText('overviewMeanAll', formatBpPair(all.mean, all.mean_diastolic));

  setText('overviewMaxDay', formatBpPair(day.max, day.max_diastolic));
  setText('overviewMaxNight', formatBpPair(night.max, night.max_diastolic));
  setText('overviewMaxAll', formatBpPair(all.max, all.max_diastolic));

  setText('overviewMinDay', formatBpPair(day.min, day.min_diastolic));
  setText('overviewMinNight', formatBpPair(night.min, night.min_diastolic));
  setText('overviewMinAll', formatBpPair(all.min, all.min_diastolic));
}

function formatBpPair(sys, dia) {
  const s = Number.isFinite(Number(sys)) ? Math.round(Number(sys)) : null;
  const d = Number.isFinite(Number(dia)) ? Math.round(Number(dia)) : null;
  if (s === null && d === null) return '--';
  return `${s ?? '--'} / ${d ?? '--'} mmHg`;
}

function formatBpSingle(value, unit) {
  const v = Number.isFinite(Number(value)) ? Math.round(Number(value)) : null;
  return v === null ? '--' : `${v} ${unit}`;
}

function renderDashboardColumns(viewModel) {
  Object.keys(TYPES).forEach((type) => {
    const entries = viewModel.columns[type] || [];
    updateStats(type, entries);
    renderList(type, entries);
    createBPChart(`chart-${type}`, entries);
  });
}

function updateLastUpdateLabel(viewModel) {
  const el = document.getElementById('lastUpdate');
  if (!el) return;

  const allEntries = Object.values(viewModel.columns)
    .flat()
    .map((entry) => new Date(entry.datetime).getTime())
    .filter((value) => !Number.isNaN(value));

  const totalImported = viewModel.summary ? viewModel.summary.measurement_count_total || 0 : 0;

  if (allEntries.length === 0) {
    if (viewModel.mode === 'import' && viewModel.report && totalImported > 0) {
      el.textContent = `Importierter Bericht ${viewModel.report.report_month}/${viewModel.report.report_year} - ${totalImported} Messungen (ohne erkannte Messart)`;
      return;
    }
    el.innerHTML = 'Noch keine Daten vorhanden - Daten im <a href="configurator.html" class="inline-link">Konfigurator</a> eintragen';
    return;
  }

  const latest = fmtDatetime(new Date(Math.max(...allEntries)).toISOString()).full;
  if (viewModel.mode === 'import' && viewModel.report) {
    el.textContent = `Importierter Bericht ${viewModel.report.report_month}/${viewModel.report.report_year} - letzte Messung: ${latest}`;
  } else {
    el.textContent = `Letzte manuelle Messung: ${latest}`;
  }
}

function renderDashboardView(viewModel) {
  renderDashboardColumns(viewModel);
  renderReportSummary(viewModel);
  updateLastUpdateLabel(viewModel);
  renderAnalysisViews();
}

// --- analysis views (unified + bland-altman) ------------------------

// Measurement-type colors are chosen distinct from the parameter line colors
// below (red / orange / green) so that points remain visible on every line.
const MEASUREMENT_TYPE_META = {
  armband: { label: 'Armband', color: '#6366f1' },
  cuff_calibration: { label: 'Kalibrierung', color: '#a855f7' },
  phone_measurement: { label: 'Telefon', color: '#06b6d4' },
  cuff_measurement: { label: 'Manschette', color: '#ec4899' },
  unknown: { label: 'Unbekannt', color: '#94a3b8' },
};

const PARAM_META = {
  sbp: { label: 'SBP', key: 'systolic',   dash: [],      lineColor: '#ef4444', filterId: 'filter-sbp' },
  dbp: { label: 'DBP', key: 'diastolic',  dash: [6, 4],  lineColor: '#f59e0b', filterId: 'filter-dbp' },
  hr:  { label: 'HR',  key: 'heart_rate', dash: [2, 4],  lineColor: '#22c55e', filterId: 'filter-hr'  },
};

const MAX_ZOOM_LEVEL = 10;

const _analysisState = {
  initialized: false,
  activeTab: 'unified',
  filters: { sbp: true, dbp: true, hr: true },
  zoomPluginRegistered: false,
  dayNight: 'all',           // 'all' | 'day' | 'night'
  timeWindow: { start: null, end: null }, // ISO dates (YYYY-MM-DD) or null
};

function buildZoomOptions({ fullMin, fullMax, chartId }) {
  if (!ensureZoomPlugin()) return undefined;
  const minRange = Math.max(1, (fullMax - fullMin) / MAX_ZOOM_LEVEL);
  const onUpdate = () => {
    if (chartId === 'unified-chart') updateZoomStatus();
  };
  return {
    zoom: {
      wheel: { enabled: true, speed: 0.12 },
      pinch: { enabled: true },
      drag: { enabled: false },
      mode: 'x',
      onZoom: onUpdate,
    },
    pan: {
      enabled: true,
      mode: 'x',
      threshold: 5,
      onPan: onUpdate,
    },
    limits: {
      x: { min: fullMin, max: fullMax, minRange },
    },
  };
}

function getServerMeasurements() {
  // Prefer the cross-report unified payload so the analysis views always
  // reflect every imported report. Fall back to the single-report payload
  // while the unified endpoint has not been fetched yet.
  let source = [];
  if (_unifiedPayload && Array.isArray(_unifiedPayload.measurements)) {
    source = _unifiedPayload.measurements;
  } else if (_lastServerPayload && Array.isArray(_lastServerPayload.measurements)) {
    source = _lastServerPayload.measurements;
  }

  if (_serverMeasurementsCache.sourceRef === source) {
    return _serverMeasurementsCache.value;
  }

  const sorted = source
    .filter((m) => m && m.datetime && Number.isFinite(new Date(m.datetime).getTime()))
    .slice()
    .sort((a, b) => new Date(a.datetime) - new Date(b.datetime));

  _serverMeasurementsCache = { sourceRef: source, value: sorted };
  return sorted;
}

function parseIsoDateOnly(isoDate) {
  // Interpret "YYYY-MM-DD" as local midnight so the user's selection on the
  // date picker corresponds to local days, not UTC days.
  if (!isoDate) return null;
  const [y, m, d] = isoDate.split('-').map(Number);
  if (!y || !m || !d) return null;
  return new Date(y, m - 1, d).getTime();
}

function applyAnalysisFilters(measurements) {
  const { dayNight, timeWindow } = _analysisState;
  let startMs = parseIsoDateOnly(timeWindow.start);
  let endMs = parseIsoDateOnly(timeWindow.end);
  if (Number.isFinite(endMs)) endMs += DAY_MS - 1; // include full end-day

  return measurements.filter((m) => {
    if (dayNight !== 'all') {
      const isNight = Boolean(m.is_night);
      if (dayNight === 'night' && !isNight) return false;
      if (dayNight === 'day' && isNight) return false;
    }
    if (Number.isFinite(startMs) || Number.isFinite(endMs)) {
      const t = new Date(m.datetime).getTime();
      if (Number.isFinite(startMs) && t < startMs) return false;
      if (Number.isFinite(endMs) && t > endMs) return false;
    }
    return true;
  });
}

function getFilteredMeasurements() {
  const source = getServerMeasurements();
  const filters = _analysisState.filters;
  const key = [
    source.length,
    _analysisState.dayNight,
    _analysisState.timeWindow.start || '',
    _analysisState.timeWindow.end || '',
    filters.sbp ? 1 : 0,
    filters.dbp ? 1 : 0,
    filters.hr ? 1 : 0,
  ].join('|');

  if (_filteredMeasurementsCache.key === key) return _filteredMeasurementsCache.value;
  const filtered = applyAnalysisFilters(source);
  _filteredMeasurementsCache = { key, value: filtered };
  return filtered;
}

function typeMeta(type) {
  return MEASUREMENT_TYPE_META[type] || MEASUREMENT_TYPE_META.unknown;
}

function ensureZoomPlugin() {
  if (_analysisState.zoomPluginRegistered) return true;
  if (typeof Chart === 'undefined') return false;
  const plugin = window.ChartZoom || (window.chartjsPluginZoom && (window.chartjsPluginZoom.default || window.chartjsPluginZoom));
  if (plugin) {
    try {
      Chart.register(plugin.default || plugin);
    } catch {
      // already registered by UMD bundle
    }
    _analysisState.zoomPluginRegistered = true;
    return true;
  }
  return false;
}

function enabledParams() {
  return Object.entries(_analysisState.filters)
    .filter(([, enabled]) => enabled)
    .map(([id]) => id);
}

function formatRange(minMs, maxMs) {
  if (!Number.isFinite(minMs) || !Number.isFinite(maxMs) || minMs >= maxMs) return '-';
  const start = new Date(minMs);
  const end = new Date(maxMs);
  const fmtDate = (d) => d.toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit', year: '2-digit' });
  const fmtTime = (d) => d.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
  const spanMs = maxMs - minMs;
  const sameDay = start.toDateString() === end.toDateString();
  if (sameDay) return `${fmtDate(start)} ${fmtTime(start)} - ${fmtTime(end)}`;
  if (spanMs < 1000 * 60 * 60 * 24 * 3) {
    return `${fmtDate(start)} ${fmtTime(start)} - ${fmtDate(end)} ${fmtTime(end)}`;
  }
  return `${fmtDate(start)} - ${fmtDate(end)}`;
}

function updateZoomStatus() {
  const chart = _charts['unified-chart'];
  const levelEl = document.getElementById('zoomLevel');
  const windowEl = document.getElementById('zoomWindow');
  const slider = document.getElementById('zoomSlider');
  if (!chart) return;

  const xScale = chart.scales.x;
  const totalMin = chart.$fullRange?.min;
  const totalMax = chart.$fullRange?.max;
  const visibleMin = xScale ? xScale.min : totalMin;
  const visibleMax = xScale ? xScale.max : totalMax;

  let level = 1;
  if (Number.isFinite(totalMin) && Number.isFinite(totalMax) && totalMax > totalMin) {
    const visibleSpan = Math.max(1, visibleMax - visibleMin);
    level = Math.min(MAX_ZOOM_LEVEL, Math.max(1, (totalMax - totalMin) / visibleSpan));
  }

  if (levelEl) levelEl.textContent = `${level.toFixed(2)}x`;
  if (windowEl) windowEl.textContent = formatRange(visibleMin, visibleMax);
  if (slider && document.activeElement !== slider) slider.value = level.toFixed(2);
}

// --- unified view ----------------------------------------------------

function renderUnifiedTable(measurements) {
  const tbody = document.getElementById('unifiedTableBody');
  const count = document.getElementById('unifiedCount');
  const empty = document.getElementById('unifiedTableEmpty');
  if (!tbody) return;

  if (count) {
    count.textContent = `${measurements.length} ${measurements.length === 1 ? 'Eintrag' : 'Einträge'}`;
  }

  if (measurements.length === 0) {
    tbody.innerHTML = '';
    if (empty) empty.style.display = 'block';
    return;
  }
  if (empty) empty.style.display = 'none';

  const rows = measurements.slice().reverse().map((m) => {
    const dt = fmtDatetime(m.datetime);
    const meta = typeMeta(m.measurement_type);
    const sys = Number.isFinite(m.systolic) ? m.systolic : '-';
    const dia = Number.isFinite(m.diastolic) ? m.diastolic : '-';
    const hr = Number.isFinite(m.heart_rate) ? m.heart_rate : '-';
    return `<tr>
      <td><span class="type-badge" style="background:${meta.color}22;color:${meta.color}">${meta.label}</span></td>
      <td>${dt.date}</td>
      <td>${dt.time}</td>
      <td><span class="sys" style="color:var(--c-sys);font-weight:600">${sys}</span></td>
      <td><span class="dia" style="color:var(--c-dia);font-weight:600">${dia}</span></td>
      <td><span class="pulse" style="color:var(--c-pulse);font-weight:600">${hr}</span></td>
    </tr>`;
  }).join('');
  tbody.innerHTML = rows;
}

function buildUnifiedDatasets(measurements) {
  const params = enabledParams();
  const datasets = [];

  for (const paramId of params) {
    const paramMeta = PARAM_META[paramId];
    const points = [];
    for (const m of measurements) {
      const value = m[paramMeta.key];
      if (!Number.isFinite(value)) continue;
      points.push({
        x: new Date(m.datetime).getTime(),
        y: value,
        _type: m.measurement_type || 'unknown',
      });
    }
    if (points.length === 0) continue;
    points.sort((a, b) => a.x - b.x);

    const pointColors = points.map((p) => typeMeta(p._type).color);

    datasets.push({
      label: paramMeta.label,
      data: points,
      parsing: false,
      normalized: true,
      borderColor: paramMeta.lineColor,
      backgroundColor: `${paramMeta.lineColor}18`,
      borderDash: paramMeta.dash,
      borderWidth: 1.75,
      tension: 0.15,
      fill: false,
      spanGaps: true,
      pointRadius: 5,
      pointHoverRadius: 8,
      pointBackgroundColor: pointColors,
      pointBorderColor: '#0f172a',
      pointBorderWidth: 1.5,
      _paramId: paramId,
    });
  }

  return datasets;
}

function renderTypeLegend(measurements) {
  const container = document.getElementById('typeLegend');
  if (!container) return;

  const present = new Set();
  for (const m of measurements) present.add(m.measurement_type || 'unknown');

  const order = ['armband', 'cuff_calibration', 'phone_measurement', 'cuff_measurement', 'unknown'];
  const items = order.filter((type) => present.has(type));
  if (items.length === 0) {
    container.innerHTML = '';
    return;
  }

  container.innerHTML = items.map((type) => {
    const meta = typeMeta(type);
    return `<span class="type-legend-item">
      <span class="type-legend-dot" style="background:${meta.color}"></span>
      ${meta.label}
    </span>`;
  }).join('');
}

const SLEEP_BANDS_PLUGIN = {
  id: 'sleepBands',
  beforeDatasetsDraw(chart) {
    const episodes = _unifiedPayload?.sleep_episodes || [];
    if (!episodes.length) return;
    const xScale = chart.scales?.x;
    const yScale = chart.scales?.y;
    if (!xScale || !yScale) return;

    const ctx = chart.ctx;
    const top = yScale.top;
    const bottom = yScale.bottom;
    const xMin = xScale.min;
    const xMax = xScale.max;

    ctx.save();
    for (const ep of episodes) {
      const startMs = new Date(ep.sleep_onset_estimate || ep.start).getTime();
      const endMs = new Date(ep.wake_estimate || ep.end).getTime();
      if (!Number.isFinite(startMs) || !Number.isFinite(endMs)) continue;
      if (endMs < xMin || startMs > xMax) continue;
      const clampedStart = Math.max(startMs, xMin);
      const clampedEnd = Math.min(endMs, xMax);
      const x1 = xScale.getPixelForValue(clampedStart);
      const x2 = xScale.getPixelForValue(clampedEnd);
      if (!Number.isFinite(x1) || !Number.isFinite(x2) || x2 <= x1) continue;
      const alpha = 0.07 + 0.10 * Math.min(1, Math.max(0, ep.confidence ?? 0));
      ctx.fillStyle = `rgba(99,102,241,${alpha.toFixed(3)})`;
      ctx.fillRect(x1, top, x2 - x1, bottom - top);
      // Top hairline so the band stays visible even on dark backgrounds.
      ctx.fillStyle = 'rgba(99,102,241,0.45)';
      ctx.fillRect(x1, top, Math.max(1, x2 - x1), 1);
    }
    ctx.restore();
  },
};

function renderUnifiedChart(measurements) {
  const canvas = document.getElementById('unified-chart');
  const empty = document.getElementById('unified-chart-empty');
  if (!canvas) return;

  renderTypeLegend(measurements);

  const datasets = buildUnifiedDatasets(measurements);
  const hasData = datasets.length > 0 && measurements.length > 0;

  if (!hasData) {
    if (_charts['unified-chart']) {
      _charts['unified-chart'].destroy();
      delete _charts['unified-chart'];
    }
    canvas.style.display = 'none';
    if (empty) empty.style.display = 'flex';
    const levelEl = document.getElementById('zoomLevel');
    const windowEl = document.getElementById('zoomWindow');
    const slider = document.getElementById('zoomSlider');
    if (levelEl) levelEl.textContent = '1.0x';
    if (windowEl) windowEl.textContent = '-';
    if (slider) slider.value = '1';
    return;
  }

  canvas.style.display = 'block';
  if (empty) empty.style.display = 'none';

  const timestamps = measurements.map((m) => new Date(m.datetime).getTime());
  const fullMin = Math.min(...timestamps);
  const fullMax = Math.max(...timestamps);

  const existing = _charts['unified-chart'];
  if (existing) {
    // Preserve the user's current zoom/pan when only the dataset filter
    // (parameter checkboxes, day/night, ...) changes. We only reset the
    // visible range when the underlying time window has actually changed
    // (new import, deleted report, new time-window selection).
    const prevRange = existing.$fullRange || {};
    const rangeChanged = prevRange.min !== fullMin || prevRange.max !== fullMax;

    existing.data.datasets = datasets;
    existing.$fullRange = { min: fullMin, max: fullMax };

    if (existing.options.plugins.zoom?.limits?.x) {
      existing.options.plugins.zoom.limits.x.min = fullMin;
      existing.options.plugins.zoom.limits.x.max = fullMax;
      existing.options.plugins.zoom.limits.x.minRange = Math.max(1, (fullMax - fullMin) / MAX_ZOOM_LEVEL);
    }

    if (rangeChanged) {
      existing.options.scales.x.min = fullMin;
      existing.options.scales.x.max = fullMax;
      if (typeof existing.resetZoom === 'function') {
        // Clear any leftover zoom-plugin state that would otherwise override
        // the freshly set visible range.
        existing.resetZoom('none');
      }
    }

    existing.update('none');
    updateZoomStatus();
    return;
  }

  const zoomOptions = buildZoomOptions({ fullMin, fullMax, chartId: 'unified-chart' });

  const chart = new Chart(canvas.getContext('2d'), {
    type: 'line',
    data: { datasets },
    plugins: [SLEEP_BANDS_PLUGIN],
    options: {
      animation: false,
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'nearest', intersect: false },
      plugins: {
        legend: {
          display: true,
          position: 'top',
          align: 'start',
          labels: {
            color: '#cbd5e1',
            usePointStyle: true,
            pointStyleWidth: 14,
            boxHeight: 8,
            padding: 12,
            font: { size: 12, family: 'Inter, system-ui', weight: '600' },
          },
        },
        tooltip: {
          callbacks: {
            title: (items) => {
              if (!items.length) return '';
              return new Date(items[0].parsed.x).toLocaleString('de-DE', {
                day: '2-digit', month: '2-digit', year: 'numeric',
                hour: '2-digit', minute: '2-digit',
              });
            },
            label: (ctx) => {
              const raw = ctx.raw || {};
              const typeLabel = raw._type ? typeMeta(raw._type).label : '';
              return `${ctx.dataset.label}: ${ctx.parsed.y}${typeLabel ? ` (${typeLabel})` : ''}`;
            },
          },
        },
        ...(zoomOptions ? { zoom: zoomOptions } : {}),
      },
      scales: {
        x: {
          type: 'time',
          min: fullMin,
          max: fullMax,
          time: {
            displayFormats: { minute: 'HH:mm', hour: 'dd.MM HH:mm', day: 'dd.MM', month: 'MM.yy' },
            tooltipFormat: 'dd.MM.yyyy HH:mm',
          },
          grid: { color: 'rgba(255,255,255,0.06)' },
          ticks: { color: '#94a3b8', font: { size: 11 }, maxRotation: 0, maxTicksLimit: 8 },
          border: { color: 'rgba(255,255,255,0.12)' },
        },
        y: {
          min: 30,
          suggestedMax: 200,
          grid: { color: 'rgba(255,255,255,0.06)' },
          ticks: { color: '#94a3b8', font: { size: 11 }, stepSize: 20 },
          border: { color: 'rgba(255,255,255,0.12)' },
        },
      },
    },
  });

  chart.$fullRange = { min: fullMin, max: fullMax };
  _charts['unified-chart'] = chart;
  updateZoomStatus();
}

function zoomUnifiedBy(factor) {
  const chart = _charts['unified-chart'];
  if (!chart || typeof chart.zoom !== 'function') return;
  chart.zoom(factor);
  updateZoomStatus();
}

function resetUnifiedZoom() {
  const chart = _charts['unified-chart'];
  if (!chart) return;
  if (typeof chart.resetZoom === 'function') {
    chart.resetZoom();
  }
  const slider = document.getElementById('zoomSlider');
  if (slider) slider.value = '1';
  updateZoomStatus();
}

function applyZoomLevel(level) {
  const chart = _charts['unified-chart'];
  if (!chart || !chart.$fullRange) return;
  const clamped = Math.min(MAX_ZOOM_LEVEL, Math.max(1, Number(level) || 1));
  const { min: fullMin, max: fullMax } = chart.$fullRange;
  const fullSpan = fullMax - fullMin;
  if (fullSpan <= 0) return;

  const targetSpan = fullSpan / clamped;
  const xScale = chart.scales.x;
  const currentCenter = xScale ? (xScale.min + xScale.max) / 2 : (fullMin + fullMax) / 2;
  let newMin = currentCenter - targetSpan / 2;
  let newMax = currentCenter + targetSpan / 2;
  if (newMin < fullMin) { newMin = fullMin; newMax = fullMin + targetSpan; }
  if (newMax > fullMax) { newMax = fullMax; newMin = fullMax - targetSpan; }

  if (typeof chart.zoomScale === 'function') {
    chart.zoomScale('x', { min: newMin, max: newMax }, 'default');
  } else {
    chart.options.scales.x.min = newMin;
    chart.options.scales.x.max = newMax;
    chart.update('none');
  }
  updateZoomStatus();
}

function renderUnifiedView() {
  const measurements = getFilteredMeasurements();
  renderUnifiedTable(measurements);
  renderUnifiedChart(measurements);
  if (typeof _renderMlSleepInfo === 'function') _renderMlSleepInfo();
}

// --- bland-altman plot ----------------------------------------------

function dayKey(iso) {
  const d = new Date(iso);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function computeDailyAggregates(measurements) {
  const byType = new Map();

  for (const m of measurements) {
    const type = m.measurement_type || 'unknown';
    const key = dayKey(m.datetime);
    if (!byType.has(type)) byType.set(type, new Map());
    const days = byType.get(type);
    if (!days.has(key)) days.set(key, { sbp: [], dbp: [] });
    const bucket = days.get(key);
    if (Number.isFinite(m.systolic)) bucket.sbp.push(m.systolic);
    if (Number.isFinite(m.diastolic)) bucket.dbp.push(m.diastolic);
  }

  const result = new Map();
  const avg = (arr) => arr.reduce((s, v) => s + v, 0) / arr.length;
  for (const [type, days] of byType.entries()) {
    const pts = [];
    for (const [date, vals] of days.entries()) {
      if (vals.sbp.length > 0) {
        pts.push({
          date, param: 'SBP',
          mean: avg(vals.sbp),
          range: Math.max(...vals.sbp) - Math.min(...vals.sbp),
          count: vals.sbp.length,
        });
      }
      if (vals.dbp.length > 0) {
        pts.push({
          date, param: 'DBP',
          mean: avg(vals.dbp),
          range: Math.max(...vals.dbp) - Math.min(...vals.dbp),
          count: vals.dbp.length,
        });
      }
    }
    if (pts.length > 0) result.set(type, pts);
  }
  return result;
}

function computeReferenceStats(aggregates) {
  const allRanges = [];
  for (const pts of aggregates.values()) {
    for (const p of pts) allRanges.push(p.range);
  }
  if (allRanges.length === 0) return null;
  const mean = allRanges.reduce((s, v) => s + v, 0) / allRanges.length;
  const variance = allRanges.reduce((s, v) => s + (v - mean) ** 2, 0) / allRanges.length;
  const sd = Math.sqrt(variance);
  return { mean, sd, upper: mean + 1.96 * sd, lower: Math.max(0, mean - 1.96 * sd) };
}

function buildBlandAltmanDatasets(aggregates, stats, xBounds) {
  const datasets = [];

  for (const [type, pts] of aggregates.entries()) {
    const meta = typeMeta(type);
    datasets.push({
      type: 'scatter',
      label: meta.label,
      data: pts.map((p) => ({ x: p.mean, y: p.range, param: p.param, date: p.date, count: p.count })),
      backgroundColor: meta.color,
      borderColor: '#0f172a',
      borderWidth: 2.5,
      hoverBorderColor: '#f8fafc',
      hoverBorderWidth: 3,
      pointRadius: 10,
      pointHoverRadius: 13,
      pointStyle: (ctx) => {
        const raw = ctx.raw;
        if (!raw) return 'circle';
        return raw.param === 'DBP' ? 'triangle' : 'circle';
      },
    });
  }

  if (stats && xBounds) {
    const span = [{ x: xBounds.min, y: stats.mean }, { x: xBounds.max, y: stats.mean }];
    const upper = [{ x: xBounds.min, y: stats.upper }, { x: xBounds.max, y: stats.upper }];
    const lower = [{ x: xBounds.min, y: stats.lower }, { x: xBounds.max, y: stats.lower }];

    const refBase = {
      type: 'line',
      pointRadius: 0,
      pointHoverRadius: 0,
      borderWidth: 1.5,
      fill: false,
      showLine: true,
      tension: 0,
      order: 0,
    };
    datasets.push({
      ...refBase,
      label: `Mittel: ${stats.mean.toFixed(1)} mmHg`,
      data: span,
      borderColor: '#f1f5f9',
      borderDash: [],
    });
    datasets.push({
      ...refBase,
      label: `+1,96 SD: ${stats.upper.toFixed(1)}`,
      data: upper,
      borderColor: '#94a3b8',
      borderDash: [4, 4],
    });
    datasets.push({
      ...refBase,
      label: `-1,96 SD: ${stats.lower.toFixed(1)}`,
      data: lower,
      borderColor: '#94a3b8',
      borderDash: [4, 4],
    });
  }

  return datasets;
}

function renderBlandAltmanChart() {
  const canvas = document.getElementById('bland-chart');
  const empty = document.getElementById('bland-chart-empty');
  if (!canvas) return;

  const measurements = getFilteredMeasurements();
  const aggregates = computeDailyAggregates(measurements);

  let totalPoints = 0;
  for (const pts of aggregates.values()) totalPoints += pts.length;

  if (totalPoints < 2) {
    if (_charts['bland-chart']) {
      _charts['bland-chart'].destroy();
      delete _charts['bland-chart'];
    }
    canvas.style.display = 'none';
    if (empty) {
      empty.style.display = 'flex';
      const message = totalPoints === 0
        ? ' Keine importierten Messdaten für Bland-Altman.'
        : ' Nicht genügend Tage mit Messwerten für einen sinnvollen Plot.';
      const textNode = Array.from(empty.childNodes).find((n) => n.nodeType === Node.TEXT_NODE);
      if (textNode) textNode.textContent = message;
    }
    return;
  }

  canvas.style.display = 'block';
  if (empty) empty.style.display = 'none';

  const stats = computeReferenceStats(aggregates);
  let minX = Infinity, maxX = -Infinity;
  for (const pts of aggregates.values()) {
    for (const p of pts) {
      if (p.mean < minX) minX = p.mean;
      if (p.mean > maxX) maxX = p.mean;
    }
  }
  if (!Number.isFinite(minX) || !Number.isFinite(maxX)) { minX = 0; maxX = 200; }
  if (minX === maxX) { minX -= 5; maxX += 5; }
  const padX = (maxX - minX) * 0.05;
  const xBounds = { min: minX - padX, max: maxX + padX };

  const datasets = buildBlandAltmanDatasets(aggregates, stats, xBounds);

  if (_charts['bland-chart']) {
    _charts['bland-chart'].destroy();
    delete _charts['bland-chart'];
  }

  _charts['bland-chart'] = new Chart(canvas.getContext('2d'), {
    data: { datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      layout: { padding: { top: 10, right: 24, bottom: 10, left: 10 } },
      plugins: {
        legend: {
          display: true,
          position: 'top',
          align: 'start',
          labels: {
            color: '#e2e8f0',
            usePointStyle: true,
            pointStyleWidth: 16,
            boxHeight: 10,
            padding: 14,
            font: { size: 13, family: 'Inter, system-ui', weight: '600' },
            filter: (item) => !!item.text,
          },
        },
        tooltip: {
          titleFont: { size: 13, weight: '600' },
          bodyFont: { size: 12 },
          padding: 10,
          callbacks: {
            label: (ctx) => {
              const r = ctx.raw || {};
              if (!r.param) {
                return `${ctx.dataset.label}: y=${ctx.parsed.y.toFixed(1)}`;
              }
              return `${ctx.dataset.label} ${r.param} | ${r.date} | Mittel: ${ctx.parsed.x.toFixed(1)}, Range: ${ctx.parsed.y.toFixed(1)} (n=${r.count})`;
            },
          },
        },
      },
      scales: {
        x: {
          type: 'linear',
          min: xBounds.min,
          max: xBounds.max,
          title: {
            display: true,
            text: 'Tagesmittelwert (mmHg)',
            color: '#e2e8f0',
            font: { size: 14, family: 'Inter, system-ui', weight: '600' },
            padding: { top: 10, bottom: 0 },
          },
          grid: { color: 'rgba(255,255,255,0.10)', lineWidth: 1 },
          ticks: {
            color: '#cbd5e1',
            font: { size: 12, family: 'Inter, system-ui', weight: '500' },
            padding: 6,
            maxRotation: 0,
            autoSkipPadding: 18,
          },
          border: { color: 'rgba(255,255,255,0.18)' },
        },
        y: {
          beginAtZero: true,
          title: {
            display: true,
            text: 'Tagesvariabilität (max - min, mmHg)',
            color: '#e2e8f0',
            font: { size: 14, family: 'Inter, system-ui', weight: '600' },
            padding: { top: 0, bottom: 10 },
          },
          grid: { color: 'rgba(255,255,255,0.10)', lineWidth: 1 },
          ticks: {
            color: '#cbd5e1',
            font: { size: 12, family: 'Inter, system-ui', weight: '500' },
            padding: 6,
          },
          border: { color: 'rgba(255,255,255,0.18)' },
        },
      },
    },
  });
}

function renderAnalysisViews() {
  if (!document.getElementById('analysisSection')) return;
  renderUnifiedView();
  renderBlandAltmanChart();
  renderAnalysisMeta();
}

function renderAnalysisMeta() {
  const dataSourceEl = document.getElementById('analysisDataSource');
  const noteEl = document.getElementById('analysisClassifierNote');
  if (dataSourceEl) {
    const reportCount = _reportsList?.reports?.length || 0;
    const measurementCount = _unifiedPayload?.measurements?.length
      || _lastServerPayload?.measurements?.length
      || 0;
    if (reportCount === 0) {
      dataSourceEl.textContent = 'Datenquelle: -';
    } else if (reportCount === 1) {
      dataSourceEl.textContent = `Datenquelle: 1 Bericht - ${measurementCount} Messungen`;
    } else {
      dataSourceEl.textContent = `Datenquelle: ${reportCount} Berichte zusammengeführt - ${measurementCount} Messungen`;
    }
  }
  if (noteEl) {
    const method = _unifiedPayload?.classification_method;
    noteEl.textContent = method
      ? `Tag/Nacht-Schätzung: ${classifierMethodLabel(method)}`
      : '';
  }
}

// --- ML training panel ----------------------------------------------

function _setMlBadge(label, state) {
  const badge = document.getElementById('mlStatusBadge');
  if (!badge) return;
  badge.textContent = label;
  badge.dataset.state = state;
}

function _renderMlInfo() {
  const info = document.getElementById('mlPanelInfo');
  if (!info) return;
  const lines = [];

  if (_mlState.status === 'running') {
    const sources = (_mlState.sources || []).join(', ') || '-';
    lines.push(`Training läuft (${sources}).`);
  } else if (_mlState.status === 'failed' && _mlState.last_error) {
    lines.push(`Letzter Fehler: <strong>${_mlState.last_error}</strong>`);
  }

  if (_mlState.last_result) {
    const r = _mlState.last_result;
    const valLoss = r.best_val_loss == null ? '-' : Number(r.best_val_loss).toFixed(4);
    lines.push(
      `Letzter Lauf: <strong>${r.sample_count}</strong> Samples (davon <strong>${r.positive_count}</strong> Nacht), `
      + `${r.epochs_run} Epochen, val_loss ${valLoss}.`
    );
  }

  if (_mlModelInfo && _mlModelInfo.available) {
    const created = _mlModelInfo.created_at
      ? new Date(_mlModelInfo.created_at).toLocaleString('de-DE')
      : 'unbekannt';
    const features = (_mlModelInfo.feature_names || []).length;
    lines.push(`Aktives Modell: trainiert am ${created}, ${features} Features.`);
  } else if (!_mlState.model_available && _mlState.status !== 'running') {
    lines.push('Es wird die regelbasierte Heuristik genutzt, bis ein Modell trainiert wurde.');
  }

  info.innerHTML = lines.length ? lines.join('<br/>') : '';
}

function _renderMlSleepInfo() {
  const el = document.getElementById('mlSleepInfo');
  if (!el) return;
  const episodes = _unifiedPayload?.sleep_episodes || [];
  const expected = _unifiedPayload?.expected_sleep_hours;
  if (!episodes.length && !expected) {
    el.hidden = true;
    el.innerHTML = '';
    return;
  }
  el.hidden = false;

  const parts = [];
  if (expected && Array.isArray(expected) && expected.length === 2) {
    parts.push(
      `Erwartete nächtliche Schlafdauer (Altersgruppe): <strong>${expected[0]}-${expected[1]} h</strong> `
      + `(NSF / Hirshkowitz et al. 2015).`
    );
  }
  if (episodes.length) {
    const previews = episodes.slice(-3).map((ep) => {
      const onset = new Date(ep.sleep_onset_estimate).toLocaleString('de-DE', {
        day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
      });
      const wake = new Date(ep.wake_estimate).toLocaleString('de-DE', {
        day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
      });
      return `${onset} - ${wake} (Konfidenz ${(ep.confidence * 100).toFixed(0)}%)`;
    });
    parts.push(
      `Erkannte Schlafepisoden (letzte ${previews.length} von ${episodes.length}): `
      + previews.join('; ')
    );
  }
  el.innerHTML = parts.join('<br/>');
}

function renderMlPanel() {
  const status = _mlState.status || 'idle';

  if (status === 'running') {
    _setMlBadge(ML_STATUS_LABELS.running, 'running');
  } else if (status === 'failed') {
    _setMlBadge(ML_STATUS_LABELS.failed, 'failed');
  } else if (_mlState.model_available) {
    _setMlBadge('Modell aktiv', 'ready');
  } else {
    _setMlBadge('Heuristik (kein Modell trainiert)', 'idle');
  }

  const trainBtn = document.getElementById('mlTrainBtn');
  if (trainBtn) {
    trainBtn.disabled = status === 'running';
    trainBtn.textContent = status === 'running'
      ? 'Training läuft...'
      : (_mlState.model_available ? 'Modell neu trainieren' : 'Modell trainieren');
  }

  _renderMlInfo();
  _renderMlSleepInfo();
}

function _stopMlPolling() {
  if (_mlState.pollTimer != null) {
    clearTimeout(_mlState.pollTimer);
    _mlState.pollTimer = null;
  }
}

async function refreshMlStatus({ silent = true } = {}) {
  try {
    const status = await apiRequest('/api/ml/training/status');
    const wasRunning = _mlState.status === 'running';
    Object.assign(_mlState, {
      status: status.status || 'idle',
      started_at: status.started_at || null,
      finished_at: status.finished_at || null,
      last_error: status.last_error || null,
      last_result: status.last_result || null,
      sources: status.sources || [],
      model_available: Boolean(status.model_available),
    });
    if (status.model_available) {
      try {
        _mlModelInfo = await apiRequest('/api/ml/model-info');
      } catch {
        _mlModelInfo = null;
      }
    } else {
      _mlModelInfo = null;
    }
    renderMlPanel();

    if (wasRunning && _mlState.status !== 'running') {
      // Training just finished: refresh classification, sleep episodes and
      // monthly summary so the day/night counts reflect the new model.
      try {
        if (typeof tryRenderImportedDashboard === 'function') {
          await tryRenderImportedDashboard();
        } else {
          await refreshMultiReportData();
        }
        renderAnalysisMeta();
        renderUnifiedView();
      } catch {
        /* non-fatal */
      }
      if (_mlState.status === 'finished') {
        showToast('Training abgeschlossen - neue Klassifikation aktiv.');
      } else if (_mlState.status === 'failed') {
        showToast(_mlState.last_error || 'Training fehlgeschlagen.', 'error');
      }
    }

    if (_mlState.status === 'running') {
      _stopMlPolling();
      _mlState.pollTimer = setTimeout(() => refreshMlStatus({ silent: true }), 1500);
    }
  } catch (error) {
    if (!silent) showToast(error.message || 'ML-Status konnte nicht geladen werden.', 'error');
  }
}

async function startMlTraining() {
  const useCsv = document.getElementById('mlUseCsv')?.checked !== false;
  const useRepo = Boolean(document.getElementById('mlUseRepo')?.checked);
  if (!useCsv && !useRepo) {
    showToast('Bitte mindestens eine Datenquelle auswählen.', 'error');
    return;
  }
  try {
    const status = await apiRequest('/api/ml/training/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ use_csv_files: useCsv, use_repository: useRepo }),
    });
    Object.assign(_mlState, {
      status: status.status || 'running',
      sources: status.sources || [],
      last_error: status.last_error || null,
      last_result: status.last_result || _mlState.last_result,
      model_available: Boolean(status.model_available),
    });
    renderMlPanel();
    showToast('Training gestartet. Status wird aktualisiert.');
    _stopMlPolling();
    _mlState.pollTimer = setTimeout(() => refreshMlStatus({ silent: true }), 800);
  } catch (error) {
    showToast(error.message || 'Training konnte nicht gestartet werden.', 'error');
  }
}

function initMlPanel() {
  if (_mlState.initialized) return;
  if (!document.getElementById('mlPanel')) return;
  _mlState.initialized = true;
  document.getElementById('mlTrainBtn')?.addEventListener('click', startMlTraining);
  refreshMlStatus({ silent: true });
}

// --- multi-report data + UI -----------------------------------------

async function fetchReportsList() {
  try {
    const data = await apiRequest('/api/reports');
    _reportsList = data || { reports: [], current_report_id: null };
  } catch {
    _reportsList = { reports: [], current_report_id: null };
  }
  return _reportsList;
}

async function fetchUnifiedMeasurements() {
  try {
    _unifiedPayload = await apiRequest('/api/dashboard/unified-measurements');
    _serverMeasurementsCache = { sourceRef: null, value: [] };
    _filteredMeasurementsCache = { key: null, value: [] };
  } catch {
    _unifiedPayload = null;
    _serverMeasurementsCache = { sourceRef: null, value: [] };
    _filteredMeasurementsCache = { key: null, value: [] };
  }
  return _unifiedPayload;
}

async function refreshMultiReportData() {
  await Promise.all([fetchReportsList(), fetchUnifiedMeasurements()]);
}

function renderReportsBar() {
  const bar = document.getElementById('reportsBar');
  const chips = document.getElementById('reportsBarChips');
  if (!bar || !chips) return;

  const reports = _reportsList?.reports || [];
  if (reports.length === 0) {
    bar.hidden = true;
    chips.innerHTML = '';
    return;
  }
  bar.hidden = false;

  chips.innerHTML = reports.map((r) => {
    const label = reportLabel(r);
    const meta = `${r.measurement_count} Messung${r.measurement_count === 1 ? '' : 'en'}`;
    const cls = r.is_current ? 'report-chip active' : 'report-chip';
    const source = r.file_name ? ` - ${r.file_name}` : '';
    return `<span class="${cls}" data-report-id="${r.id}">
      <span class="report-chip-label">${label}</span>
      <span class="report-chip-meta">${meta}${source}</span>
      <button type="button" class="report-chip-del" data-action="delete" data-report-id="${r.id}" title="Bericht löschen" aria-label="Bericht löschen">x</button>
    </span>`;
  }).join('');

  chips.querySelectorAll('.report-chip').forEach((el) => {
    el.addEventListener('click', (event) => {
      const target = event.target;
      if (target.closest('.report-chip-del')) return;
      const id = el.dataset.reportId;
      if (id) activateReport(id);
    });
  });
  chips.querySelectorAll('.report-chip-del').forEach((btn) => {
    btn.addEventListener('click', (event) => {
      event.stopPropagation();
      const id = btn.dataset.reportId;
      if (id) deleteReport(id);
    });
  });
}

async function activateReport(reportId) {
  if (!reportId) return;
  try {
    const payload = await apiRequest(`/api/reports/${encodeURIComponent(reportId)}/activate`, { method: 'POST' });
    _lastServerPayload = payload;
    await refreshMultiReportData();
    renderDashboardView(mapImportedPayload(payload));
    renderReportsBar();
    showToast('Bericht aktiviert.');
  } catch (error) {
    showToast(error.message || 'Aktivierung fehlgeschlagen.', 'error');
  }
}

async function deleteReport(reportId) {
  if (!reportId) return;
  const report = (_reportsList?.reports || []).find((r) => r.id === reportId);
  const label = reportLabel(report);
  if (!confirm(`Bericht "${label}" inklusive aller Messungen unwiderruflich löschen?`)) return;
  try {
    const listing = await apiRequest(`/api/reports/${encodeURIComponent(reportId)}`, { method: 'DELETE' });
    _reportsList = listing;

    // After the deletion we need to bring the dashboard in sync with the new
    // "current" report (if any). A successful response includes the updated
    // listing, so we only need to re-fetch the active report payload.
    if (listing.current_report_id) {
      try {
        _lastServerPayload = await apiRequest('/api/dashboard/monthly-summary');
      } catch {
        _lastServerPayload = null;
      }
    } else {
      _lastServerPayload = null;
    }
    await fetchUnifiedMeasurements();

    const viewModel = _lastServerPayload
      ? mapImportedPayload(_lastServerPayload)
      : buildManualViewModel();
    renderDashboardView(viewModel);
    renderReportsBar();
    renderConfiguratorReportsCard();
    if (document.getElementById('configurator-root')) {
      renderConfiguratorTable(_currentFilter);
    }
    showToast('Bericht gelöscht.');
  } catch (error) {
    showToast(error.message || 'Löschen fehlgeschlagen.', 'error');
  }
}

function renderConfiguratorReportsCard() {
  const card = document.getElementById('reportsManageCard');
  const list = document.getElementById('reportsManageList');
  if (!card || !list) return;

  const reports = _reportsList?.reports || [];
  if (reports.length === 0) {
    card.hidden = true;
    list.innerHTML = '';
    return;
  }
  card.hidden = false;

  list.innerHTML = reports.map((r) => {
    const label = reportLabel(r);
    const cls = r.is_current ? 'reports-manage-row current' : 'reports-manage-row';
    const file = r.file_name ? ` - ${r.file_name}` : '';
    const current = r.is_current ? '<span class="status-badge status-original">Aktiv</span>' : '';
    return `<div class="${cls}" data-report-id="${r.id}">
      <div>
        <div class="label">${label} ${current}</div>
        <div class="meta">${r.measurement_count} Messungen${file}</div>
      </div>
      <div class="actions">
        ${r.is_current ? '' : `<button class="btn btn-ghost" type="button" data-action="activate" data-report-id="${r.id}">Aktivieren</button>`}
        <button class="btn btn-danger" type="button" data-action="delete" data-report-id="${r.id}">Löschen</button>
      </div>
    </div>`;
  }).join('');

  list.querySelectorAll('[data-action="delete"]').forEach((btn) => {
    btn.addEventListener('click', () => deleteReport(btn.dataset.reportId));
  });
  list.querySelectorAll('[data-action="activate"]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      await activateReport(btn.dataset.reportId);
      renderConfiguratorReportsCard();
      renderConfiguratorTable(_currentFilter);
    });
  });
}

// --- analysis controls wiring ---------------------------------------

function setDayNightFilter(value) {
  const allowed = new Set(['all', 'day', 'night']);
  if (!allowed.has(value)) return;
  _analysisState.dayNight = value;
  document.querySelectorAll('#daynightFilter button').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.daynight === value);
  });
  renderUnifiedView();
  renderBlandAltmanChart();
}

function setTimeWindow({ start = null, end = null }) {
  _analysisState.timeWindow = { start: start || null, end: end || null };
  const startInput = document.getElementById('twStart');
  const endInput = document.getElementById('twEnd');
  if (startInput) startInput.value = start || '';
  if (endInput) endInput.value = end || '';
  renderUnifiedView();
  renderBlandAltmanChart();
}

function applyTimeWindowPreset(days) {
  const measurements = getServerMeasurements();
  if (measurements.length === 0) return;
  const maxMs = new Date(measurements[measurements.length - 1].datetime).getTime();
  const end = new Date(maxMs);
  const endIso = [end.getFullYear(), String(end.getMonth() + 1).padStart(2, '0'), String(end.getDate()).padStart(2, '0')].join('-');
  let startIso;
  if (days <= 1) {
    startIso = endIso;
  } else {
    const start = new Date(end.getFullYear(), end.getMonth(), end.getDate() - (days - 1));
    startIso = [start.getFullYear(), String(start.getMonth() + 1).padStart(2, '0'), String(start.getDate()).padStart(2, '0')].join('-');
  }
  setTimeWindow({ start: startIso, end: endIso });
}

function switchAnalysisTab(tab) {
  _analysisState.activeTab = tab;
  document.querySelectorAll('.analysis-tab').forEach((btn) => {
    const active = btn.dataset.analysisTab === tab;
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  const unified = document.getElementById('panel-unified');
  const bland = document.getElementById('panel-bland-altman');
  if (unified) unified.style.display = tab === 'unified' ? 'flex' : 'none';
  if (bland) bland.style.display = tab === 'bland-altman' ? 'flex' : 'none';

  if (tab === 'unified' && _charts['unified-chart']) {
    _charts['unified-chart'].resize();
    updateZoomStatus();
  }
  if (tab === 'bland-altman' && _charts['bland-chart']) {
    _charts['bland-chart'].resize();
  }
}

function initAnalysisControls() {
  if (_analysisState.initialized) return;
  if (!document.getElementById('analysisSection')) return;
  _analysisState.initialized = true;

  document.querySelectorAll('.analysis-tab').forEach((btn) => {
    btn.addEventListener('click', () => switchAnalysisTab(btn.dataset.analysisTab));
  });

  for (const [paramId, meta] of Object.entries(PARAM_META)) {
    const el = document.getElementById(meta.filterId);
    if (!el) continue;
    el.checked = _analysisState.filters[paramId];
    el.addEventListener('change', () => {
      _analysisState.filters[paramId] = el.checked;
      renderUnifiedView();
    });
  }

  document.getElementById('zoom-in')?.addEventListener('click', () => zoomUnifiedBy(1.25));
  document.getElementById('zoom-out')?.addEventListener('click', () => zoomUnifiedBy(0.8));
  document.getElementById('zoom-reset')?.addEventListener('click', () => resetUnifiedZoom());
  document.getElementById('zoomSlider')?.addEventListener('input', (e) => {
    applyZoomLevel(e.target.value);
  });

  document.querySelectorAll('#daynightFilter button').forEach((btn) => {
    btn.addEventListener('click', () => setDayNightFilter(btn.dataset.daynight));
  });

  document.getElementById('twStart')?.addEventListener('change', (e) => {
    _analysisState.timeWindow.start = e.target.value || null;
    renderUnifiedView();
    renderBlandAltmanChart();
  });
  document.getElementById('twEnd')?.addEventListener('change', (e) => {
    _analysisState.timeWindow.end = e.target.value || null;
    renderUnifiedView();
    renderBlandAltmanChart();
  });
  document.getElementById('twClear')?.addEventListener('click', () => setTimeWindow({ start: null, end: null }));
  document.querySelectorAll('.timewindow-presets .btn').forEach((btn) => {
    btn.addEventListener('click', () => applyTimeWindowPreset(Number(btn.dataset.preset)));
  });

  initMlPanel();
}

// --- dashboard import flow ------------------------------------------

async function loadImportedDashboard() {
  const payload = await apiRequest('/api/dashboard/monthly-summary');
  _lastServerPayload = payload;
  return payload;
}

async function tryRenderImportedDashboard() {
  try {
    const payload = await loadImportedDashboard();
    await refreshMultiReportData();
    renderDashboardView(mapImportedPayload(payload));
    renderReportsBar();
    setImportStatus('Importierte Monatsdaten geladen.');
    return true;
  } catch {
    // No report yet — still refresh the reports bar so a freshly-imported
    // report becomes visible even if the single-report fetch failed.
    await refreshMultiReportData();
    renderReportsBar();
    return false;
  }
}

function setImportStatus(message, type = 'info') {
  const el = document.getElementById('importStatus');
  if (!el) return;
  el.textContent = message;
  el.style.color = type === 'error' ? '#fca5a5' : type === 'success' ? '#86efac' : '';
}

function updateFilePickerLabel(file) {
  const textEl = document.querySelector('#pdfFileLabel .file-picker-text');
  if (!textEl) return;
  textEl.textContent = file ? file.name : 'PDF auswählen';
  const label = document.getElementById('pdfFileLabel');
  if (label) label.classList.toggle('has-file', Boolean(file));
}

async function handlePdfImport(event) {
  event.preventDefault();
  const fileInput = document.getElementById('pdfFile');
  const file = fileInput?.files?.[0];

  if (!file) {
    setImportStatus('Bitte zuerst eine PDF-Datei auswählen.', 'error');
    showToast('Bitte eine PDF-Datei auswählen.', 'error');
    return;
  }

  const formData = new FormData();
  formData.append('file', file);
  setImportStatus('PDF wird importiert...');

  try {
    const payload = await apiRequest('/api/imports/hilo', {
      method: 'POST',
      body: formData,
    });
    _lastServerPayload = payload;
    await refreshMultiReportData();
    renderDashboardView(mapImportedPayload(payload));
    renderReportsBar();
    setImportStatus(`Import erfolgreich: ${payload.summary?.measurement_count_total ?? 0} Messungen.`, 'success');
    showToast('PDF erfolgreich importiert.');
  } catch (error) {
    setImportStatus(error.message || 'Import fehlgeschlagen.', 'error');
    showToast(error.message || 'Import fehlgeschlagen.', 'error');
  }
}

function initDashboard() {
  document.getElementById('pdfImportForm')?.addEventListener('submit', handlePdfImport);
  document.getElementById('pdfFile')?.addEventListener('change', (e) => {
    updateFilePickerLabel(e.target.files?.[0] || null);
  });
  initAnalysisControls();
  renderDashboardView(buildManualViewModel());
  tryRenderImportedDashboard();
}

// --- configurator ---------------------------------------------------

function columnForMeasurement(measurement) {
  return IMPORT_TYPE_TO_COLUMN[measurement.measurement_type] || 'cuffless';
}

function buildConfiguratorRows(importedPayload, localData) {
  const rows = [];

  for (const m of importedPayload?.measurements || []) {
    const type = columnForMeasurement(m);
    rows.push({
      entry_id: m.entry_id,
      scope: 'server',
      type,
      datetime: m.datetime,
      sys: m.systolic,
      dia: m.diastolic,
      pulse: m.heart_rate,
      measurement_type: m.measurement_type,
      entry_status: m.entry_status,
      source: m.source,
      source_file: m.source_file,
    });
  }

  for (const type of Object.keys(TYPES)) {
    for (const entry of localData[type] || []) {
      rows.push({
        entry_id: entry.id,
        scope: 'local',
        type,
        datetime: entry.datetime,
        sys: entry.sys,
        dia: entry.dia,
        pulse: entry.pulse,
        measurement_type: COLUMN_TO_IMPORT_TYPE[type],
        entry_status: 'manual',
        source: 'manual_local',
        source_file: null,
      });
    }
  }

  return rows;
}

function renderConfiguratorTable(filter) {
  _currentFilter = filter;
  const tbody = document.getElementById('allDataBody');
  const empty = document.getElementById('tableEmpty');
  if (!tbody) return;

  const localData = getLocalData();
  let rows = buildConfiguratorRows(_lastServerPayload, localData);

  if (filter !== 'all') rows = rows.filter((row) => row.type === filter);
  rows.sort((a, b) => new Date(b.datetime) - new Date(a.datetime));

  if (rows.length === 0) {
    tbody.innerHTML = '';
    if (empty) empty.style.display = 'block';
    return;
  }
  if (empty) empty.style.display = 'none';

  tbody.innerHTML = rows.map((row) => configuratorRowHtml(row)).join('');
}

function configuratorRowHtml(row) {
  const dt = fmtDatetime(row.datetime);
  const cat = bpCategory(row.sys, row.dia);
  const badge = statusBadge(row.entry_status);
  const source = row.source_file ? `<div class="row-source">${row.source_file}</div>` : '';
  const editButton = row.scope === 'server'
    ? `<button class="btn btn-ghost" onclick="handleEdit('${row.entry_id}')" type="button">Bearbeiten</button>`
    : '';
  const deleteAction = row.scope === 'server'
    ? `handleDeleteServer('${row.entry_id}')`
    : `handleDeleteLocal('${row.type}','${row.entry_id}')`;
  return `<tr>
    <td>
      <span class="type-badge ${row.type}">${TYPES[row.type].short}</span>
      ${badge}
      ${source}
    </td>
    <td><div class="bp-display"><span class="sys">${row.sys}</span><span class="sep">/</span><span class="dia">${row.dia}</span><span class="unit">mmHg</span></div></td>
    <td><div class="pulse-display">HR ${row.pulse} bpm</div></td>
    <td><span class="bp-cat ${cat.cls}">${cat.label}</span></td>
    <td>${dt.date}</td>
    <td>${dt.time}</td>
    <td class="row-actions">
      ${editButton}
      <button class="btn btn-danger" onclick="${deleteAction}" type="button">Löschen</button>
    </td>
  </tr>`;
}

function findServerEntry(entryId) {
  const measurements = _lastServerPayload?.measurements || [];
  return measurements.find((m) => m.entry_id === entryId) || null;
}

function openEditDialog(entry) {
  const dialog = document.getElementById('editDialog');
  if (!dialog) return;
  dialog.classList.add('open');
  document.getElementById('editEntryId').value = entry.entry_id;
  document.getElementById('editType').value = entry.measurement_type;
  const localIso = new Date(new Date(entry.datetime).getTime() - new Date().getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
  document.getElementById('editDatetime').value = localIso;
  document.getElementById('editSys').value = entry.systolic;
  document.getElementById('editDia').value = entry.diastolic;
  document.getElementById('editPulse').value = entry.heart_rate;

  const statusInfo = document.getElementById('editStatusInfo');
  if (statusInfo) {
    const badge = statusBadge(entry.entry_status);
    const original = entry.original_values;
    const originalLine = original
      ? `Original: ${original.systolic}/${original.diastolic}, Puls ${original.heart_rate}`
      : 'Keine Originalwerte hinterlegt';
    statusInfo.innerHTML = `${badge} <span class="status-hint">${originalLine}</span>`;
  }
}

function closeEditDialog() {
  document.getElementById('editDialog')?.classList.remove('open');
}

window.handleEdit = function(entryId) {
  const entry = findServerEntry(entryId);
  if (!entry) {
    showToast('Eintrag nicht gefunden.', 'error');
    return;
  }
  openEditDialog(entry);
};

window.handleDeleteLocal = function(type, id) {
  if (!confirm('Eintrag wirklich löschen?')) return;
  deleteLocalEntry(type, id);
  renderConfiguratorTable(_currentFilter);
  showToast('Lokaler Eintrag gelöscht');
};

window.handleDeleteServer = async function(entryId) {
  if (!confirm('Importierten Eintrag wirklich löschen?')) return;
  try {
    const payload = await apiRequest(`/api/measurements/${entryId}`, { method: 'DELETE' });
    _lastServerPayload = payload;
    renderConfiguratorTable(_currentFilter);
    showToast('Eintrag gelöscht.');
  } catch (error) {
    showToast(error.message || 'Löschen fehlgeschlagen.', 'error');
  }
};

async function submitEditDialog(event) {
  event.preventDefault();
  const entryId = document.getElementById('editEntryId').value;
  const body = {
    datetime: localIsoFromInput(document.getElementById('editDatetime').value),
    systolic: parseInt(document.getElementById('editSys').value, 10),
    diastolic: parseInt(document.getElementById('editDia').value, 10),
    heart_rate: parseInt(document.getElementById('editPulse').value, 10),
    measurement_type: document.getElementById('editType').value,
  };

  if (!entryId || Number.isNaN(body.systolic) || Number.isNaN(body.diastolic) || Number.isNaN(body.heart_rate)) {
    showToast('Bitte alle Werte angeben.', 'error');
    return;
  }

  try {
    const payload = await apiRequest(`/api/measurements/${entryId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    _lastServerPayload = payload;
    closeEditDialog();
    renderConfiguratorTable(_currentFilter);
    showToast('Eintrag aktualisiert.');
  } catch (error) {
    showToast(error.message || 'Aktualisierung fehlgeschlagen.', 'error');
  }
}

function updatePreview() {
  const sys = document.getElementById('sys');
  const dia = document.getElementById('dia');
  const pulse = document.getElementById('pulse');
  const prev = document.getElementById('bp-preview');
  if (!sys || !dia || !pulse || !prev) return;

  const s = parseInt(sys.value, 10);
  const d = parseInt(dia.value, 10);
  const p = parseInt(pulse.value, 10);

  if (Number.isNaN(s) || Number.isNaN(d)) {
    prev.innerHTML = '<span style="color:var(--text-dim)">Werte eingeben...</span>';
    return;
  }

  const cat = bpCategory(s, d);
  const pulseStr = !Number.isNaN(p) ? `<span class="preview-pulse">HR ${p} bpm</span>` : '';
  prev.innerHTML = `
    <div class="preview-vals">
      <span class="sys">${s}</span><span class="sep">/</span><span class="dia">${d}</span>
    </div>
    <span style="color:var(--text-dim);font-size:0.78rem">mmHg</span>
    ${pulseStr}
    <span class="bp-cat ${cat.cls}" style="margin-left:auto">${cat.label}</span>`;
}

async function handleConfiguratorSubmit(e) {
  e.preventDefault();
  const type = document.getElementById('type').value;
  const sysStr = document.getElementById('sys').value.trim();
  const diaStr = document.getElementById('dia').value.trim();
  const pulseStr = document.getElementById('pulse').value.trim();
  const dtVal = document.getElementById('datetime').value;
  const target = document.querySelector('input[name="saveTarget"]:checked')?.value || 'local';

  if (!type || !sysStr || !diaStr || !pulseStr || !dtVal) {
    showToast('Bitte alle Felder ausfüllen.', 'error');
    return;
  }
  const sys = parseInt(sysStr, 10);
  const dia = parseInt(diaStr, 10);
  const pulse = parseInt(pulseStr, 10);
  if (sys < 60 || sys > 260) return showToast('SYS-Wert scheint unrealistisch (60-260).', 'error');
  if (dia < 40 || dia > 160) return showToast('DIA-Wert scheint unrealistisch (40-160).', 'error');
  if (pulse < 30 || pulse > 250) return showToast('Puls-Wert scheint unrealistisch (30-250).', 'error');

  const isoDatetime = localIsoFromInput(dtVal);

  if (target === 'server') {
    try {
      const payload = await apiRequest('/api/measurements', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          datetime: isoDatetime,
          systolic: sys,
          diastolic: dia,
          heart_rate: pulse,
          measurement_type: COLUMN_TO_IMPORT_TYPE[type],
        }),
      });
      _lastServerPayload = payload;
      showToast('Messung im Bericht gespeichert.');
    } catch (error) {
      showToast(error.message || 'Speichern fehlgeschlagen.', 'error');
      return;
    }
  } else {
    addLocalEntry(type, sys, dia, pulse, isoDatetime);
    showToast('Lokale Messung gespeichert.');
  }

  document.getElementById('sys').value = '';
  document.getElementById('dia').value = '';
  document.getElementById('pulse').value = '';
  const now = new Date();
  document.getElementById('datetime').value = new Date(now - now.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  updatePreview();
  renderConfiguratorTable(_currentFilter);
}

async function refreshConfiguratorServerData() {
  try {
    const payload = await apiRequest('/api/dashboard/monthly-summary');
    _lastServerPayload = payload;
  } catch {
    _lastServerPayload = null;
  }
  await fetchReportsList();
  renderConfiguratorReportsCard();
  renderConfiguratorTable(_currentFilter);
}

function initConfigurator() {
  const dtInput = document.getElementById('datetime');
  if (dtInput) {
    const now = new Date();
    dtInput.value = new Date(now - now.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  }

  ['sys', 'dia', 'pulse'].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('input', updatePreview);
  });

  document.getElementById('entryForm')?.addEventListener('submit', handleConfiguratorSubmit);

  document.querySelectorAll('.filter-tab').forEach((tab) => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.filter-tab').forEach((item) => item.classList.remove('active'));
      tab.classList.add('active');
      renderConfiguratorTable(tab.dataset.filter);
    });
  });

  document.getElementById('editDialogForm')?.addEventListener('submit', submitEditDialog);
  document.getElementById('editDialogCancel')?.addEventListener('click', (e) => {
    e.preventDefault();
    closeEditDialog();
  });

  refreshConfiguratorServerData();
}

document.addEventListener('DOMContentLoaded', () => {
  if (document.getElementById('dashboard-root')) initDashboard();
  if (document.getElementById('configurator-root')) initConfigurator();
});
