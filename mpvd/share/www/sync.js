// Drift correction of «Ver juntos» (H25), shared by the page and the tests (node): where the host is now, and what
// the guest's <video> should do about the difference. A big gap is a jump (seek); a small one is absorbed by
// playing a little faster or slower for a while (no visible jump).
(function (root) {
  'use strict';
  var HARD = 1.5;      // seconds: jump
  var SOFT = 0.15;     // seconds: below this, leave it
  var MAX_ADJ = 0.08;  // at most ±8 % of speed while catching up
  var GAIN = 0.5;      // speed change per second of difference
  var PAUSED_TOL = 0.3;

  // F5 · host clock − ours, from a request we sent and whose answer carries the host's `now`: the midpoint of our
  // send and receive times is the fairest estimate of when it answered. The fastest round trip is the least
  // noisy, so it wins; a one-way state stamp cannot know the latency, only a round trip can.
  function clockFrom(serverNow, sentMs, gotMs, clock) {
    var rtt = gotMs - sentMs;
    if (clock.rtt === null || clock.rtt === undefined || rtt < clock.rtt) {
      clock.offset = serverNow - (sentMs + gotMs) / 2000;
      clock.rtt = rtt;
    }
  }

  // F5 · when, on our clock, the host was at state.pos. `state.at` is the host's clock when it read that position;
  // anchoring at arrival instead left the guest behind by the transport delay for ever (1–2 s with a poll, 4,5 s
  // through the tunnel). Without a round trip yet, transit only ever subtracts from (at − our clock), so the
  // largest candidate seen is the closest offset we have.
  function anchor(state, nowMs, clock) {
    var at = Number(state && state.at) || 0;
    if (!at) return nowMs;
    var candidate = at - nowMs / 1000;
    if (clock.seen === null || clock.seen === undefined || candidate > clock.seen) clock.seen = candidate;
    var offset = clock.offset === null || clock.offset === undefined ? clock.seen : clock.offset;
    return nowMs - Math.max(0, nowMs / 1000 + offset - at) * 1000;
  }

  // Position of the host now: the last state plus the time since that position was read (the page's own clock,
  // so the two devices never need to agree on the time of day).
  function expected(state, anchorMs, nowMs) {
    if (!state) return null;
    var pos = Number(state.pos) || 0;
    if (!state.paused) pos += Math.max(0, (nowMs - anchorMs) / 1000) * (Number(state.speed) || 1);
    if (state.duration && pos > state.duration) pos = Number(state.duration);
    return pos;
  }

  // {action: 'none' | 'rate' | 'seek', rate, to?, diff}; diff > 0 means the guest is behind.
  function correction(expectedPos, currentTime, speed, paused) {
    speed = Number(speed) || 1;
    var diff = expectedPos - currentTime;
    var abs = Math.abs(diff);
    if (paused) {
      return abs > PAUSED_TOL ? { action: 'seek', to: expectedPos, rate: speed, diff: diff }
                              : { action: 'none', rate: speed, diff: diff };
    }
    if (abs > HARD) return { action: 'seek', to: expectedPos, rate: speed, diff: diff };
    if (abs > SOFT) {
      var adj = Math.max(-MAX_ADJ, Math.min(MAX_ADJ, diff * GAIN));
      return { action: 'rate', rate: speed * (1 + adj), diff: diff };
    }
    return { action: 'none', rate: speed, diff: diff };
  }

  function clock(s) {
    s = Math.max(0, Math.floor(Number(s) || 0));
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    var mm = h ? String(m).padStart(2, '0') : String(m);
    return (h ? h + ':' : '') + mm + ':' + String(sec).padStart(2, '0');
  }

  var api = { clockFrom: clockFrom, anchor: anchor, expected: expected, correction: correction, clock: clock,
              HARD: HARD, SOFT: SOFT, MAX_ADJ: MAX_ADJ };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.MuSync = api;
})(this);
