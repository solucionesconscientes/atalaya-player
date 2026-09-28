-- luacheck config for the project's own Lua (mpv scripts, LuaJIT/5.1).
std = 'luajit'
max_line_length = 120
exclude_files = {
  'mpv-config/scripts/uosc',      -- vendored (see vendor.lock)
  'mpv-config/scripts/thumbfast.lua',
  'vendor', '.venv', 'tmp', '.cache', 'logs',
}
