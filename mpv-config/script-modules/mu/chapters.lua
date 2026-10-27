-- mu.chapters: the pure half of «Capítulos vía IA externa» (H75, ADR-134, docs/capitulos-ia/FICHA.md). Nothing in
-- here touches mp, so tests/test_mu_chapters.py calls every function through tests/fixtures/lua/chapters_probe.lua.
--
--   parse_srt(text)              → { { start =, stop =, text = }, … }
--   compact(blocks, opts)        → { lines = { '[mm:ss] text', … }, text =, long = }   what is sent to the AI
--   prompt(prompt, duration, lines), tokens(text), clock(seconds, long)
--   parse_reply(text)            → { { time =, title = }, … }   what the AI answered, read tolerantly
--   parse_answer(text)           → { chapters =, points = }      the same, plus how many key points came along
--   fit(list, opts)              → { chapters =, matched =, total =, suspicious = }
--   format(list, duration)       → 'MM:SS Title\n…'   the <video>.capitulos.txt that can be edited by hand
--
-- LuaJIT / Lua 5.1 / 5.2 (mpv is built with any of them): no utf8.*, no //, no goto. Multibyte characters are
-- compared as plain byte strings, never inside a pattern class.
local M = {}

local BOM = '\239\187\191'
local EN_DASH, EM_DASH = '\226\128\147', '\226\128\148'
local BULLET = '\226\128\162'
local ELLIPSIS = '\226\128\166'
local QUOTES = { '"', "'", '\194\171', '\194\187', '\226\128\156', '\226\128\157', '\226\128\152', '\226\128\153' }
-- What Whisper writes over silence or music. Lowercase: string.lower leaves accented letters alone.
local HALLUCINATIONS = { 'gracias por ver', 'suscríbete', 'suscribete', 'dale a la campanita', 'subtítulos por',
                         'subtitulos por', 'subtítulos realizados por', 'subtitulos realizados por', 'amara.org' }

local function trim(s) return (s:gsub('^%s+', ''):gsub('%s+$', '')) end

local function unprefix(s, p)
  if s:sub(1, #p) == p then return s:sub(#p + 1), true end
  return s, false
end

local function unsuffix(s, p)
  if #p <= #s and s:sub(-#p) == p then return s:sub(1, #s - #p), true end
  return s, false
end

local function lines_of(text)
  text = tostring(text or '')
  if text:sub(1, 3) == BOM then text = text:sub(4) end
  text = text:gsub('\r\n', '\n'):gsub('\r', '\n')
  local out = {}
  for line in (text .. '\n'):gmatch('([^\n]*)\n') do out[#out + 1] = line end
  return out
end

function M.clock(seconds, long)
  local t = math.max(0, math.floor(tonumber(seconds) or 0))
  if long then
    return string.format('%d:%02d:%02d', math.floor(t / 3600), math.floor(t % 3600 / 60), t % 60)
  end
  return string.format('%02d:%02d', math.floor(t / 60), t % 60)
end

-- -- SRT -------------------------------------------------------------------------------------------------------------

local function srt_seconds(h, m, s, frac)
  local f = (frac ~= '') and tonumber(frac) / (10 ^ #frac) or 0
  return tonumber(h) * 3600 + tonumber(m) * 60 + tonumber(s) + f
end

local function clean_cue(text)
  text = text:gsub('<[^>]*>', ''):gsub('{\\[^}]*}', '')
  return trim((text:gsub('%s+', ' ')))
end

function M.parse_srt(text)
  local blocks, cur = {}, nil
  local function flush()
    if cur then
      local t = clean_cue(table.concat(cur.lines, ' '))
      if t ~= '' then blocks[#blocks + 1] = { start = cur.start, stop = cur.stop, text = t } end
    end
    cur = nil
  end
  for _, line in ipairs(lines_of(text)) do
    local h1, m1, s1, f1, h2, m2, s2, f2 = line:match(
      '^%s*(%d+):(%d%d):(%d%d)[,%.]?(%d*)%s*%-%->%s*(%d+):(%d%d):(%d%d)[,%.]?(%d*)')
    if h1 then
      flush()
      cur = { start = srt_seconds(h1, m1, s1, f1:sub(1, 3)), stop = srt_seconds(h2, m2, s2, f2:sub(1, 3)),
              lines = {} }
    elseif trim(line) == '' then
      flush()
    elseif cur then
      cur.lines[#cur.lines + 1] = line
    end
  end
  flush()
  table.sort(blocks, function(a, b) return a.start < b.start end)
  return blocks
end

-- -- what is sent to the AI ------------------------------------------------------------------------------------------

local function hallucination(text)
  local low = text:lower()
  for _, p in ipairs(HALLUCINATIONS) do
    if low:find(p, 1, true) then return true end
  end
  return false
end

function M.compact(blocks, opts)
  blocks, opts = blocks or {}, opts or {}
  local window = tonumber(opts.line_seconds) or 12
  local last = blocks[#blocks]
  local long = (tonumber(opts.duration) or (last and last.start) or 0) >= 3600
  local lines, line_start, parts, prev = {}, nil, {}, nil
  local function flush()
    if line_start then lines[#lines + 1] = '[' .. M.clock(line_start, long) .. '] ' .. table.concat(parts, ' ') end
    line_start, parts = nil, {}
  end
  for _, b in ipairs(blocks) do
    local text = tostring(b.text or '')
    local key = text:lower()
    if text ~= '' and key ~= prev and not hallucination(text) then
      prev = key
      if line_start and b.start - line_start >= window then flush() end
      line_start = line_start or b.start
      parts[#parts + 1] = text
    end
  end
  flush()
  return { lines = lines, text = table.concat(lines, '\n'), long = long }
end

-- What is copied when only the prompt is asked for: the prompt, how long the video is, and the line where the
-- transcription goes — so the person pastes it right underneath, in the same message to the AI.
function M.prompt_head(prompt_text, duration)
  local head = tostring(prompt_text or ''):gsub('%s+$', '')
  return head .. '\n\nDURACIÓN TOTAL: ' .. M.clock(duration, true) .. '\n\nTRANSCRIPCIÓN:\n'
end

function M.prompt(prompt_text, duration, lines)
  return M.prompt_head(prompt_text, duration) .. table.concat(lines or {}, '\n')
end

-- Rough: ~4 bytes per token for Spanish or English text. Only shown as «~N mil tokens».
function M.tokens(text) return math.ceil(#tostring(text or '') / 4) end

-- -- what the AI answers ---------------------------------------------------------------------------------------------

-- '1:02:03…' or '7:42…' at the start of s → seconds, the rest. Minutes may pass 59 only when there are no hours.
local function read_time(s)
  local h, m, sec, rest = s:match('^(%d+):(%d%d):(%d%d)(.*)$')
  if h then
    h, m, sec = tonumber(h), tonumber(m), tonumber(sec)
    if m >= 60 or sec >= 60 then return nil end
    return h * 3600 + m * 60 + sec, rest
  end
  m, sec, rest = s:match('^(%d+):(%d%d)(.*)$')
  if not m then return nil end
  m, sec = tonumber(m), tonumber(sec)
  if sec >= 60 then return nil end
  return m * 60 + sec, rest
end

-- Strips the first matching prefix of `list` (one per call), then leading spaces.
local function strip_one(s, list)
  for _, p in ipairs(list) do
    local r, ok = unprefix(s, p)
    if ok then return (r:gsub('^%s+', '')), true end
  end
  return s, false
end

local LEAD = { '**', '__', '`', '-', '*', '+', BULLET, '[', '(' }
local CLOSE = { '**', '__', '`', ']', ')' }
local SEPARATORS = { '-', EN_DASH, EM_DASH, ':', '|' }

local function strip_lead(s)
  repeat
    local before = s
    s = s:gsub('^%s+', ''):gsub('^%d+[%.%)]%s+', '')
    s = strip_one(s, LEAD)
  until s == before
  return s
end

local function strip_after_time(rest)
  local changed
  repeat rest, changed = strip_one((rest:gsub('^%s+', '')), CLOSE) until not changed
  -- a range «00:00 - 07:42 Title»: the end of the range is not part of the title
  for _, d in ipairs({ '-', EN_DASH, EM_DASH }) do
    local r, ok = unprefix(rest, d)
    if ok then
      local t, after = read_time((r:gsub('^%s+', '')))
      if t then rest = after end
      break
    end
  end
  repeat rest, changed = strip_one((rest:gsub('^%s+', '')), CLOSE) until not changed
  repeat rest, changed = strip_one(rest, SEPARATORS) until not changed
  return rest
end

local function clean_title(t)
  t = t:gsub('%*%*', ''):gsub('__', ''):gsub('`', '')
  repeat
    local before = t
    t = trim(t)
    for _, q in ipairs(QUOTES) do t = (unsuffix((unprefix(t, q)), q)) end
    -- «?» and «!» stay: they go with their «¿» and «¡»
    for _, p in ipairs({ '.', ',', ';', ':', ELLIPSIS }) do t = (unsuffix(t, p)) end
  until t == before
  return t
end

-- H76 · la respuesta trae DOS bloques: los capítulos (los que van a la barra) y los puntos clave (que solo se
-- cuentan, para poder decir cuántos vinieron). Se lee en una pasada y se para en la primera línea que no es un
-- capítulo después de haber leído uno: ahí empieza el segundo bloque. Las vallas (```) no cuentan ni cierran.
function M.parse_answer(text)
  local chapters, points, closed = {}, 0, false
  for _, line in ipairs(lines_of(text)) do
    local s = trim(line)
    if s:sub(1, 3) ~= '```' then
      local t, rest = read_time(strip_lead(s))
      if not closed then
        local title = t and clean_title(strip_after_time(rest)) or ''
        if t and title ~= '' then
          chapters[#chapters + 1] = { time = t, title = title }
        elseif #chapters > 0 then
          closed = true
        end
      elseif t then
        points = points + 1
      end
    end
  end
  return { chapters = chapters, points = points }
end

function M.parse_reply(text) return M.parse_answer(text).chapters end

-- -- check and adjust ------------------------------------------------------------------------------------------------

function M.fit(list, opts)
  opts = opts or {}
  local duration = tonumber(opts.duration)
  local starts = (type(opts.starts) == 'table' and #opts.starts > 0) and opts.starts or nil
  local tolerance = tonumber(opts.tolerance) or 15
  local min_gap = tonumber(opts.min_gap) or 45

  local items = {}
  for i, c in ipairs(list or {}) do
    local t = tonumber(c.time)
    if t and t >= 0 and (not duration or t <= duration) then
      items[#items + 1] = { time = t, title = tostring(c.title or ''), order = i }
    end
  end

  -- quality check and snapping to the nearest cue start. 0 is neither: the prompt asks for it, it does not come
  -- from the transcript.
  local matched, total = 0, 0
  if starts then
    for _, c in ipairs(items) do
      if c.time > 0 then
        total = total + 1
        local best, dist = nil, nil
        for _, s in ipairs(starts) do
          local d = math.abs(s - c.time)
          if not dist or d < dist then best, dist = s, d end
        end
        if dist <= tolerance then
          matched = matched + 1
          c.time = best
        end
      end
    end
  end

  table.sort(items, function(a, b)
    if a.time ~= b.time then return a.time < b.time end
    return a.order < b.order
  end)
  local uniq = {}
  for _, c in ipairs(items) do
    if not uniq[#uniq] or uniq[#uniq].time ~= c.time then uniq[#uniq + 1] = c end
  end

  -- a first chapter a few seconds in IS the start; otherwise the start gets its own chapter
  if uniq[1] and uniq[1].time > 0 then
    if uniq[1].time <= tolerance then
      uniq[1].time = 0
    else
      table.insert(uniq, 1, { time = 0, title = opts.first_title or 'Inicio' })
    end
  end

  local chapters = {}
  for _, c in ipairs(uniq) do
    local prev = chapters[#chapters]
    if not prev or c.time - prev.time >= min_gap then chapters[#chapters + 1] = { time = c.time, title = c.title } end
  end
  return { chapters = chapters, matched = matched, total = total,
           suspicious = starts ~= nil and total > 0 and matched * 2 < total }
end

function M.format(list, duration)
  local long = (tonumber(duration) or 0) >= 3600
  for _, c in ipairs(list or {}) do
    if (tonumber(c.time) or 0) >= 3600 then long = true end
  end
  local out = {}
  for _, c in ipairs(list or {}) do out[#out + 1] = M.clock(c.time, long) .. ' ' .. tostring(c.title or '') .. '\n' end
  return table.concat(out)
end

return M
