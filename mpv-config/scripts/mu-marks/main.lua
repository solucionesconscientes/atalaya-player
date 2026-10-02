-- mu-marks (H52): the one owner of mpv's `chapter-list`.
--
-- The timeline of uosc draws a diamond per chapter and colours the ranges whose title matches
-- `chapter_range_patterns`, so writing chapters is how anything of ours gets DRAWN ON THE TIMELINE without
-- touching uosc. The catch: `chapter-list` is a single property and **every mpv script runs in its own Lua
-- state**, so two scripts writing it overwrite each other and neither can see it coming. Before this script
-- mu-subs wrote it directly; now it asks here, and so do the segments (mu-cut) and the notes (mu-notes).
--
--   script-message-to mu_marks mu-marks-set <source> <json>
--       json = { mode = 'add' | 'instead', marks = { { time = <s>, title = <text> }, … } }
--   script-message-to mu_marks mu-marks-clear <source>
--
-- `add` (the default) keeps the film's own chapters; `instead` replaces them while that source is alive (what the
-- AI chapters of mu-subs do). The originals are remembered and put back when the source clears or the file changes.
local mp = require('mp')
local msg = require('mp.msg')
local utils = require('mp.utils')

local ORDER = { 'subs', 'cut', 'notes' }      -- fixed order so `instead` and ties never depend on table iteration

local originals = nil        -- the film's own chapters, captured before anyone writes
local sources = {}           -- name -> { mode = , marks = { {time=, title=} } }
local applied = false        -- have WE written the list? While false, mpv's own list is left strictly alone.

local function publish()
  local counts = {}
  local total = 0
  for _, name in ipairs(ORDER) do
    local n = sources[name] and #sources[name].marks or 0
    counts[name] = n
    total = total + n
  end
  mp.set_property_native('user-data/mu/marks', {
    total = total, counts = counts, originals = originals and #originals or 0,
  })
end

local function rebuild()
  local any = false
  for _, name in ipairs(ORDER) do
    if sources[name] and #sources[name].marks > 0 then any = true end
  end
  -- Con nada que añadir NO se toca `chapter-list`. Se tocaba, y eso perdía capítulos de la película: mpv publica
  -- la lista un poco después de cargar el archivo, así que un «no tengo marcas» que llegara antes escribía encima
  -- una copia a medias (o vacía) de lo que mpv aún no había terminado de leer.
  if not any then
    if applied then
      applied = false
      mp.set_property_native('chapter-list', originals or {})
    end
    publish()
    return
  end
  applied = true
  local base = originals or {}
  for _, name in ipairs(ORDER) do
    local src = sources[name]
    if src and src.mode == 'instead' and #src.marks > 0 then base = {} end
  end
  -- Ojo con los tiempos negativos: hay ficheros cuyo primer capítulo empieza en -0,02 s (el chapters.mkv de las
  -- pruebas, sin ir más lejos) y mpv DESCARTA en silencio un capítulo con tiempo negativo al escribir la lista.
  -- Copiarlo tal cual perdía ese capítulo; un capítulo no puede empezar antes del fichero, así que se lleva a 0.
  local list = {}
  for _, c in ipairs(base) do list[#list + 1] = { title = c.title, time = math.max(0, c.time or 0) } end
  for _, name in ipairs(ORDER) do
    local src = sources[name]
    for _, m in ipairs(src and src.marks or {}) do
      if type(m.time) == 'number' then
        list[#list + 1] = { title = tostring(m.title or ''), time = math.max(0, m.time) }
      end
    end
  end
  table.sort(list, function(a, b) return (a.time or 0) < (b.time or 0) end)
  mp.set_property_native('chapter-list', list)
  publish()
end

local function set(name, json)
  if not name or name == '' then return end
  local data = utils.parse_json(json or '') or {}
  local marks = {}
  for _, m in ipairs(type(data.marks) == 'table' and data.marks or {}) do
    if type(m) == 'table' and type(m.time) == 'number' then
      marks[#marks + 1] = { time = m.time, title = tostring(m.title or '') }
    end
  end
  if #marks == 0 then
    sources[name] = nil
  else
    sources[name] = { mode = data.mode == 'instead' and 'instead' or 'add', marks = marks }
  end
  rebuild()
end

local function clear(name)
  if name and name ~= '' then sources[name] = nil else sources = {} end
  rebuild()
end

-- The film's own chapters. mpv publishes chapter-list a little after loading the file —and more than once for
-- some formats— so this follows the property until we have something of our own to add; from then on the list is
-- ours and what comes back is our own write.
local function capture(list)
  if applied then return end
  originals = list or {}
  publish()
end

mp.register_event('start-file', function()
  sources = {}
  originals = nil
  applied = false
  publish()
end)

mp.observe_property('chapter-list', 'native', function(_, list) capture(list) end)

mp.register_script_message('mu-marks-set', set)
mp.register_script_message('mu-marks-clear', clear)

publish()
msg.info('mu-marks loaded')
