// Mando MPV-UOS: vanilla JS, talks to mpvd's /api and listens to /events (SSE).
(() => {
  const $ = (id) => document.getElementById(id);
  const state = { st: null, seeking: false, tab: 'control', es: null, lastTracks: 0 };

  function fmt(s) {
    if (s == null || isNaN(s)) return '0:00';
    s = Math.max(0, Math.floor(s));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
    return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(r).padStart(2, '0');
  }
  function notice(text, error) {
    const n = $('notice');
    if (!text) { n.classList.add('hidden'); return; }
    n.textContent = text; n.classList.toggle('error', !!error); n.classList.remove('hidden');
  }
  async function api(path, body) {
    const opts = body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {};
    const r = await fetch(path, opts);
    if (r.status === 401) { notice('Sin emparejar: abre el mando desde el reproductor (alt+z) y escanea el QR.', true); throw new Error('401'); }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) { notice(data.error || ('Error ' + r.status), true); throw new Error(data.error || r.status); }
    return data;
  }
  const cmd = (name, args) => api('/api/cmd', Object.assign({ cmd: name }, args || {}));

  // -- pairing -------------------------------------------------------------------------------
  async function pairFromHash() {
    const m = location.hash.match(/[#&]t=([A-Za-z0-9_-]+)/);
    if (!m) return false;
    history.replaceState(null, '', location.pathname);
    try {
      const r = await api('/api/pair', { token: m[1] });
      notice('Emparejado: ' + r.name + '. Puedes añadir esta página a la pantalla de inicio.');
      setTimeout(() => notice(''), 6000);
      return true;
    } catch (e) { return false; }
  }

  // -- state --------------------------------------------------------------------------------
  function render(st) {
    state.st = st;
    if (st.no_player) {
      $('title').textContent = 'Mando MPV-UOS';
      $('subtitle').textContent = 'No hay ningún reproductor abierto';
      return;
    }
    const title = st.channel || st['media-title'] || st.filename || (st['idle-active'] ? 'Sin archivo' : '');
    $('title').textContent = title;
    const extra = st.icy_title ? ' · ' + st.icy_title : '';
    $('subtitle').textContent = (st.pause ? '⏸ ' : '▶ ') + fmt(st['time-pos']) + (st.duration ? ' / ' + fmt(st.duration) : ' · directo')
      + (st.speed && st.speed !== 1 ? ' · ' + st.speed + '×' : '') + extra;
    $('playpause').textContent = st.pause ? '▶' : '⏸';
    $('pos').textContent = fmt(st['time-pos']);
    $('dur').textContent = st.duration ? fmt(st.duration) : '∞';
    if (!state.seeking) {
      const bar = $('seekbar');
      bar.disabled = !st.duration;
      bar.value = st.duration ? Math.round(1000 * (st['time-pos'] || 0) / st.duration) : 0;
    }
    if (document.activeElement !== $('volume')) $('volume').value = Math.round(st.volume || 0);
    $('volval').textContent = Math.round(st.volume || 0);
    $('mute').textContent = st.mute ? '🔇' : '🔊';
    document.querySelectorAll('#speeds button').forEach((b) => b.classList.toggle('on', Number(b.dataset.speed) === Number(st.speed)));
  }
  function connect() {
    if (state.es) state.es.close();
    const es = new EventSource('/events');
    state.es = es;
    es.addEventListener('hello', () => notice(''));
    es.addEventListener('state', (e) => render(JSON.parse(e.data)));
    es.onerror = () => {
      $('subtitle').textContent = 'Sin conexión con el reproductor… reintentando';
      fetch('/api/state').then((r) => { if (r.status === 401) { es.close(); notice('Sin emparejar: escanea el QR del reproductor (alt+z).', true); } });
    };
  }

  // -- lists ---------------------------------------------------------------------------------
  function item(title, sub, onclick, on) {
    const d = document.createElement('div');
    d.className = 'item' + (on ? ' on' : '');
    d.innerHTML = '<div class="t"></div><div class="s"></div>';
    d.firstChild.textContent = title; d.lastChild.textContent = sub || '';
    d.onclick = onclick;
    return d;
  }
  function fill(el, nodes, empty) {
    el.innerHTML = '';
    if (!nodes.length) el.appendChild(item(empty, ''));
    nodes.forEach((n) => el.appendChild(n));
  }
  let channelTimer = null;
  async function loadChannels() {
    const q = $('channel-q').value.trim();
    const data = await api('/api/channels?q=' + encodeURIComponent(q) + '&limit=40');
    const rows = Array.isArray(data) ? data : [].concat(data.favorites || [], data.recents || []);
    const seen = new Set();
    fill($('channel-list'), rows.filter((c) => c && c.id && !seen.has(c.id) && seen.add(c.id)).map((c) =>
      item(c.name || c.id, [c.kind === 'radio' ? '📻' : '📺', c.group, c.country].filter(Boolean).join(' · '),
        () => cmd('channel', { id: c.id }).then(() => switchTab('control')), state.st && state.st.channel === c.name)),
      q ? 'Sin resultados' : 'Sin favoritos ni recientes. Escribe para buscar.');
  }
  async function loadSearch() {
    const q = $('search-q').value.trim();
    if (!q) return;
    $('search-info').textContent = 'Buscando…';
    try {
      const r = await api('/api/search?q=' + encodeURIComponent(q));
      const hits = (r.semantic && r.semantic.length ? r.semantic : r.text) || [];
      $('search-info').textContent = r.mode === 'semantic' ? 'Búsqueda semántica' : (r.status === 'unavailable' ? 'Sin índice semántico: coincidencia literal' : 'Coincidencia literal' + (r.status ? ' (indexando…)' : ''));
      fill($('search-list'), hits.map((h) => item(h.text || h.cue || '', fmt(h.start != null ? h.start : h.time),
        () => cmd('seek', { seconds: h.start != null ? h.start : h.time, mode: 'absolute' }).then(() => switchTab('control')))),
        'Nada encontrado (¿hay transcripción IA de este archivo?)');
    } catch (e) { $('search-info').textContent = ''; }
  }
  async function loadRecents() {
    const rows = await api('/api/recents?limit=30');
    fill($('recents-list'), rows.map((r) => item(r.title || r.name || r.path, (r.position ? fmt(r.position) + (r.duration ? ' / ' + fmt(r.duration) : '') : '') + (r.finished ? ' · visto' : ''),
      () => cmd('play', { target: r.path }).then(() => switchTab('control')))), 'Sin recientes');
  }
  async function loadMore() {
    const [tracks, ch, pl] = await Promise.all([api('/api/tracks'), api('/api/chapters'), api('/api/playlist')]);
    fill($('sub-list'), [item('Sin subtítulos', '', () => cmd('sub', { id: 'no' }), !tracks.sub.some((t) => t.selected))].concat(
      tracks.sub.map((t) => item(t.title || ('Pista ' + t.id), [t.lang, t.codec, t.external ? 'externa' : ''].filter(Boolean).join(' · '),
        () => cmd('sub', { id: t.id }).then(loadMore), t.selected))), '');
    fill($('audio-list'), tracks.audio.map((t) => item(t.title || ('Pista ' + t.id), [t.lang, t.codec].filter(Boolean).join(' · '),
      () => cmd('audio', { id: t.id }).then(loadMore), t.selected)), 'Sin audio');
    fill($('chapter-list'), (ch.chapters || []).map((c, i) => item(c.title || ('Capítulo ' + (i + 1)), fmt(c.time),
      () => cmd('chapter_set', { index: i }).then(() => switchTab('control')), i === ch.current)), 'Sin capítulos');
    fill($('playlist'), (pl.items || []).map((p) => item(p.title || (p.filename || '').split('/').pop(), '',
      () => cmd('playlist_play', { index: p.index }).then(() => switchTab('control')), p.current)), 'Lista vacía');
  }
  function switchTab(name) {
    state.tab = name;
    document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('hidden', t.id !== 'tab-' + name));
    document.querySelectorAll('nav button').forEach((b) => b.classList.toggle('active', b.dataset.tab === name));
    if (name === 'channels') loadChannels().catch(() => {});
    if (name === 'recents') loadRecents().catch(() => {});
    if (name === 'more') loadMore().catch(() => {});
    window.scrollTo(0, 0);
  }

  // -- wiring ---------------------------------------------------------------------------------
  document.querySelectorAll('nav button').forEach((b) => b.onclick = () => switchTab(b.dataset.tab));
  document.querySelectorAll('button[data-cmd]').forEach((b) => b.onclick = () => {
    const args = {};
    if (b.dataset.seconds) args.seconds = Number(b.dataset.seconds);
    if (b.dataset.delta) args.delta = Number(b.dataset.delta);
    if (b.dataset.name) args.name = b.dataset.name;
    cmd(b.dataset.cmd, args).catch(() => {});
  });
  document.querySelectorAll('#speeds button').forEach((b) => b.onclick = () => cmd('speed', { value: Number(b.dataset.speed) }).catch(() => {}));
  const bar = $('seekbar');
  bar.oninput = () => { state.seeking = true; if (state.st && state.st.duration) $('pos').textContent = fmt(bar.value / 1000 * state.st.duration); };
  bar.onchange = () => {
    state.seeking = false;
    if (state.st && state.st.duration) cmd('seek', { seconds: bar.value / 1000 * state.st.duration, mode: 'absolute' }).catch(() => {});
  };
  $('volume').onchange = () => cmd('volume', { value: Number($('volume').value) }).catch(() => {});
  $('volume').oninput = () => { $('volval').textContent = $('volume').value; };
  $('channel-q').oninput = () => { clearTimeout(channelTimer); channelTimer = setTimeout(() => loadChannels().catch(() => {}), 250); };
  $('search-q').onchange = () => loadSearch();
  $('search-q').onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); loadSearch(); } };
  $('open-form').onsubmit = (e) => { e.preventDefault(); cmd('play', { target: $('open-url').value.trim() }).then(() => { $('open-url').value = ''; switchTab('control'); }).catch(() => {}); };
  $('unpair').onclick = async () => { if (confirm('¿Desemparejar este móvil?')) { await api('/api/unpair', {}); location.reload(); } };
  $('about').textContent = 'Mando local de MPV-UOS · ' + location.host;

  window.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'INPUT') return;
    if (e.key === ' ') { e.preventDefault(); cmd('toggle'); }
    if (e.key === 'ArrowRight') cmd('seek', { seconds: 10 });
    if (e.key === 'ArrowLeft') cmd('seek', { seconds: -10 });
  });

  (async () => {
    await pairFromHash();
    if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
    connect();
  })();
})();
