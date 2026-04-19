/* ─────────────────────────────────────────────────────────────────────────────
   BlutdruckMonitor — app.js
   Datenverwaltung · Diagramme · Dashboard · Konfigurator
───────────────────────────────────────────────────────────────────────────── */

'use strict';

// ── Konfiguration ─────────────────────────────────────────────────────────────

const STORAGE_KEY = 'bpmonitor_v1';

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

// ── Datenzugriff ──────────────────────────────────────────────────────────────

function getData() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { cuffless: [], phone: [], cuff: [] };
    return JSON.parse(raw);
  } catch {
    return { cuffless: [], phone: [], cuff: [] };
  }
}

function saveData(data) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
}

function addEntry(type, sys, dia, pulse, datetime) {
  const data = getData();
  if (!data[type]) data[type] = [];
  const entry = {
    id: `${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
    datetime: datetime || new Date().toISOString(),
    sys:   parseInt(sys,   10),
    dia:   parseInt(dia,   10),
    pulse: parseInt(pulse, 10),
  };
  data[type].push(entry);
  // chronologisch sortieren
  data[type].sort((a, b) => new Date(a.datetime) - new Date(b.datetime));
  saveData(data);
  return entry;
}

function deleteEntry(type, id) {
  const data = getData();
  if (!data[type]) return;
  data[type] = data[type].filter(e => e.id !== id);
  saveData(data);
}

// ── Hilfsfunktionen ───────────────────────────────────────────────────────────

function fmtDatetime(iso) {
  const d = new Date(iso);
  const date = d.toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit', year: 'numeric' });
  const time = d.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
  return { date, time, full: `${date} ${time}` };
}

function bpCategory(sys, dia) {
  if (sys < 120 && dia < 80) return { label: 'Optimal',  cls: 'optimal'  };
  if (sys < 130 && dia < 85) return { label: 'Normal',   cls: 'normal'   };
  if (sys < 140 && dia < 90) return { label: 'Erhöht',   cls: 'elevated' };
  return                            { label: 'Hoch',     cls: 'high'     };
}

function showToast(msg, type = 'success') {
  let el = document.getElementById('toast');
  if (!el) { el = document.createElement('div'); el.id = 'toast'; document.body.appendChild(el); }
  el.textContent = msg;
  el.style.background = type === 'error' ? '#ef4444' : '#10b981';
  el.classList.add('show');
  clearTimeout(el._timeout);
  el._timeout = setTimeout(() => el.classList.remove('show'), 2800);
}

// ── Diagramm ──────────────────────────────────────────────────────────────────

const _charts = {};

function createBPChart(canvasId, entries) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;

  if (_charts[canvasId]) { _charts[canvasId].destroy(); delete _charts[canvasId]; }

  const wrap  = canvas.parentElement;
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
    data: entries.map(e => ({ x: new Date(e.datetime), y: e[key] })),
    borderColor: color,
    backgroundColor: color + '18',
    pointBackgroundColor: color,
    pointBorderColor: '#0f172a',
    pointBorderWidth: 1.5,
    pointRadius: entries.length < 30 ? 5 : 3,
    pointHoverRadius: 7,
    tension: 0.35,
    fill: false,
    borderWidth: 2,
  });

  _charts[canvasId] = new Chart(canvas.getContext('2d'), {
    type: 'line',
    data: {
      datasets: [
        mkDataset('SYS',  'sys',   '#ef4444'),
        mkDataset('DIA',  'dia',   '#f59e0b'),
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
        tooltip: {
          backgroundColor: 'rgba(15,23,42,0.97)',
          borderColor: 'rgba(255,255,255,0.1)',
          borderWidth: 1,
          titleColor: '#f1f5f9',
          bodyColor: '#94a3b8',
          padding: 12,
          titleFont: { size: 12, weight: '600' },
          bodyFont:  { size: 12 },
          callbacks: {
            title: items => {
              const d = new Date(items[0].parsed.x);
              return d.toLocaleString('de-DE', {
                day: '2-digit', month: '2-digit', year: 'numeric',
                hour: '2-digit', minute: '2-digit',
              });
            },
            label: item => ` ${item.dataset.label}: ${item.parsed.y}${item.dataset.label === 'Puls' ? ' bpm' : ' mmHg'}`,
          },
        },
      },
      scales: {
        x: {
          type: 'time',
          time: {
            displayFormats: {
              millisecond: 'HH:mm', second: 'HH:mm', minute: 'HH:mm',
              hour: 'dd.MM HH:mm', day: 'dd.MM', week: 'dd.MM',
              month: 'MM.yy', quarter: 'MM.yy', year: 'yyyy',
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
}

// ── Dashboard ─────────────────────────────────────────────────────────────────

function renderList(type, entries) {
  const list  = document.getElementById(`list-${type}`);
  const count = document.getElementById(`count-${type}`);
  if (!list) return;

  if (count) count.textContent = `${entries.length} ${entries.length === 1 ? 'Eintrag' : 'Einträge'}`;

  if (entries.length === 0) {
    list.innerHTML = `<div class="empty-state">Noch keine Messungen.<br>
      <a href="configurator.html" style="color:${TYPES[type].color}">Jetzt eintragen →</a></div>`;
    return;
  }

  const sorted = [...entries].reverse(); // neueste zuerst
  list.innerHTML = sorted.map(e => {
    const dt = fmtDatetime(e.datetime);
    return `<div class="data-entry">
      <div class="entry-values">
        <div class="entry-bp">
          <span class="sys">${e.sys}</span><span class="sep">/</span><span class="dia">${e.dia}</span>
        </div>
        <div class="entry-pulse">♥ ${e.pulse}</div>
      </div>
      <div class="entry-time">
        <div class="t-date">${dt.date}</div>
        <div class="t-time">${dt.time}</div>
      </div>
    </div>`;
  }).join('');
}

function updateStats(type, entries) {
  if (entries.length === 0) return;
  const last = entries[entries.length - 1];
  const set  = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
  set(`last-sys-${type}`,   last.sys);
  set(`last-dia-${type}`,   last.dia);
  set(`last-pulse-${type}`, last.pulse);
}

function initDashboard() {
  const data  = getData();
  const types = ['cuffless', 'phone', 'cuff'];

  types.forEach(type => {
    const entries = data[type] || [];
    updateStats(type, entries);
    renderList(type, entries);
    createBPChart(`chart-${type}`, entries);
  });

  // Letzte Messung globale Zeitangabe
  const allTs = types.flatMap(t => (data[t] || []).map(e => +new Date(e.datetime)));
  if (allTs.length > 0) {
    const el = document.getElementById('lastUpdate');
    if (el) el.textContent = `Letzte Messung: ${fmtDatetime(new Date(Math.max(...allTs)).toISOString()).full}`;
  }
}

// ── Konfigurator ──────────────────────────────────────────────────────────────

let _currentFilter = 'all';

function renderAllData(filter) {
  _currentFilter = filter;
  const data  = getData();
  const tbody = document.getElementById('allDataBody');
  const empty = document.getElementById('tableEmpty');
  if (!tbody) return;

  let rows = [];
  for (const type of ['cuffless', 'phone', 'cuff']) {
    for (const e of (data[type] || [])) rows.push({ ...e, type });
  }

  if (filter !== 'all') rows = rows.filter(r => r.type === filter);
  rows.sort((a, b) => new Date(b.datetime) - new Date(a.datetime));

  if (rows.length === 0) {
    tbody.innerHTML = '';
    if (empty) empty.style.display = 'block';
    return;
  }
  if (empty) empty.style.display = 'none';

  tbody.innerHTML = rows.map(r => {
    const dt  = fmtDatetime(r.datetime);
    const cat = bpCategory(r.sys, r.dia);
    return `<tr>
      <td><span class="type-badge ${r.type}">${TYPES[r.type].short}</span></td>
      <td><div class="bp-display">
        <span class="sys">${r.sys}</span><span class="sep">/</span><span class="dia">${r.dia}</span>
        <span class="unit">mmHg</span>
      </div></td>
      <td><div class="pulse-display">♥ ${r.pulse} bpm</div></td>
      <td><span class="bp-cat ${cat.cls}">${cat.label}</span></td>
      <td>${dt.date}</td>
      <td>${dt.time}</td>
      <td>
        <button class="btn btn-danger" onclick="handleDelete('${r.type}','${r.id}')">Löschen</button>
      </td>
    </tr>`;
  }).join('');
}

window.handleDelete = function(type, id) {
  if (!confirm('Eintrag wirklich löschen?')) return;
  deleteEntry(type, id);
  renderAllData(_currentFilter);
  showToast('Eintrag gelöscht');
};

function updatePreview() {
  const sys   = document.getElementById('sys');
  const dia   = document.getElementById('dia');
  const pulse = document.getElementById('pulse');
  const prev  = document.getElementById('bp-preview');
  if (!sys || !dia || !pulse || !prev) return;

  const s = parseInt(sys.value, 10);
  const d = parseInt(dia.value, 10);
  const p = parseInt(pulse.value, 10);

  if (isNaN(s) || isNaN(d)) {
    prev.innerHTML = '<span style="color:var(--text-dim)">Werte eingeben…</span>';
    return;
  }

  const cat = bpCategory(s, d);
  const pulseStr = (!isNaN(p)) ? `<span class="preview-pulse">♥ ${p} bpm</span>` : '';
  prev.innerHTML = `
    <div class="preview-vals">
      <span class="sys">${s}</span><span class="sep">/</span><span class="dia">${d}</span>
    </div>
    <span style="color:var(--text-dim);font-size:0.78rem">mmHg</span>
    ${pulseStr}
    <span class="bp-cat ${cat.cls}" style="margin-left:auto">${cat.label}</span>`;
}

function initConfigurator() {
  // Standard-Datum = jetzt
  const dtInput = document.getElementById('datetime');
  if (dtInput) {
    const now   = new Date();
    dtInput.value = new Date(now - now.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  }

  // Live-Vorschau
  ['sys', 'dia', 'pulse'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('input', updatePreview);
  });

  // Formular absenden
  const form = document.getElementById('entryForm');
  if (form) {
    form.addEventListener('submit', e => {
      e.preventDefault();
      const type  = document.getElementById('type').value;
      const sys   = document.getElementById('sys').value.trim();
      const dia   = document.getElementById('dia').value.trim();
      const pulse = document.getElementById('pulse').value.trim();
      const dtVal = document.getElementById('datetime').value;

      if (!type || !sys || !dia || !pulse || !dtVal) {
        showToast('Bitte alle Felder ausfüllen.', 'error'); return;
      }
      if (parseInt(sys) < 60 || parseInt(sys) > 260) {
        showToast('SYS-Wert scheint unrealistisch (60–260).', 'error'); return;
      }
      if (parseInt(dia) < 40 || parseInt(dia) > 160) {
        showToast('DIA-Wert scheint unrealistisch (40–160).', 'error'); return;
      }
      if (parseInt(pulse) < 30 || parseInt(pulse) > 250) {
        showToast('Puls-Wert scheint unrealistisch (30–250).', 'error'); return;
      }

      addEntry(type, sys, dia, pulse, new Date(dtVal).toISOString());
      showToast('Messung gespeichert!');

      // Felder zurücksetzen
      document.getElementById('sys').value   = '';
      document.getElementById('dia').value   = '';
      document.getElementById('pulse').value = '';
      const now2 = new Date();
      document.getElementById('datetime').value =
        new Date(now2 - now2.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
      updatePreview();
      renderAllData(_currentFilter);
    });
  }

  // Filter-Tabs
  document.querySelectorAll('.filter-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.filter-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      renderAllData(tab.dataset.filter);
    });
  });

  renderAllData('all');
}

// ── Seiten-Init ───────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  if (document.getElementById('dashboard-root'))    initDashboard();
  if (document.getElementById('configurator-root')) initConfigurator();
});
