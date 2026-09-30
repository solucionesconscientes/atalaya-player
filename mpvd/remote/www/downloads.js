// Downloads panel of MPV-UOS (H23): vanilla JS on mpvd's /api/tasks + /events/tasks (SSE), same pairing cookie as
// the remote. Live table of downloads and conversions, bulk actions, paste/drop links, disk space, and a notice
// (browser notification when allowed, otherwise banner + vibration) when a task finishes.
(() => {
  const $ = (id) => document.getElementById(id);
  const state = { tasks: [], selected: new Map(), prev: null, es: null, poll: null, title: document.title, unseen: 0 };
  const PRESET_KEY = 'mu-dl-preset';
  const NOTIFY_KEY = 'mu-dl-notify';

  function notice(text, error) {
    const n = $('notice');
    if (!text) { n.classList.add('hidden'); return; }
    n.textContent = text; n.classList.toggle('error', !!error); n.classList.remove('hidden');
  }
  const UNPAIRED = 'Sin emparejar: en el reproductor abre Mando a distancia → «Panel de descargas en el navegador», o escanea el QR (alt+z) y entra en Más → Descargas.';
  async function api(path, body) {
    const opts = body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {};
    const r = await fetch(path, opts);
    if (r.status === 401) { notice(UNPAIRED, true); throw new Error('401'); }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) { notice(data.error || ('Error ' + r.status), true); throw new Error(data.error || r.status); }
    return data;
  }
  function bytes(n) {
    if (n == null) return '?';
    const u = ['B', 'KB', 'MB', 'GB', 'TB']; let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return (i >= 3 ? n.toFixed(1) : Math.round(n)) + ' ' + u[i];
  }
  const key = (t) => t.type + ':' + t.id;
  const active = (t) => t.status === 'queued' || t.status === 'running';
  const STATUS = { queued: 'en cola', running: 'en marcha', done: 'terminada', failed: 'falló', cancelled: 'cancelada' };

  // -- pairing / links from the hash -----------------------------------------------------------------
  async function fromHash() {
    const h = location.hash;
    if (!h) return;
    history.replaceState(null, '', location.pathname);
    const t = h.match(/[#&]t=([A-Za-z0-9_-]+)/);
    if (t) {
      try {
        const r = await api('/api/pair', { token: t[1] });
        notice('Emparejado: ' + r.name + '.');
        setTimeout(() => notice(''), 5000);
      } catch (e) { /* notice already shown */ }
    }
    const add = h.match(/[#&]add=([^&]+)/);
    if (add) {
      // never downloaded on arrival: any page could open this address; the user confirms with «Descargar»
      $('links').value = decodeURIComponent(add[1]);
      notice('Enlace recibido. Elige el formato y pulsa «Descargar».');
      $('add').focus();
    }
  }

  // -- rendering ------------------------------------------------------------------------------------
  function row(t) {
    const d = document.createElement('div');
    const k = key(t);
    d.className = 'task ' + t.status + (state.selected.has(k) ? ' sel' : '');
    d.dataset.key = k;
    const cb = document.createElement('input');
    cb.type = 'checkbox'; cb.checked = state.selected.has(k); cb.setAttribute('aria-label', 'Seleccionar');
    const main = document.createElement('div');
    main.style.minWidth = '0';
    const title = document.createElement('div'); title.className = 't';
    title.textContent = (t.type === 'convert' ? '⚙ ' : '⬇ ') + (t.title || '?');
    title.title = t.title || '';
    const sub = document.createElement('div'); sub.className = 's';
    const bits = [];
    if (t.description) bits.push(t.description);
    if (t.status === 'failed' && t.error) bits.push(t.error.split('\n').pop());
    else if (t.message && t.status !== 'done') bits.push(t.message);
    if (t.status === 'done' && t.outputs && t.outputs.length) bits.push(t.outputs[0].split(/[\\/]/).pop());
    sub.textContent = bits.join(' · ');
    sub.title = bits.join('\n');
    main.append(title, sub);
    const right = document.createElement('div'); right.className = 'st';
    if (t.status === 'done' && t.outputs && t.outputs.length) {
      const play = document.createElement('button');
      play.textContent = '▶'; play.title = 'Reproducir en MPV-UOS';
      play.onclick = (e) => { e.stopPropagation(); act('play', [t]); };
      right.appendChild(play);
    } else {
      right.textContent = t.status === 'running' ? Math.round(100 * (t.progress || 0)) + ' %' : (STATUS[t.status] || t.status);
    }
    const bar = document.createElement('div'); bar.className = 'p';
    const fill = document.createElement('div'); fill.style.width = (100 * (t.status === 'done' ? 1 : (t.progress || 0))).toFixed(1) + '%';
    bar.appendChild(fill);
    d.append(cb, main, right, bar);
    d.onclick = (e) => { if (e.target !== cb) cb.checked = !cb.checked; toggle(t, cb.checked); d.classList.toggle('sel', cb.checked); };
    return d;
  }
  function fill(el, rows, empty) {
    el.innerHTML = '';
    if (!rows.length) { const p = document.createElement('div'); p.className = 'muted'; p.textContent = empty; el.appendChild(p); }
    rows.forEach((t) => el.appendChild(row(t)));
  }
  function render(data) {
    if (data.error) notice(data.error, true);
    state.tasks = data.tasks || [];
    const live = new Set(state.tasks.map(key));
    for (const k of [...state.selected.keys()]) if (!live.has(k)) state.selected.delete(k);
    const act = state.tasks.filter(active), hist = state.tasks.filter((t) => !active(t));
    fill($('active'), act, 'Nada en marcha. Pega un enlace arriba para empezar.');
    fill($('history'), hist, 'El historial está vacío.');
    $('count-active').textContent = act.length ? String(act.length) : '';
    $('count-history').textContent = data.history ? String(data.history) : '';
    $('subtitle').textContent = act.length ? act.length + ' en curso' : 'Sin tareas en curso';
    renderDisk(data.disk || []);
    updateBar();
    watchFinished(state.tasks);
  }
  function renderDisk(disk) {
    const el = $('disk');
    el.innerHTML = '';
    disk.forEach((d) => {
      const line = document.createElement('div');
      const low = d.total && d.free / d.total < 0.05;
      line.className = low ? 'low' : '';
      const kinds = d.kinds.map((k) => k === 'audio' ? 'audio' : 'vídeo').join(' y ');
      const b = document.createElement('b'); b.textContent = bytes(d.free) + ' libres';
      line.append('💾 ', b, ' de ' + bytes(d.total) + ' · ' + kinds + ': ' + d.path + (low ? ' · ¡queda poco sitio!' : ''));
      el.appendChild(line);
    });
  }

  // -- selection and actions ------------------------------------------------------------------------------
  function toggle(t, on) { if (on) state.selected.set(key(t), t); else state.selected.delete(key(t)); updateBar(); }
  function selectedTasks() { return state.tasks.filter((t) => state.selected.has(key(t))); }
  function updateBar() {
    const sel = selectedTasks();
    $('act-cancel').disabled = !sel.some(active);
    $('act-retry').disabled = !sel.some((t) => t.status === 'failed' || t.status === 'cancelled' || t.status === 'done');
    $('act-remove').disabled = !sel.some((t) => !active(t));
    $('selcount').textContent = sel.length ? sel.length + ' seleccionada' + (sel.length > 1 ? 's' : '') : '';
    const act = state.tasks.filter(active), hist = state.tasks.filter((t) => !active(t));
    $('all-active').checked = act.length > 0 && act.every((t) => state.selected.has(key(t)));
    $('all-history').checked = hist.length > 0 && hist.every((t) => state.selected.has(key(t)));
  }
  function selectAll(filter, on) {
    state.tasks.filter(filter).forEach((t) => { if (on) state.selected.set(key(t), t); else state.selected.delete(key(t)); });
    render({ tasks: state.tasks, history: state.tasks.filter((t) => !active(t)).length, disk: state.disk || [] });
  }
  async function act(action, tasks) {
    const fits = { cancel: active, retry: (t) => !active(t), remove: (t) => !active(t), play: (t) => t.status === 'done' };
    const items = tasks.filter(fits[action]).map((t) => ({ type: t.type, id: t.id }));
    if (!items.length) return;
    try {
      const r = await api('/api/tasks/action', { action, items });
      if (action === 'play') { notice('▶ En el reproductor: ' + r.target.split(/[\\/]/).pop()); setTimeout(() => notice(''), 4000); return; }
      const failed = (r.results || []).filter((x) => !x.ok);
      notice(failed.length ? (r.done + ' hechas, ' + failed.length + ' no: ' + failed[0].error) : '', failed.length > 0);
      if (action === 'remove') tasks.forEach((t) => state.selected.delete(key(t)));
      refresh();
    } catch (e) { /* notice shown */ }
  }
  async function refresh() {
    try { render(await api('/api/tasks')); } catch (e) { /* notice shown */ }
  }

  // -- adding links ---------------------------------------------------------------------------------
  async function loadPresets() {
    const r = await api('/api/downloads/presets');
    const sel = $('preset');
    const groups = { video: 'Vídeo', audio: 'Audio', subs: 'Subtítulos' };
    const byGroup = {};
    r.presets.forEach((p) => { (byGroup[p.group] = byGroup[p.group] || []).push(p); });
    sel.innerHTML = '';
    Object.keys(byGroup).forEach((g) => {
      const og = document.createElement('optgroup'); og.label = groups[g] || g;
      byGroup[g].forEach((p) => { const o = document.createElement('option'); o.value = p.id; o.textContent = p.title; og.appendChild(o); });
      sel.appendChild(og);
    });
    const saved = localStorage.getItem(PRESET_KEY);
    sel.value = saved && r.presets.some((p) => p.id === saved) ? saved : r.default;
  }
  async function add() {
    const text = $('links').value.trim();
    if (!text) { notice('Pega o arrastra algún enlace.', true); return; }
    $('add').disabled = true;
    try {
      const r = await api('/api/downloads/add', { text, preset: $('preset').value });
      $('links').value = '';
      notice('⬇ ' + r.count + (r.count === 1 ? ' descarga en cola' : ' descargas en cola'));
      setTimeout(() => notice(''), 4000);
      refresh();
    } catch (e) { /* notice shown */ } finally { $('add').disabled = false; }
  }
  function appendLinks(text) {
    if (!text) return;
    const cur = $('links').value.trim();
    $('links').value = (cur ? cur + '\n' : '') + text.trim();
  }
  function wireDrop() {
    const zone = $('drop');
    const over = (e) => { e.preventDefault(); zone.classList.add('over'); };
    document.addEventListener('dragover', over);
    document.addEventListener('dragleave', (e) => { if (!e.relatedTarget) zone.classList.remove('over'); });
    document.addEventListener('drop', (e) => {
      e.preventDefault();
      zone.classList.remove('over');
      const dt = e.dataTransfer;
      if (dt.files && dt.files.length) {
        [...dt.files].filter((f) => f.size < 1024 * 1024).forEach((f) => f.text().then(appendLinks));
        return;
      }
      const uris = (dt.getData('text/uri-list') || '').split(/\r?\n/).filter((l) => l && !l.startsWith('#')).join('\n');
      appendLinks(uris || dt.getData('text/plain'));
    });
  }

  // -- notice when a task finishes ------------------------------------------------------------------------
  function canNotify() { return window.isSecureContext && 'Notification' in window; }
  function watchFinished(tasks) {
    const now = new Map(tasks.map((t) => [key(t), t.status]));
    if (state.prev) {
      tasks.forEach((t) => {
        const before = state.prev.get(key(t));
        if ((before === 'queued' || before === 'running') && (t.status === 'done' || t.status === 'failed')) finished(t);
      });
    }
    state.prev = now;
  }
  function finished(t) {
    const ok = t.status === 'done';
    const text = (ok ? '✓ Terminada: ' : '✗ Falló: ') + (t.title || '');
    notice(text, !ok);
    if (navigator.vibrate) navigator.vibrate(ok ? [120, 80, 120] : [400]);
    if (document.hidden) { state.unseen++; document.title = '(' + state.unseen + ') ' + state.title; }
    if (localStorage.getItem(NOTIFY_KEY) === '1' && canNotify() && Notification.permission === 'granted') {
      const opts = { body: t.title || '', icon: '/icon-192.png', tag: key(t) };
      const title = ok ? 'Descarga terminada' : 'Descarga fallida';
      // Android Chrome only shows notifications through the service worker
      if (navigator.serviceWorker && navigator.serviceWorker.controller) {
        navigator.serviceWorker.ready.then((reg) => reg.showNotification(title, opts)).catch(() => {});
      } else {
        try { new Notification(title, opts); } catch (e) { /* not allowed here */ }
      }
    }
  }
  async function askNotify() {
    if (!canNotify()) {
      notice('Este navegador no permite notificaciones en una dirección de red local sin HTTPS. El aviso sale aquí arriba y el móvil vibra; deja la página abierta.');
      return;
    }
    const p = await Notification.requestPermission();
    localStorage.setItem(NOTIFY_KEY, p === 'granted' ? '1' : '0');
    $('notify').textContent = p === 'granted' ? 'Avisos activados' : 'Avisarme al terminar';
    notice(p === 'granted' ? 'Te avisaré cuando termine cada descarga.' : 'Sin permiso: el aviso saldrá solo en la página.');
  }
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { state.unseen = 0; document.title = state.title; } });

  // -- live updates ------------------------------------------------------------------------------------
  function connect() {
    if (state.es) state.es.close();
    if (!('EventSource' in window)) { state.poll = setInterval(refresh, 1000); refresh(); return; }
    const es = new EventSource('/events/tasks');
    state.es = es;
    es.addEventListener('tasks', (e) => { const d = JSON.parse(e.data); state.disk = d.disk; render(d); });
    es.onerror = () => {
      $('subtitle').textContent = 'Sin conexión con MPV-UOS… reintentando';
      fetch('/api/tasks').then((r) => { if (r.status === 401) { es.close(); notice(UNPAIRED, true); } }).catch(() => {});
    };
  }

  // -- bookmarklets ------------------------------------------------------------------------------------
  function bookmarklets() {
    const js = (body) => 'javascript:(function(){' + body + '})()';
    $('mk-scheme').href = js("location.href='mpv-uos://download?url='+encodeURIComponent(location.href)");
    $('mk-open').href = js("location.href='mpv-uos://open?path='+encodeURIComponent(location.href)");
    $('mk-panel').href = js("window.open('" + location.origin + "/downloads#add='+encodeURIComponent(location.href),'mpv-uos-descargas')");
    document.querySelectorAll('.marklets a').forEach((a) => a.onclick = (e) => {
      e.preventDefault();
      notice('Arrástralo a la barra de marcadores; luego púlsalo en la página del vídeo.');
    });
  }

  // -- wiring -------------------------------------------------------------------------------------------
  $('add').onclick = add;
  $('links').onkeydown = (e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); add(); } };
  $('preset').onchange = () => localStorage.setItem(PRESET_KEY, $('preset').value);
  $('act-cancel').onclick = () => act('cancel', selectedTasks());
  $('act-retry').onclick = () => act('retry', selectedTasks());
  $('act-remove').onclick = () => act('remove', selectedTasks());
  $('all-active').onchange = (e) => selectAll(active, e.target.checked);
  $('all-history').onchange = (e) => selectAll((t) => !active(t), e.target.checked);
  $('clear').onclick = async () => {
    if (!confirm('¿Vaciar el historial? Los archivos no se borran.')) return;
    try { const r = await api('/api/tasks/clear', {}); notice(r.removed + ' quitadas del historial'); refresh(); } catch (e) { /* shown */ }
  };
  $('notify').onclick = askNotify;
  if (localStorage.getItem(NOTIFY_KEY) === '1' && canNotify() && Notification.permission === 'granted') $('notify').textContent = 'Avisos activados';
  window.addEventListener('hashchange', fromHash);
  wireDrop();
  bookmarklets();

  (async () => {
    await fromHash();
    if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
    loadPresets().catch(() => {});
    connect();
  })();
})();
