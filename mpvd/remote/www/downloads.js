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
  const UNPAIRED = () => t('Sin emparejar: en el reproductor abre Mando a distancia → «Panel de descargas en el navegador».')
    + ' ' + t('O escanea el QR (alt+z) y entra en Más → Descargas.');
  async function api(path, body) {
    const opts = body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {};
    const r = await fetch(path, opts);
    if (r.status === 401) { notice(UNPAIRED(), true); throw new Error('401'); }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) { notice(data.error || t('Error %s', r.status), true); throw new Error(data.error || r.status); }
    return data;
  }
  function bytes(n) {
    if (n == null) return '?';
    const u = ['B', 'KB', 'MB', 'GB', 'TB']; let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return (i >= 3 ? n.toFixed(1) : Math.round(n)) + ' ' + u[i];
  }
  const key = (x) => x.type + ':' + x.id;
  const active = (x) => x.status === 'queued' || x.status === 'running';
  const STATUS = { queued: t('en cola'), running: t('en marcha'), done: t('terminada'), failed: t('falló'),
                   cancelled: t('cancelada') };

  // -- pairing / links from the hash -----------------------------------------------------------------
  async function fromHash() {
    const h = location.hash;
    if (!h) return;
    history.replaceState(null, '', location.pathname);
    const m = h.match(/[#&]t=([A-Za-z0-9_-]+)/);
    if (m) {
      try {
        const r = await api('/api/pair', { token: m[1] });
        notice(t('Emparejado: %s.', r.name));
        setTimeout(() => notice(''), 5000);
      } catch (e) { /* notice already shown */ }
    }
    const add = h.match(/[#&]add=([^&]+)/);
    if (add) {
      // never downloaded on arrival: any page could open this address; the user confirms with «Descargar»
      $('links').value = decodeURIComponent(add[1]);
      notice(t('Enlace recibido. Elige el formato y pulsa «Descargar».'));
      $('add').focus();
    }
  }

  // -- rendering ------------------------------------------------------------------------------------
  function row(task) {
    const d = document.createElement('div');
    const k = key(task);
    d.className = 'task ' + task.status + (state.selected.has(k) ? ' sel' : '');
    d.dataset.key = k;
    const cb = document.createElement('input');
    cb.type = 'checkbox'; cb.checked = state.selected.has(k); cb.setAttribute('aria-label', t('Seleccionar'));
    const main = document.createElement('div');
    main.style.minWidth = '0';
    const title = document.createElement('div'); title.className = 't';
    title.textContent = (task.type === 'convert' ? '⚙ ' : '⬇ ') + (task.title || '?');
    title.title = task.title || '';
    const sub = document.createElement('div'); sub.className = 's';
    const bits = [];
    if (task.description) bits.push(task.description);
    if (task.status === 'failed' && task.error) bits.push(task.error.split('\n').pop());
    else if (task.message && task.status !== 'done') bits.push(task.message);
    if (task.status === 'done' && task.outputs && task.outputs.length) bits.push(task.outputs[0].split(/[\\/]/).pop());
    sub.textContent = bits.join(' · ');
    sub.title = bits.join('\n');
    main.append(title, sub);
    const right = document.createElement('div'); right.className = 'st';
    if (task.status === 'done' && task.outputs && task.outputs.length) {
      const play = document.createElement('button');
      play.textContent = '▶'; play.title = t('Reproducir en MPV-UOS');
      play.onclick = (e) => { e.stopPropagation(); act('play', [task]); };
      right.appendChild(play);
    } else {
      right.textContent = task.status === 'running' ? Math.round(100 * (task.progress || 0)) + ' %' : (STATUS[task.status] || task.status);
    }
    const bar = document.createElement('div'); bar.className = 'p';
    const fill = document.createElement('div'); fill.style.width = (100 * (task.status === 'done' ? 1 : (task.progress || 0))).toFixed(1) + '%';
    bar.appendChild(fill);
    d.append(cb, main, right, bar);
    d.onclick = (e) => { if (e.target !== cb) cb.checked = !cb.checked; toggle(task, cb.checked); d.classList.toggle('sel', cb.checked); };
    return d;
  }
  function fill(el, rows, empty) {
    el.innerHTML = '';
    if (!rows.length) { const p = document.createElement('div'); p.className = 'muted'; p.textContent = empty; el.appendChild(p); }
    rows.forEach((tk) => el.appendChild(row(tk)));
  }
  function render(data) {
    if (data.error) notice(data.error, true);
    state.tasks = data.tasks || [];
    const live = new Set(state.tasks.map(key));
    for (const k of [...state.selected.keys()]) if (!live.has(k)) state.selected.delete(k);
    const act = state.tasks.filter(active), hist = state.tasks.filter((tk) => !active(tk));
    fill($('active'), act, t('Nada en marcha. Pega un enlace arriba para empezar.'));
    fill($('history'), hist, t('El historial está vacío.'));
    $('count-active').textContent = act.length ? String(act.length) : '';
    $('count-history').textContent = data.history ? String(data.history) : '';
    $('subtitle').textContent = act.length ? t('%s en curso', act.length) : t('Sin tareas en curso');
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
      const kinds = d.kinds.map((k) => k === 'audio' ? t('audio') : t('vídeo')).join(t(' y '));
      const b = document.createElement('b'); b.textContent = t('%s libres', bytes(d.free));
      line.append('💾 ', b, t(' de %s · %s: %s', bytes(d.total), kinds, d.path)
        + (low ? t(' · ¡queda poco sitio!') : ''));
      el.appendChild(line);
    });
  }

  // -- selection and actions ------------------------------------------------------------------------------
  function toggle(tk, on) { if (on) state.selected.set(key(tk), tk); else state.selected.delete(key(tk)); updateBar(); }
  function selectedTasks() { return state.tasks.filter((tk) => state.selected.has(key(tk))); }
  function updateBar() {
    const sel = selectedTasks();
    $('act-cancel').disabled = !sel.some(active);
    $('act-retry').disabled = !sel.some((tk) => tk.status === 'failed' || tk.status === 'cancelled' || tk.status === 'done');
    $('act-remove').disabled = !sel.some((tk) => !active(tk));
    $('selcount').textContent = sel.length === 1 ? t('1 seleccionada') : (sel.length ? t('%s seleccionadas', sel.length) : '');
    const act = state.tasks.filter(active), hist = state.tasks.filter((tk) => !active(tk));
    $('all-active').checked = act.length > 0 && act.every((tk) => state.selected.has(key(tk)));
    $('all-history').checked = hist.length > 0 && hist.every((tk) => state.selected.has(key(tk)));
  }
  function selectAll(filter, on) {
    state.tasks.filter(filter).forEach((tk) => { if (on) state.selected.set(key(tk), tk); else state.selected.delete(key(tk)); });
    render({ tasks: state.tasks, history: state.tasks.filter((tk) => !active(tk)).length, disk: state.disk || [] });
  }
  async function act(action, tasks) {
    const fits = { cancel: active, retry: (tk) => !active(tk), remove: (tk) => !active(tk), play: (tk) => tk.status === 'done' };
    const items = tasks.filter(fits[action]).map((tk) => ({ type: tk.type, id: tk.id }));
    if (!items.length) return;
    try {
      const r = await api('/api/tasks/action', { action, items });
      if (action === 'play') { notice(t('▶ En el reproductor: %s', r.target.split(/[\\/]/).pop())); setTimeout(() => notice(''), 4000); return; }
      const failed = (r.results || []).filter((x) => !x.ok);
      notice(failed.length ? t('%s hechas, %s no: %s', r.done, failed.length, failed[0].error) : '', failed.length > 0);
      if (action === 'remove') tasks.forEach((tk) => state.selected.delete(key(tk)));
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
    const groups = { video: t('Vídeo'), audio: t('Audio'), subs: t('Subtítulos') };
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
    if (!text) { notice(t('Pega o arrastra algún enlace.'), true); return; }
    $('add').disabled = true;
    try {
      const r = await api('/api/downloads/add', { text, preset: $('preset').value });
      $('links').value = '';
      notice(r.count === 1 ? t('⬇ %s descarga en cola', r.count) : t('⬇ %s descargas en cola', r.count));
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
    const now = new Map(tasks.map((tk) => [key(tk), tk.status]));
    if (state.prev) {
      tasks.forEach((tk) => {
        const before = state.prev.get(key(tk));
        if ((before === 'queued' || before === 'running') && (tk.status === 'done' || tk.status === 'failed')) finished(tk);
      });
    }
    state.prev = now;
  }
  function finished(task) {
    const ok = task.status === 'done';
    const text = ok ? t('✓ Terminada: %s', task.title || '') : t('✗ Falló: %s', task.title || '');
    notice(text, !ok);
    if (navigator.vibrate) navigator.vibrate(ok ? [120, 80, 120] : [400]);
    if (document.hidden) { state.unseen++; document.title = '(' + state.unseen + ') ' + state.title; }
    if (localStorage.getItem(NOTIFY_KEY) === '1' && canNotify() && Notification.permission === 'granted') {
      const opts = { body: task.title || '', icon: '/icon-192.png', tag: key(task) };
      const title = ok ? t('Descarga terminada') : t('Descarga fallida');
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
      notice(t('Este navegador no permite notificaciones en una dirección de red local sin HTTPS.') + ' '
        + t('El aviso sale aquí arriba y el móvil vibra; deja la página abierta.'));
      return;
    }
    const p = await Notification.requestPermission();
    localStorage.setItem(NOTIFY_KEY, p === 'granted' ? '1' : '0');
    $('notify').textContent = p === 'granted' ? t('Avisos activados') : t('Avisarme al terminar');
    notice(p === 'granted' ? t('Te avisaré cuando termine cada descarga.')
      : t('Sin permiso: el aviso saldrá solo en la página.'));
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
      $('subtitle').textContent = t('Sin conexión con MPV-UOS… reintentando');
      fetch('/api/tasks').then((r) => { if (r.status === 401) { es.close(); notice(UNPAIRED(), true); } }).catch(() => {});
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
      notice(t('Arrástralo a la barra de marcadores; luego púlsalo en la página del vídeo.'));
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
  $('all-history').onchange = (e) => selectAll((tk) => !active(tk), e.target.checked);
  $('clear').onclick = async () => {
    if (!confirm(t('¿Vaciar el historial? Los archivos no se borran.'))) return;
    try { const r = await api('/api/tasks/clear', {}); notice(t('%s quitadas del historial', r.removed)); refresh(); } catch (e) { /* shown */ }
  };
  $('notify').onclick = askNotify;
  if (localStorage.getItem(NOTIFY_KEY) === '1' && canNotify() && Notification.permission === 'granted') $('notify').textContent = t('Avisos activados');
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
