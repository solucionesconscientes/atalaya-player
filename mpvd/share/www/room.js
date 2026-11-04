// «Ver juntos» (H25): the guest page. Proves it holds the token of the link (#k=…) with an HMAC challenge-response
// (the token never travels), then follows the host over SSE (/s/<room>/events) and keeps its own <video> in step
// (MuSync). The HLS relay is AES-128 encrypted (F4): hls.js gets the key from its own loader, derived in the page
// from the token with MuCrypt, and the key is never fetched. Private rooms add a chat and reactions (always drawn
// as text: textContent, never HTML). A public «solo ver» link (#k=…&v=1) joins by itself without a name.
(function () {
  'use strict';
  var S = window.MuSync;
  var $ = function (id) { return document.getElementById(id); };
  var roomId = (location.pathname.match(/^\/s\/([A-Za-z0-9_-]+)/) || [])[1] || '';
  var base = '/s/' + roomId + '/';
  var token = (location.hash.match(/[#&]k=([A-Za-z0-9_-]+)/) || [])[1] || '';
  var publicLink = /[#&]v=1\b/.test(location.hash);
  var tokenKey = 'mu-share-token-' + roomId;
  try {
    // the token leaves the address bar after joining; keep it for this tab so a reload can still decrypt the
    // relay and re-join if the short session credential expired. sessionStorage is neither a URL nor a header.
    if (token) {
      sessionStorage.setItem(tokenKey, token);
      sessionStorage.setItem(tokenKey + '-public', publicLink ? '1' : '0');
    } else if (sessionStorage.getItem(tokenKey)) {
      token = sessionStorage.getItem(tokenKey);
      publicLink = sessionStorage.getItem(tokenKey + '-public') === '1';
    }
  } catch (e) { /* storage disabled */ }
  var mode = publicLink ? 'public' : 'private', lastChat = 0, reactions = {};
  // ?poll=1: skip SSE (tests of the tunnel fallback, and a page behind a proxy that buffers event streams)
  var forcePoll = /[?&]poll=1\b/.test(location.search);
  var video = $('video');
  var me = null, perm = 'view', pending = false;
  var state = null, anchor = 0, media = null, source = '', subsUrl = '';
  var hls = null, hlsLoading = null, useRelay = false, es = null, needGesture = false, ended = false;
  var seeking = false;
  // F3 · the Cloudflare quick tunnel forwards a response's headers but holds its body until the response ends,
  // and an SSE stream never ends. If the first event has not arrived in 2.5 s the page follows the room by polling
  // once a second (the same api/me the page already uses). Local/LAN guests keep the SSE push.
  var transport = 'sse', helloTimer = null, pollTimer = null, pollFailures = 0, saturatedAt = 0;
  var lastMediaSig = '', lastNoticeAt = 0, firstPoll = true;

  function show(id, on) { $(id).classList.toggle('hidden', !on); }
  function message(text) { show('join', false); show('room', false); show('message', true); $('message-text').textContent = text; }

  function api(path, body, timeoutMs) {
    var opts = { method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin', headers: {} };
    if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
    var timer = null;
    if (timeoutMs && typeof AbortController !== 'undefined') {
      var ctl = new AbortController();
      opts.signal = ctl.signal;
      timer = setTimeout(function () { ctl.abort(); }, timeoutMs);
    }
    var done = function () { if (timer) { clearTimeout(timer); timer = null; } };
    return fetch(base + path, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok) { var e = new Error(data.error || t('HTTP %s', r.status)); e.status = r.status; throw e; }
        return data;
      });
    }).then(function (data) { done(); return data; },
            function (e) { done(); throw e; });
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
    if (!token) $('join-error').textContent = t('Abre el enlace de invitación completo que te han pasado.');
  }

  // F4 · prove possession of the fragment token without sending it: the server gives a nonce, the page answers
  // HMAC(token, "mu-share-v1|room|client|server") and gets a short-lived session credential.
  function randomNonce() {
    var b = new Uint8Array(16);
    if (window.crypto && crypto.getRandomValues) crypto.getRandomValues(b);
    else for (var i = 0; i < 16; i++) b[i] = Math.floor(Math.random() * 256);
    return MuCrypt.hex(b);
  }

  function join(name, gesture) {
    if (!token || !window.MuCrypt) {
      return Promise.reject(new Error(t('Abre el enlace de invitación completo que te han pasado.')));
    }
    var nonce = randomNonce();
    return api('api/challenge', { nonce: nonce }).then(function (d) {
      var proof = MuCrypt.proof(token, roomId, nonce, d.nonce);
      return api('api/join', { nonce: nonce, proof: proof, name: name });
    }).then(function (data) {
      history.replaceState(null, '', location.pathname + location.search);  // the token is not needed any more
      enter(data.guest, gesture, data.room);
    });
  }

  $('join-form').addEventListener('submit', function (ev) {
    ev.preventDefault();
    var name = $('name').value.trim();
    if (!name) return;
    $('join-error').textContent = '';
    join(name, true).then(function () {
      localStorage.setItem('mu-share-name', name);
    }).catch(function (e) { $('join-error').textContent = e.message; });
  });

  function autoJoin() {
    join('', false).catch(function (e) {
      message(e.status === 410 ? t('La sala está cerrada o ha caducado.') : t('No se puede entrar: %s', e.message));
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
    $('who').textContent = mode === 'public' ? t('Sala pública · solo ver') : t('Estás como %s', guest.name);
    updatePerm();
    connect();
  }

  // -- events ------------------------------------------------------------------------------------------

  function setTransport(name) {
    transport = name;
    document.documentElement.setAttribute('data-transport', name);
  }

  function connect() {
    if (transport === 'poll') return;   // a delayed SSE reconnect must not undo the polling fallback
    if (helloTimer) { clearTimeout(helloTimer); helloTimer = null; }
    if (es) es.close();
    es = null;
    setTransport('sse');
    if (forcePoll) { startPolling(); return; }
    var sawHello = false;
    helloTimer = setTimeout(function () {
      helloTimer = null;
      if (!sawHello && !ended) startPolling();
    }, 2500);
    es = new EventSource(base + 'events');
    es.addEventListener('hello', function (e) {
      sawHello = true;
      if (helloTimer) { clearTimeout(helloTimer); helloTimer = null; }
      var d = JSON.parse(e.data); me = d.guest; perm = me.perm; pending = me.pending; updatePerm(); setMode(d.room);
    });
    es.addEventListener('state', function (e) { onState(JSON.parse(e.data)); });
    es.addEventListener('media', function (e) { onMedia(JSON.parse(e.data)); });
    es.addEventListener('guests', function (e) { renderGuests(JSON.parse(e.data)); });
    es.addEventListener('viewers', function (e) { renderViewers(JSON.parse(e.data)); });
    es.addEventListener('chat', function (e) { addChat(JSON.parse(e.data)); });
    es.addEventListener('notice', function (e) { var n = JSON.parse(e.data); if (!me || n.who !== me.name) toast(n.text); });
    es.addEventListener('perm', function (e) { applyPerm(JSON.parse(e.data)); });
    es.addEventListener('closed', function (e) { finish(JSON.parse(e.data).text || t('La sala se ha cerrado')); });
    es.addEventListener('kicked', function (e) { finish(JSON.parse(e.data).text || t('Has salido de la sala')); });
    es.onerror = function () {
      if (ended || transport !== 'sse') return;
      if (es && es.readyState === EventSource.CLOSED) {
        api('api/me').then(function () { setTimeout(connect, 2000); })
          .catch(function (err) { finish(err.status === 410 ? t('La sala está cerrada') : t('Ya no estás en la sala')); });
      }
    };
    api('api/me').then(function (d) { if (d.room && d.room.guests) renderGuests(d.room.guests); chatHistory(d); })
      .catch(function () {});
  }

  // F3 · a tunneled guest: the page cannot receive SSE, so it asks api/me once a second. One request at a time
  // (the next starts when the previous finishes), which keeps this guest at a single request in flight.
  function startPolling() {
    if (ended || transport === 'poll') return;
    setTransport('poll');
    if (helloTimer) { clearTimeout(helloTimer); helloTimer = null; }
    if (es) { es.close(); es = null; }
    pollOnce();
  }

  function pollOnce() {
    if (ended || transport !== 'poll') return;
    var started = performance.now();
    api('api/me', undefined, 4000).then(function (d) {
      pollFailures = 0;
      applySnapshot(d);
    }).catch(function (e) {
      if (ended) return;
      if (e.status === 401 || e.status === 403) { finish(t('Ya no estás en la sala')); return; }
      if (e.status === 410) { finish(t('La sala está cerrada')); return; }
      pollFailures += 1;
      if (e.status === 429 || e.status === 503) {
        var now = performance.now();
        if (!saturatedAt || now - saturatedAt > 15000) {
          saturatedAt = now;
          toast(t('La sala va al límite de conexiones: la sincronía puede ir con retraso.'));
        }
      } else if (pollFailures === 5) {
        toast(t('Sin conexión con la sala; seguimos intentando.'));
      }
    }).then(function () {
      if (ended || transport !== 'poll') return;
      pollTimer = setTimeout(pollOnce, Math.max(150, 1000 - (performance.now() - started)));
    });
  }

  function applyPerm(d) {
    var before = perm, wasPending = pending;
    perm = d.perm; pending = d.pending;
    if (d.denied || (wasPending && !pending && perm !== 'control')) {
      toast(t('El anfitrión no te ha dado el control'));
    } else if (perm === 'control' && before !== 'control') {
      toast(t('Ya puedes controlar la reproducción'));
    } else if (perm === 'view' && before === 'control') {
      toast(t('El anfitrión ha recuperado el control'));
    }
    updatePerm();
  }

  function applySnapshot(d) {
    if (ended) return;
    if (d.guest) applyPerm({ perm: d.guest.perm, pending: d.guest.pending });
    if (d.room) {
      if (d.room.mode && d.room.mode !== mode) setMode(d.room);
      if (mode === 'public') renderViewers({ count: d.room.viewers, max: d.room.max_guests });
      else renderGuests(d.room.guests);
    }
    if (d.state && (!state || d.state.at !== state.at)) onState(d.state);
    if (d.media) {
      var sig = JSON.stringify(d.media);
      if (sig !== lastMediaSig) { lastMediaSig = sig; onMedia(d.media); }
    }
    if (d.notices) {
      var maxAt = 0;
      d.notices.forEach(function (n) {
        var at = n.at || 0;
        if (at > maxAt) maxAt = at;
        if (!firstPoll && at > lastNoticeAt && (!me || n.who !== me.name)) toast(n.text);
      });
      lastNoticeAt = Math.max(lastNoticeAt, maxAt);
    }
    firstPoll = false;
    chatHistory(d);
  }

  function finish(text) {
    ended = true;
    if (helloTimer) { clearTimeout(helloTimer); helloTimer = null; }
    if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
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
      n.textContent = me && g.id === me.id ? t('%s (tú)', g.name) : g.name;
      var etiqueta = document.createElement('span');
      etiqueta.className = 'tag';
      etiqueta.textContent = g.perm === 'control' ? t('controla')
        : (g.pending ? t('pide el control') : (g.connected ? t('mira') : t('desconectado')));
      li.appendChild(n); li.appendChild(etiqueta);
      ul.appendChild(li);
    });
  }

  function renderViewers(v) {
    if (!v) return;
    $('viewers').textContent = v.count === 1 ? t('1 persona viendo') : t('%s personas viendo', v.count || 0);
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
    who.textContent = me && row.guest === me.id ? t('%s (tú)', row.who) : row.who;
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
    // H55 · unos botones apagados sin decir por qué parecen rotos: con el control quitado se dice, y el botón de
    // pedirlo queda al lado.
    var why = $('why-view');
    if (why) {
      why.textContent = can ? '' : (mode === 'public'
        ? t('Esta sala es de solo ver: los mandos los lleva quien la ha abierto.')
        : t('Ahora mismo solo puedes ver. Pide el control para pausar y saltar.'));
      show('why-view', !can);
    }
    show('ask', !can && mode !== 'public');
    $('ask').disabled = pending;
    $('ask').textContent = pending ? t('Esperando al anfitrión…') : t('Pedir el control');
  }

  // -- the host's state ----------------------------------------------------------------------------------

  function onState(st) {
    state = st;
    anchor = performance.now();
    $('title').textContent = st.title || '';
    $('play').textContent = st.paused ? t('Seguir') : t('Pausa');
    if (st.reason === 'seek' || st.reason === 'file' || st.reason === 'hello') correct(true);
    else correct(false);
  }

  function hostPos() { return S.expected(state, anchor, performance.now()); }

  // H44/C5 · el relay ya no empieza en el segundo 0 del vídeo sino donde estaba el anfitrión al arrancarlo, así que
  // el reloj del vídeo que llega va `offset` segundos por detrás del reloj del anfitrión. Todo lo que compara los
  // dos tiene que descontarlo: antes el invitado que entraba en el minuto 40 esperaba a que el empaquetado
  // *alcanzara* su posición, y eso eran ~18 minutos medidos.
  function offset() {
    if (!media) return 0;
    if (media.kind === 'file') return useRelay ? (media.relay_offset || 0) : 0;
    if (media.kind === 'direct') return useRelay ? (media.relay_offset || 0) : 0;
    return media.offset || 0;
  }

  // segundo del vídeo que corresponde a la posición del anfitrión
  function localPos() { return Math.max(0, hostPos() - offset()); }

  function ready() {
    // seconds the relay has produced (Infinity for a direct URL, the original file and finished relays)
    if (!media) return 0;
    if (media.kind === 'file' && !useRelay) return Infinity;
    if (media.kind === 'direct' && !useRelay) return Infinity;
    var complete = useRelay ? media.relay_complete : media.complete;
    var r = useRelay ? media.relay_ready : media.ready;
    return complete ? Infinity : (r || 0);
  }

  function correct(force) {
    if (!state || !source || ended) return;
    if (state.idle) { overlay(t('El anfitrión no está reproduciendo nada')); return; }
    var exp = localPos();
    if (exp > ready() - 1) {
      if (!video.paused) video.pause();
      var r = ready();
      // con offset, lo que se está preparando empieza donde va el anfitrión: decirlo, en vez de dejar al invitado
      // mirando una cuenta que parece que no avanza nunca
      if (offset() > 1) {
        overlay(t('Empezamos donde va el anfitrión (%s): %s listos…', S.clock(offset()), S.clock(r)));
      } else {
        overlay(t('Preparando la retransmisión… %s de %s', S.clock(r), S.clock(state.duration || exp)));
      }
      return;
    }
    if (needGesture) { overlay(t('La sala ya está en marcha'), true); return; }
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
      if (p && p.catch) p.catch(function () { needGesture = true; overlay(t('Pulsa para empezar a ver'), true); });
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
      overlay(t('Este navegador no puede abrir el vídeo directamente: preparando la retransmisión…'));
      api('api/relay', {}).then(onMedia).catch(function (e) { overlay(t('No se puede reproducir: %s', e.message)); });
    }
  });
  $('start').addEventListener('click', function () {
    needGesture = false;
    overlay('');
    video.play().catch(function () {});
    correct(true);
  });

  // -- what to play -----------------------------------------------------------------------------------------

  // H51 · qué película es esta, para distinguir «la misma con más relay listo» de «el anfitrión ha cambiado de
  // película». Para un archivo es el testigo que manda el servidor (la URL es siempre /s/<sala>/file); para un
  // vídeo de internet, su dirección, que NO cambia cuando además se pide el relay.
  function mediaKey(m) { return (m.kind || '') + '|' + (m.v || m.url || ''); }
  var currentKey = '';

  function onMedia(m) {
    media = m;
    // Película nueva: las decisiones tomadas para la anterior no valen. Antes `useRelay` se ponía a true y no
    // volvía nunca: si después venía una película que el navegador SÍ abre, se buscaba un relay_url que ya no
    // existía, la dirección quedaba vacía y el invitado se quedaba con la película anterior para siempre.
    if (m.kind !== 'preparing' && mediaKey(m) !== currentKey) {
      currentKey = mediaKey(m);
      useRelay = (m.kind === 'file') && (m.browser !== 'direct');
      ended = false;
    }
    var url = '';
    if (m.kind === 'hls') url = m.url;
    // H44/C4 · un archivo del anfitrión: el original tal cual cuando este navegador puede con él, y si no el relay.
    // En los dos casos se ofrece además abrirlo en el reproductor del invitado (calidad original, saltos al instante).
    else if (m.kind === 'file') url = (!useRelay && m.browser === 'direct') ? m.url : (m.relay_url || '');
    else if (m.kind === 'direct') url = useRelay ? (m.relay_url || '') : m.url;
    // Un archivo del anfitrión hay que empaquetarlo para este navegador, y en un equipo modesto eso tarda: decir qué
    // está pasando, porque un «Preparando…» mudo no distingue «va» de «se ha roto».
    else if (m.kind === 'preparing') overlay(t('Preparando la retransmisión de «%s»… puede tardar un minuto',
                                               m.title || t('lo que está viendo')));
    else if (m.kind === 'none') overlay(m.reason ? t('No se puede compartir esto: %s', m.reason) : t('Nada en reproducción'));
    if (m.kind === 'hls' && m.status === 'failed') overlay(t('La retransmisión ha fallado: %s', m.error || ''));
    if (url && url !== source) attach(url);
    setSubs(m.subs);
    ownPlayer(m);
    correct(false);
  }

  // -- H44/C3 · «Abrir en mi reproductor» -------------------------------------------------------------------
  // Tres formas, porque cada sistema va mejor con una: copiar el enlace, bajar un .m3u (doble clic lo abre en VLC
  // o en mpv en Windows, macOS y Linux) y la línea para pegar en un terminal. Solo aparece cuando lo que se está
  // viendo es un archivo del anfitrión: un vídeo de YouTube, la TV o la radio no se pueden servir así.
  var fileLink = '';
  var fileFor = '';

  function ownPlayer(m) {
    // H51 · la TV y los vídeos de internet se abren en tu reproductor con su dirección original. F4: la
    // retransmisión HLS ya no, va cifrada y su clave solo la sabe derivar esta página.
    var on = m && (m.kind === 'file' || m.kind === 'direct');
    show('own', !!on);
    if (!on) { fileLink = ''; fileFor = ''; return; }
    $('own-name').textContent = m.name || m.title || '';
    if (fileFor !== mediaKey(m)) {
      // el enlace lleva una credencial de ESTE invitado (mpv y VLC no mandan la cookie de la sala), así que no
      // puede ir en el aviso que se reparte a todos: se pide aquí, una vez por archivo.
      fileFor = mediaKey(m);
      api('api/filelink').then(function (r) {
        fileLink = r.url || '';
        $('own-cmd').textContent = 'mpv "' + fileLink + '"';
        $('own-m3u').href = r.m3u || '#';
      }).catch(function () { fileFor = ''; show('own', false); });
    }
    $('own-why').textContent = whyOwnPlayer(m);
  }

  // Por qué merece la pena llevárselo a tu reproductor, que no es lo mismo según lo que esté puesto.
  function whyOwnPlayer(m) {
    if (m.kind === 'direct') return t('El vídeo original de la web, directo: ni pasa por el equipo del anfitrión.');
    if (m.browser !== 'direct') {
      return t('Este vídeo no está en un formato que tu navegador abra tal cual, así que aquí lo ves recomprimido.')
        + ' ' + t('En tu reproductor lo verás como es.');
    }
    return t('Calidad original, sin recomprimir nada, y los saltos son instantáneos.');
  }

  function copyText(text, what) {
    var done = function () { toast(t('Copiado: %s', what)); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, function () { toast(text); });
      return;
    }
    var ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); done(); } catch (e) { toast(text); }
    document.body.removeChild(ta);
  }

  $('own-copy').addEventListener('click', function () { copyText(fileLink, t('el enlace del vídeo')); });
  $('own-copy-cmd').addEventListener('click', function () { copyText('mpv "' + fileLink + '"', t('la orden de mpv')); });
  $('own-copy-pos').addEventListener('click', function () {
    copyText(S.clock(state ? hostPos() : 0), t('la posición del anfitrión'));
  });

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

  // F4 · the relay's AES-128 key never comes from a URL: this loader answers hls.js's key request with the key
  // derived in the page from the invitation token. If there is no token (link stripped after a lost session) the
  // video is not shown and the reason is said out loud.
  function keyLoader(Hls) {
    var Base = Hls.DefaultConfig.loader;
    function MuLoader(config) { Base.call(this, config); }
    MuLoader.prototype = Object.create(Base.prototype);
    MuLoader.prototype.constructor = MuLoader;
    MuLoader.prototype.load = function (context, config, callbacks) {
      if (context.type !== 'key') return Base.prototype.load.call(this, context, config, callbacks);
      if (!token || !window.MuCrypt) {
        callbacks.onError({ code: 0, text: t('No se pudo preparar la clave de la retransmisión') }, context, null, null);
        return;
      }
      var bytes = MuCrypt.utf8(MuCrypt.hlsKey(token, roomId));
      callbacks.onSuccess({ url: context.url, data: bytes.buffer }, { trequest: 0, tfirst: 0, tload: 0,
        loaded: bytes.length, total: bytes.length }, context, null);
    };
    return MuLoader;
  }

  function attach(url) {
    source = url;
    if (hls) { hls.destroy(); hls = null; }
    var isHls = /\.m3u8(\?|$)/.test(url);
    if (!isHls) {
      video.src = url;
      video.load();
      return;
    }
    // Native HLS (Safari/iOS) cannot use a custom key loader, and every relay is encrypted: always hls.js.
    loadHlsJs().then(function (Hls) {
      if (source !== url) return;
      if (!Hls.isSupported()) { overlay(t('Este navegador no puede reproducir la retransmisión')); return; }
      hls = new Hls({ enableWorker: true, lowLatencyMode: false, backBufferLength: 60, loader: keyLoader(Hls) });
      hls.on(Hls.Events.ERROR, function (_, data) {
        if (data.details === Hls.ErrorDetails.KEY_LOAD_ERROR) {
          overlay(t('No se pudo descifrar la retransmisión. Vuelve a abrir el enlace de invitación.'));
          if (hls) { hls.destroy(); hls = null; }
          return;
        }
        if (!data.fatal) return;
        if (data.type === Hls.ErrorTypes.NETWORK_ERROR) hls.startLoad();
        else if (data.type === Hls.ErrorTypes.MEDIA_ERROR) hls.recoverMediaError();
      });
      hls.loadSource(url);
      hls.attachMedia(video);
    }).catch(function () { overlay(t('No se pudo cargar el reproductor')); });
  }

  function setSubs(sub) {
    var url = sub && sub.url ? sub.url : '';
    show('subs', !!url);
    if (url === subsUrl) return;
    subsUrl = url;
    Array.prototype.slice.call(video.querySelectorAll('track')).forEach(function (pista) { pista.remove(); });
    if (!url) return;
    var tr = document.createElement('track');
    tr.kind = 'subtitles';
    tr.src = url;
    tr.label = sub.label || t('Subtítulos');
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
    $('mute').textContent = video.muted ? t('Activar sonido') : t('Silenciar');
  });
  $('volume').addEventListener('input', function () { video.volume = $('volume').value / 100; });
  $('subs').addEventListener('click', function () {
    var pista = video.textTracks[0];
    if (!pista) return;
    pista.mode = pista.mode === 'showing' ? 'hidden' : 'showing';
    $('subs').classList.toggle('on', pista.mode === 'showing');
  });
  $('full').addEventListener('click', function () {
    var st = $('stage');
    if (document.fullscreenElement) document.exitFullscreen();
    else if (st.requestFullscreen) st.requestFullscreen();
    else if (video.webkitEnterFullscreen) video.webkitEnterFullscreen();
  });
  $('leave').addEventListener('click', function () {
    api('api/leave', {}).finally(function () { finish(t('Has salido de la sala. Puedes volver con el mismo enlace.')); });
  });

  // -- start ---------------------------------------------------------------------------------------------

  if (!roomId) { message(t('Enlace no válido')); return; }
  api('api/me').then(function (d) {
    enter(d.guest, false, d.room);
    if (d.media) { lastMediaSig = JSON.stringify(d.media); onMedia(d.media); }
    if (d.state) onState(d.state);
  }).catch(function (e) {
    if (e.status === 401) askName();
    else if (e.status === 410) message(t('La sala está cerrada o ha caducado. Pide un enlace nuevo.'));
    else message(t('No se puede entrar: %s', e.message));
  });
})();
