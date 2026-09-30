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

  // Position of the host now: the last state plus the time since it arrived (the page's own clock, so the two
  // devices never need to agree on the time of day).
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

  var api = { expected: expected, correction: correction, clock: clock, HARD: HARD, SOFT: SOFT, MAX_ADJ: MAX_ADJ };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.MuSync = api;
})(this);
