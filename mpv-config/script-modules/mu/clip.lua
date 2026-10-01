-- mu.clip: the one place that puts text in the system clipboard. mpv 0.41 has a native `clipboard/text` property
-- (Wayland, X11, Windows and macOS); when the build or the session has no backend, the usual command line helpers
-- are tried in order, asynchronously, so the Lua thread never blocks.
-- H42/A4: every link that has to travel to another device is copied through here. Before this module mu-iptv,
-- mu-remote and mu-share each carried their own copy of the same code.
local mp = require('mp')

local M = {}

local function helpers()
  local platform = mp.get_property_native('platform')
  if platform == 'windows' then return { { 'cmd', '/c', 'clip' } } end
  if platform == 'darwin' then return { { 'pbcopy' } } end
  return { { 'wl-copy' }, { 'xclip', '-selection', 'clipboard' }, { 'xsel', '--clipboard', '--input' } }
end

-- copy(text[, done]) → true when mpv's own clipboard took the text right away (nothing else is run). Otherwise the
-- helpers are started and `done(ok)` is called when one of them succeeds or all of them fail. `done` runs exactly
-- once either way, so a caller can show the same notice on both paths.
function M.copy(text, done)
  text = tostring(text or '')
  local function finish(ok)
    if done then done(ok) end
    return ok
  end
  if text == '' then return finish(false) end
  local called, native = pcall(mp.set_property, 'clipboard/text', text)
  if called and native then return finish(true) end
  local candidates = helpers()
  local function try(i)
    local args = candidates[i]
    if not args then finish(false) return end
    mp.command_native_async({ name = 'subprocess', args = args, stdin_data = text, playback_only = false,
                              capture_stdout = true, capture_stderr = true }, function(ok, res)
      if ok and res and res.status == 0 then finish(true) else try(i + 1) end
    end)
  end
  try(1)
  return false
end

-- The notice to show on the OSD: the same wording everywhere, and when there is no clipboard at all the text
-- itself, so that it can still be read off the screen.
function M.notice(ok, text, what)
  if ok then return 'Copiado: ' .. (what or text) end
  return 'Sin portapapeles · ' .. tostring(text or '')
end

-- copy(text) plus the notice through the caller's own `osd` function (every mu-* script has one, with its own
-- duration). `what` replaces the text in the notice when the text itself is too long to read.
function M.copy_osd(text, osd, what)
  return M.copy(text, function(ok) osd(M.notice(ok, text, what)) end)
end

return M
