-- mu-core: bridge between mpv and the mpvd companion daemon.
-- H0: skeleton. Publishes its state under user-data/mu/core and detects uosc.
-- H1 will add: spawning mpvd, session registration, JSON-RPC calls, retries.
local mp = require('mp')
local msg = require('mp.msg')
local utils = require('mp.utils')

local VERSION = '0.1.0'

-- Project root: MPV_UOS_ROOT (exported by bin/mpv-uos) or the parent of --config-dir.
local function detect_root()
  local env = os.getenv('MPV_UOS_ROOT')
  if env and env ~= '' then return env end
  local cfg = mp.get_property('config-dir')
  if cfg and cfg ~= '' then
    local parent = utils.split_path(cfg)
    return (parent:gsub('[/\\]+$', ''))
  end
  return ''
end

local state = {
  version = VERSION,
  root = detect_root(),
  ipc = mp.get_property('input-ipc-server') or '',
  platform = mp.get_property_native('platform') or 'unknown',
  uosc = false,
  uosc_version = '',
  mpvd = 'disconnected',
}

local function publish()
  mp.set_property_native('user-data/mu/core', state)
end

-- uosc broadcasts `script-message uosc-version <v>` when it loads...
mp.register_script_message('uosc-version', function(v)
  state.uosc = true
  state.uosc_version = v or ''
  publish()
end)

-- ...and also sets user-data/osc/margins, which we observe in case the broadcast raced our load.
mp.observe_property('user-data/osc/margins', 'native', function(_, value)
  if value ~= nil and not state.uosc then
    state.uosc = true
    publish()
  end
end)

publish()
msg.info(string.format('mu-core %s · root=%s · ipc=%s · platform=%s', VERSION, state.root, state.ipc, state.platform))
