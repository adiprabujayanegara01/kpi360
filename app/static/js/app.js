(function () {
  var meta = document.querySelector('meta[name="csrf-token"]');
  var CSRF = meta ? meta.content : '';
  var root = document.documentElement;

  // tema terang/gelap
  var saved = null; try { saved = localStorage.getItem('kpi360-theme'); } catch (e) {}
  root.setAttribute('data-bs-theme', saved || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));
  document.addEventListener('click', function (e) {
    var t = e.target.closest('[data-theme-toggle]');
    if (!t) return;
    var n = root.getAttribute('data-bs-theme') === 'dark' ? 'light' : 'dark';
    root.setAttribute('data-bs-theme', n);
    try { localStorage.setItem('kpi360-theme', n); } catch (e2) {}
    renderCharts();
  });

  // konfirmasi aksi tak dapat dibatalkan
  document.addEventListener('submit', function (e) {
    var m = e.target.getAttribute('data-confirm');
    if (m && !window.confirm(m)) e.preventDefault();
  });

  function api(path, body) {
    return fetch('/api/v1' + path, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF, 'Accept': 'application/json' },
      body: JSON.stringify(body || {})
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, data: j }; }); });
  }
  window.kpiApi = api;

  // ---------- chart
  var charts = [];
  function css(v) { return getComputedStyle(root).getPropertyValue(v).trim(); }
  function renderCharts() {
    if (!window.Chart) return;
    charts.forEach(function (c) { c.destroy(); }); charts = [];
    Chart.defaults.color = css('--k-muted'); Chart.defaults.borderColor = css('--k-line');
    Chart.defaults.font.family = '"IBM Plex Sans", system-ui, sans-serif';
    document.querySelectorAll('canvas[data-chart]').forEach(function (cv) {
      var cfg = JSON.parse(cv.getAttribute('data-chart'));
      cfg.options = Object.assign({ responsive: true, maintainAspectRatio: false }, cfg.options || {});
      charts.push(new Chart(cv.getContext('2d'), cfg));
    });
  }
  window.addEventListener('load', renderCharts);

  // ---------- AI insight
  function esc(s) { var d = document.createElement('div'); d.textContent = s == null ? '' : s; return d.innerHTML; }
  function li(a) { return (a || []).map(function (x) { return '<li>' + esc(x) + '</li>'; }).join(''); }
  var form = document.getElementById('ai-form');
  if (form) {
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var out = document.getElementById('ai-out'), btn = form.querySelector('button[type=submit]');
      out.innerHTML = '<div class="empty">Menganalisis data kinerja…</div>'; btn.disabled = true;
      var fd = new FormData(form);
      api('/ai/performance-analysis', { employee_id: fd.get('employee_id'), period_id: fd.get('period_id'), force: fd.get('force') ? 1 : 0 })
        .then(function (r) {
          btn.disabled = false;
          if (!r.ok) { out.innerHTML = '<div class="alert alert-warning">' + esc(r.data.error ? r.data.error.message : 'Gagal') + '</div>'; return; }
          var d = r.data;
          out.innerHTML = '<div class="ai-box"><div class="ai-label mb-2">' + esc(d.label) + ' · ' + esc(d.model) + ' · prompt ' + esc(d.prompt_version) + (d.cached ? ' · dari cache' : '') + '</div>' +
            '<h3 class="h6">' + esc(d.employee) + ' — ' + esc(d.period) + '</h3><p>' + esc(d.summary) + '</p>' +
            '<div class="row g-3"><div class="col-md-6"><b>Kekuatan</b><ul>' + li(d.strengths) + '</ul></div><div class="col-md-6"><b>Perlu ditingkatkan</b><ul>' + li(d.weaknesses) + '</ul></div></div>' +
            '<b>Rekomendasi</b><ol>' + li(d.recommendations) + '</ol>' +
            '<div>Tren: <b>' + esc(d.trend) + '</b> · Risiko: <span class="risk-' + esc(d.risk_level) + '">' + esc(d.risk_level) + '</span></div>' +
            '<div class="mt-2 small text-secondary">Bukti: ' + (d.evidence || []).map(function (e) { return esc(e.kpi) + ' ' + esc(e.achievement) + '%'; }).join('; ') + '</div>' +
            '<div class="mt-3 d-flex gap-2 align-items-center"><span class="small text-secondary">Apakah insight ini berguna?</span>' +
            '<button class="btn btn-sm btn-outline-success" data-rate="1" data-id="' + d.id + '">Ya</button>' +
            '<button class="btn btn-sm btn-outline-danger" data-rate="-1" data-id="' + d.id + '">Tidak</button></div></div>';
          var pf = document.getElementById('pred-out');
          if (pf) {
            api('/ai/prediction', { employee_id: fd.get('employee_id'), period_id: fd.get('period_id') }).then(function (p) {
              if (!p.ok) { pf.innerHTML = ''; return; }
              var x = p.data;
              pf.innerHTML = x.available ? '<div class="panel mt-3"><h3>Prediksi periode berikutnya</h3><div class="row g-3"><div class="col-sm-3"><div class="stat"><div class="l">Skor prediksi</div><div class="v">' + x.predicted_score + '</div><div class="s">rentang ' + x.low + ' – ' + x.high + '</div></div></div>' +
                '<div class="col-sm-3"><div class="stat"><div class="l">Kategori</div><div class="v" style="font-size:1.1rem">' + esc(x.category) + '</div></div></div>' +
                '<div class="col-sm-3"><div class="stat"><div class="l">Risiko</div><div class="v risk-' + x.risk_level + '" style="font-size:1.1rem">' + x.risk_level + '</div></div></div>' +
                '<div class="col-sm-3"><div class="stat"><div class="l">Metode</div><div class="v" style="font-size:1rem">' + esc(x.method) + '</div><div class="s">' + x.n_periods + ' periode data</div></div></div></div></div>'
                : '<div class="empty mt-3">' + esc(x.reason) + '</div>';
            });
          }
        });
    });
    document.addEventListener('click', function (e) {
      var b = e.target.closest('[data-rate]'); if (!b) return;
      api('/ai/insights/' + b.dataset.id + '/rating', { rating: parseInt(b.dataset.rate, 10) }).then(function () { b.parentNode.innerHTML = '<span class="small text-secondary">Terima kasih atas masukan Anda.</span>'; });
    });
  }

  // ---------- chat
  var cf = document.getElementById('chat-form');
  if (cf) {
    var box = document.getElementById('chat-box');
    function add(cls, txt) { var d = document.createElement('div'); d.className = 'msg ' + cls; d.textContent = txt; box.appendChild(d); box.scrollTop = box.scrollHeight; return d; }
    function ask(q) {
      add('u', q); var w = add('a', 'Mencari data…');
      api('/ai/chat', { message: q }).then(function (r) { w.textContent = r.ok ? r.data.answer : (r.data.error ? r.data.error.message : 'Terjadi kesalahan.'); box.scrollTop = box.scrollHeight; });
    }
    cf.addEventListener('submit', function (e) { e.preventDefault(); var i = cf.querySelector('input'); var q = i.value.trim(); if (!q) return; i.value = ''; ask(q); });
    document.querySelectorAll('[data-q]').forEach(function (b) { b.addEventListener('click', function () { ask(b.dataset.q); }); });
  }

  // validasi ukuran file bukti (UX saja; validasi sebenarnya di server)
  document.querySelectorAll('input[type=file][data-maxmb]').forEach(function (i) {
    i.addEventListener('change', function () {
      var max = parseFloat(i.dataset.maxmb) * 1048576;
      Array.prototype.forEach.call(i.files, function (f) { if (f.size > max) { alert(f.name + ' melebihi ' + i.dataset.maxmb + ' MB.'); i.value = ''; } });
    });
  });
})();
