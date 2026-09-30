// «Ver juntos» (H25): the guest page. Joins with the token of the link (#k=…) and a name, then follows the host
// over SSE (/s/<room>/events) and keeps its own <video> in step (MuSync). HLS plays natively where the browser can
// (Safari, iOS, Android) and through the vendored hls.js elsewhere (desktop Chrome/Firefox), loaded only if needed.
// Private rooms add a chat and reactions (always drawn as text: textContent, never HTML). A public «solo ver» link
// (#k=…&v=1) joins by itself without a name and shows only the picture and how many are watching.
(function () {
  'use strict';
  var S = window.MuSync;
  var $ = function (id) { return document.getElementById(id); };
  var roomId = (location.pathname.match(/^\/s\/([A-Za-z0-9_-]+)/) || [])[1] || '';
  var base = '/s/' + roomId + '/';
  var token = (location.hash.match(/[#&]k=([A-Za-z0-9_-]+)/) || [])[1] || '';
  var publicLink = /[#&]v=1\b/.test(location.hash);
  var tokenKey = 'mu-share-token-' + roomId;
  try {  // a public viewer whose seat was given away can come back without the link (this tab only)
    if (token && publicLink) sessionStorage.setItem(tokenKey, token);
    else if (!token && sessionStorage.getItem(tokenKey)) { token = sessionStorage.getItem(tokenKey); publicLink = true; }
  } catch (e) { /* storage disabled */ }
  var mode = publicLink ? 'public' : 'private', lastChat = 0, reactions = {};
  // ?hlsjs=1: use hls.js even where the browser plays HLS by itself (tests, comparing engines)
  var forceHlsJs = /[?&]hlsjs=1\b/.test(location.search);
  var video = $('video');
  var me = null, perm = 'view', pending = false;
  var state = null, anchor = 0, media = null, source = '', subsUrl = '';
  var hls = null, hlsLoading = null, useRelay = false, es = null, needGesture = false, ended = false;
  var seeking = false;

  function show(id, on) { $(id).classList.toggle('hidden', !on); }
  function message(text) { show('join', false); show('room', false); show('message', true); $('message-text').textContent = text; }

  function api(path, body) {
    var opts = { method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin', headers: {} };
    if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
    return fetch(base + path, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok) { var e = new Error(data.error || ('HTTP ' + r.status)); e.status = r.status; throw e; }
        return data;
      });
    });
  }

  function toast(text) {
    if (!text) return;
    var el = document.createElement('div');
    el.className = 'toast';
    el.textContent = text;
    $('toasts').appendChild(el);
    setTimeout(function () { el.classList.add('fade'); }, 4500);
    setTimeout(function () { el.remove(); }, 5000);
  }

  function overlay(text, button) {
    show('overlay', !!text || !!button);
    $('overlay-text').textContent = text || '';
    show('start', !!button);
  }

  // -- joining -----------------------------------------------------------------------------------------

  function askName() {
    if (publicLink && token) { autoJoin(); return; }
    show('message', false); show('room', false); show('join', true);
    $('name').value = localStorage.getItem('mu-share-name') || '';
    $('name').focus();
    if (!token) $('join-error').textContent = 'Abre el enlace de invitación completo que te han pasado.';
  }

  $('join-form').addEventListener('submit', function (ev) {
    ev.preventDefault();
    var name = $('name').value.trim();
    if (!name) return;
    $('join-error').textContent = '';
    api('api/join', { token: token, name: name }).then(function (data) {
      localStorage.setItem('mu-share-name', name);
      history.replaceState(null, '', location.pathname + location.search);  // the token is not needed any more
      enter(data.guest, true, data.room);
    }).catch(function (e) { $('join-error').textContent = e.message; });
  });

  function autoJoin() {
    api('api/join', { token: token, name: '' }).then(function (data) {
      history.replaceState(null, '', location.pathname + location.search);
      enter(data.guest, false, data.room);
    }).catch(function (e) {
      message(e.status === 410 ? 'La sala está cerrada o ha caducado.' : 'No se puede entrar: ' + e.message);
    });
  }

  function setMode(room) {
    if (room && room.mode) mode = room.mode;
    var pub = mode === 'public';
    show('guests', !pub); show('guests-title', !pub); show('viewers', pub);
    show('chat', !pub);
    ['back10', 'play', 'fwd10'].forEach(function (id) { show(id, !pub); });
    if (pub) { show('ask', false); $('seek').disabled = true; }
    if (room && pub) renderViewers({ count: room.viewers, max: room.max_guests });
  }

  function enter(guest, gesture, room) {
    me = guest; perm = guest.perm; pending = guest.pending;
    needGesture = !gesture;
    show('join', false); show('message', false); show('room', true);
    setMode(room);
    $('who').textContent = mode === 'public' ? 'Sala pública · solo ver' : 'Estás como ' + guest.name;
    updatePerm();
    connect();
  }

  // -- events ------------------------------------------------------------------------------------------

  function connect() {
    if (es) es.close();
    es = new EventSource(base + 'events');
    es.addEventListener('hello', function (e) {
      var d = JSON.parse(e.data); me = d.guest; perm = me.perm; pending = me.pending; updatePerm(); setMode(d.room);
    });
    es.addEventListener('state', function (e) { onState(JSON.parse(e.data)); });
    es.addEventListener('media', function (e) { onMedia(JSON.parse(e.data)); });
    es.addEventListener('guests', function (e) { renderGuests(JSON.parse(e.data)); });
    es.addEventListener('viewers', function (e) { renderViewers(JSON.parse(e.data)); });
    es.addEventListener('chat', function (e) { addChat(JSON.parse(e.data)); });
    es.addEventListener('notice', function (e) { var n = JSON.parse(e.data); if (!me || n.who !== me.name) toast(n.text); });
    es.addEventListener('perm', function (e) {
      var d = JSON.parse(e.data);
      var before = perm;
      perm = d.perm; pending = d.pending;
      if (d.denied) toast('El anfitrión no te ha dado el control');
      else if (perm === 'control' && before !== 'control') toast('Ya puedes controlar la reproducción');
      else if (perm === 'view' && before === 'control') toast('El anfitrión ha recuperado el control');
      updatePerm();
    });
    es.addEventListener('closed', function (e) { finish(JSON.parse(e.data).text || 'La sala se ha cerrado'); });
    es.addEventListener('kicked', function (e) { finish(JSON.parse(e.data).text || 'Has salido de la sala'); });
    es.onerror = function () {
      if (ended) return;
      if (es.readyState === EventSource.CLOSED) {
        api('api/me').then(function () { setTimeout(connect, 2000); })
          .catch(function (err) { finish(err.status === 410 ? 'La sala está cerrada' : 'Ya no estás en la sala'); });
      }
    };
    api('api/me').then(function (d) { if (d.room && d.room.guests) renderGuests(d.room.guests); chatHistory(d); })
      .catch(function () {});
  }

  function finish(text) {
    ended = true;
    if (es) es.close();
    try { video.pause(); } catch (e) { /* nothing playing */ }
    if (hls) { hls.destroy(); hls = null; }
    message(text);
  }

  function renderGuests(list) {
    var ul = $('guests');
    ul.textContent = '';
    (list || []).forEach(function (g) {
      var li = document.createElement('li');
      if (!g.connected) li.className = 'off';
      if (me && g.id === me.id) li.classList.add('me');
      var n = document.createElement('span');
      n.textContent = g.name + (me && g.id === me.id ? ' (tú)' : '');
      var t = document.createElement('span');
      t.className = 'tag';
      t.textContent = g.perm === 'control' ? 'controla' : (g.pending ? 'pide el control' : (g.connected ? 'mira' : 'desconectado'));
      li.appendChild(n); li.appendChild(t);
      ul.appendChild(li);
    });
  }

  function renderViewers(v) {
    if (!v) return;
    $('viewers').textContent = v.count === 1 ? '1 persona viendo' : (v.count || 0) + ' personas viendo';
  }

  // -- chat and reactions (private rooms) ------------------------------------------------------------------

  function chatHistory(d) {
    if (d.reactions && !Object.keys(reactions).length) {
      reactions = d.reactions;
      var box = $('reactions');
      box.textContent = '';
      Object.keys(reactions).forEach(function (id) {
        var b = document.createElement('button');
        b.type = 'button';
        b.textContent = reactions[id];
        b.setAttribute('data-r', id);
        b.addEventListener('click', function () {
          api('api/react', { reaction: id }).catch(function (e) { toast(e.message); });
        });
        box.appendChild(b);
      });
    }
    (d.chat || []).forEach(addChat);
  }

  function addChat(row) {
    if (!row || !row.id || row.id <= lastChat) return;
    lastChat = row.id;
    if (row.kind === 'reaction') { floatReaction(row); return; }
    var li = document.createElement('li');
    if (row.host) li.classList.add('host');
    var who = document.createElement('span');
    who.className = 'who';
    who.textContent = row.who + (me && row.guest === me.id ? ' (tú)' : '');
    var text = document.createElement('span');
    text.className = 'text';
    text.textContent = row.text;       // plain text: markup is shown, never interpreted
    li.appendChild(who); li.appendChild(text);
    var ul = $('chat-list');
    ul.appendChild(li);
    while (ul.children.length > 60) ul.removeChild(ul.firstChild);
    ul.scrollTop = ul.scrollHeight;
  }

  function floatReaction(row) {
    var el = document.createElement('div');
    el.className = 'float';
    el.textContent = row.emoji || '';
    var small = document.createElement('small');
    small.textContent = row.who;
    el.appendChild(small);
    el.style.left = (10 + Math.random() * 75) + '%';
    $('floats').appendChild(el);
    setTimeout(function () { el.remove(); }, 3200);
  }

  $('chat-form').addEventListener('submit', function (ev) {
    ev.preventDefault();
    var text = $('chat-text').value.trim();
    if (!text) return;
    api('api/chat', { text: text }).then(function () { $('chat-text').value = ''; })
      .catch(function (e) { toast(e.message); });
  });

  function updatePerm() {
    var can = perm === 'control' && mode !== 'public';
    ['play', 'back10', 'fwd10', 'seek'].forEach(function (id) { $(id).disabled = !can; });
    show('ask', !can && mode !== 'public');
    $('ask').disabled = pending;
    $('ask').textContent = pending ? 'Esperando al anfitrión…' : 'Pedir el control';
  }

  // -- the host's state ----------------------------------------------------------------------------------

  function onState(st) {
    state = st;
    anchor = performance.now();
    $('title').textContent = st.title || '';
    $('play').textContent = st.paused ? 'Seguir' : 'Pausa';
    if (st.reason === 'seek' || st.reason === 'file' || st.reason === 'hello') correct(true);
    else correct(false);
  }

  function hostPos() { return S.expected(state, anchor, performance.now()); }

  function ready() {
    // seconds the relay has produced (Infinity for direct URLs and finished relays)
    if (!media) return 0;
    if (media.kind === 'direct' && !useRelay) return Infinity;
    var complete = useRelay ? media.relay_complete : media.complete;
    var r = useRelay ? media.relay_ready : media.ready;
    return complete ? Infinity : (r || 0);
  }

  function correct(force) {
    if (!state || !source || ended) return;
    if (state.idle) { overlay('El anfitrión no está reproduciendo nada'); return; }
    var exp = hostPos();
    if (exp > ready() - 1) {
      if (!video.paused) video.pause();
      var r = ready();
      overlay('Preparando la retransmisión… ' + S.clock(r) + ' de ' + S.clock(state.duration || exp));
      return;
    }
    if (needGesture) { overlay('La sala ya está en marcha', true); return; }
    overlay('');
    if (video.readyState < 1) return;  // metadata not loaded yet: loadedmetadata calls us again
    var c = S.correction(exp, video.currentTime, state.speed, state.paused);
    if (c.action === 'seek' || (force && Math.abs(c.diff) > 0.3)) {
      seeking = true;
      video.currentTime = exp;
    }
    video.playbackRate = c.rate;
    if (state.paused && !video.paused) video.pause();
    if (!state.paused && video.paused) {
      var p = video.play();
      if (p && p.catch) p.catch(function () { needGesture = true; overlay('Pulsa para empezar a ver', true); });
    }
  }

  setInterval(function () {
    if (!state) return;
    var pos = state.idle ? 0 : hostPos();
    $('pos').textContent = S.clock(pos);
    $('dur').textContent = S.clock(state.duration || 0);
    if (state.duration && !seekDragging) $('seek').value = Math.round(1000 * pos / state.duration);
    correct(false);
  }, 500);

  video.addEventListener('loadedmetadata', function () { correct(true); });
  video.addEventListener('seeked', function () { seeking = false; });
  video.addEventListener('error', function () {
    if (media && media.kind === 'direct' && !useRelay) {
      useRelay = true;
      overlay('Este navegador no puede abrir el vídeo directamente: preparando la retransmisión…');
      api('api/relay', {}).then(onMedia).catch(function (e) { overlay('No se puede reproducir: ' + e.message); });
    }
  });
  $('start').addEventListener('click', function () {
    needGesture = false;
    overlay('');
    video.play().catch(function () {});
    correct(true);
  });

  // -- what to play -----------------------------------------------------------------------------------------

  function onMedia(m) {
    media = m;
    var url = '';
    if (m.kind === 'hls') url = m.url;
    else if (m.kind === 'direct') url = useRelay ? (m.relay_url || '') : m.url;
    else if (m.kind === 'preparing') overlay('Preparando…');
    else if (m.kind === 'none') overlay(m.reason ? 'No se puede compartir esto: ' + m.reason : 'Nada en reproducción');
    if (m.kind === 'hls' && m.status === 'failed') overlay('La retransmisión ha fallado: ' + (m.error || ''));
    if (url && url !== source) attach(url);
    setSubs(m.subs);
    correct(false);
  }

  function loadHlsJs() {
    if (window.Hls) return Promise.resolve(window.Hls);
    if (hlsLoading) return hlsLoading;
    hlsLoading = new Promise(function (resolve, reject) {
      var s = document.createElement('script');
      s.src = '/static/hls.light.min.js';
      s.onload = function () { resolve(window.Hls); };
      s.onerror = reject;
      document.head.appendChild(s);
    });
    return hlsLoading;
  }

  function attach(url) {
    source = url;
    if (hls) { hls.destroy(); hls = null; }
    var isHls = /\.m3u8(\?|$)/.test(url);
    if (!isHls || (!forceHlsJs && video.canPlayType('application/vnd.apple.mpegurl'))) {
      video.src = url;
      video.load();
      return;
    }
    loadHlsJs().then(function (Hls) {
      if (source !== url) return;
      if (!Hls.isSupported()) { overlay('Este navegador no puede reproducir la retransmisión'); return; }
      hls = new Hls({ enableWorker: true, lowLatencyMode: false, backBufferLength: 60 });
      hls.on(Hls.Events.ERROR, function (_, data) {
        if (!data.fatal) return;
        if (data.type === Hls.ErrorTypes.NETWORK_ERROR) hls.startLoad();
        else if (data.type === Hls.ErrorTypes.MEDIA_ERROR) hls.recoverMediaError();
      });
      hls.loadSource(url);
      hls.attachMedia(video);
    }).catch(function () { overlay('No se pudo cargar el reproductor'); });
  }

  function setSubs(sub) {
    var url = sub && sub.url ? sub.url : '';
    show('subs', !!url);
    if (url === subsUrl) return;
    subsUrl = url;
    Array.prototype.slice.call(video.querySelectorAll('track')).forEach(function (t) { t.remove(); });
    if (!url) return;
    var tr = document.createElement('track');
    tr.kind = 'subtitles';
    tr.src = url;
    tr.label = sub.label || 'Subtítulos';
    if (sub.lang) tr.srclang = sub.lang.slice(0, 2);
    tr.default = true;
    video.appendChild(tr);
    setTimeout(function () { if (video.textTracks[0]) video.textTracks[0].mode = 'showing'; }, 0);
    $('subs').classList.add('on');
  }

  // -- controls ---------------------------------------------------------------------------------------------

  function cmd(body) { api('api/cmd', body).catch(function (e) { toast(e.message); }); }
  $('play').addEventListener('click', function () { cmd({ cmd: 'toggle' }); });
  $('back10').addEventListener('click', function () { cmd({ cmd: 'seek_rel', seconds: -10 }); });
  $('fwd10').addEventListener('click', function () { cmd({ cmd: 'seek_rel', seconds: 10 }); });
  var seekDragging = false;
  $('seek').addEventListener('input', function () { seekDragging = true; });
  $('seek').addEventListener('change', function () {
    seekDragging = false;
    if (state && state.duration) cmd({ cmd: 'seek', seconds: state.duration * $('seek').value / 1000 });
  });
  $('ask').addEventListener('click', function () {
    api('api/request', {}).then(function (d) { perm = d.perm; pending = d.pending; updatePerm(); })
      .catch(function (e) { toast(e.message); });
  });
  $('mute').addEventListener('click', function () {
    video.muted = !video.muted;
    $('mute').textContent = video.muted ? 'Activar sonido' : 'Silenciar';
  });
  $('volume').addEventListener('input', function () { video.volume = $('volume').value / 100; });
  $('subs').addEventListener('click', function () {
    var t = video.textTracks[0];
    if (!t) return;
    t.mode = t.mode === 'showing' ? 'hidden' : 'showing';
    $('subs').classList.toggle('on', t.mode === 'showing');
  });
  $('full').addEventListener('click', function () {
    var st = $('stage');
    if (document.fullscreenElement) document.exitFullscreen();
    else if (st.requestFullscreen) st.requestFullscreen();
    else if (video.webkitEnterFullscreen) video.webkitEnterFullscreen();
  });
  $('leave').addEventListener('click', function () {
    api('api/leave', {}).finally(function () { finish('Has salido de la sala. Puedes volver con el mismo enlace.'); });
  });

  // -- start ---------------------------------------------------------------------------------------------

  if (!roomId) { message('Enlace no válido'); return; }
  api('api/me').then(function (d) {
    enter(d.guest, false, d.room);
    if (d.media) onMedia(d.media);
    if (d.state) onState(d.state);
  }).catch(function (e) {
    if (e.status === 401) askName();
    else if (e.status === 410) message('La sala está cerrada o ha caducado. Pide un enlace nuevo.');
    else message('No se puede entrar: ' + e.message);
  });
})();
