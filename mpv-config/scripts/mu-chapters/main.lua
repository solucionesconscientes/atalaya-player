-- mu-chapters: «Capítulos vía IA externa» (H75, ADR-134, docs/capitulos-ia/FICHA.md). Nothing runs a model here:
--   · chapters-copy (ctrl+alt+c): only the prompt (plus the video's duration) → the clipboard. The person pastes it
--     into whatever AI they like, with the transcription underneath.
--   · chapters-copy-transcript: the SRT next to the video, compacted, no prompt → the clipboard.
--   · chapters-copy-all (ctrl+alt+c cuando el vídeo trae subtítulos): the prompt, a blank line and the transcription
--     in one go → the clipboard. Es lo que se pega en la IA de una vez (H76).
--   · chapters-paste (ctrl+alt+p): the AI's answer from the clipboard → checked, snapped to the SRT, saved as
--     <video>.capitulos.txt (editable; the previous one kept as .bak) and drawn on uosc's timeline. De la respuesta
--     se queda con el BLOQUE 1 (capítulos); los puntos clave solo se cuentan para decirlo (H76).
--   · chapters-remove (ctrl+alt+d): the other way round: takes them off the timeline (and deletes the file next to
--     the video, after asking), so a video the AI got wrong can be done again from scratch.
--   · On opening a local file, its <video>.capitulos.txt (if any) is loaded at once. Nothing is ever written just
--     by opening or playing; streams (IPTV, radio, links) are left alone.
-- The prompt that is copied comes from prompt-ia.txt next to this module's (prompt_file), editable; the copy inside
-- is the fallback.
-- The parsing lives in mu.chapters (pure, tested apart). `chapter-list` is written only by mu-marks (H52): this is
-- its source `chapters`, in `instead` mode. Clipboard: mpv's own `clipboard/text` and nothing else (ADR-134).
-- Script name: mu_chapters. State for the tests: user-data/mu/chapters.
local mp = require('mp')
local msg = require('mp.msg')
local utils = require('mp.utils')
local options = require('mp.options')
package.path = mp.command_native({ 'expand-path', '~~/script-modules/?.lua' }) .. ';' .. package.path
local chapters = require('mu.chapters')
local tr = require('mu.i18n').t

local opts = {
  respect_existing = true,   -- a file that brings its own chapters keeps them
  min_gap = 45,              -- seconds between two chapters
  tolerance = 15,            -- seconds a time may move to reach the start of a subtitle cue
  line_seconds = 12,         -- seconds of speech per line of the compacted transcript
  confirm_seconds = 30,      -- how long the «times do not look like this transcript» question waits
  osd_seconds = 5,
  prompt_file = '~~/script-modules/mu/prompt-ia.txt',   -- editable; the built-in prompt is the fallback
  open_command = '',         -- programa que abre una dirección ('' = xdg-open / start / open); los tests usan `true`
  test_clipboard = false,    -- tests: user-data/mu/chapters-clipboard stands in for the system clipboard
}
options.read_options(opts, 'mu-chapters')

-- docs/capitulos-ia/prompt-ia.txt, with its long lines wrapped (line breaks inside a paragraph do not matter to an AI)
local DEFAULT_PROMPT = [[
Eres un editor de vídeo experto en estructurar contenido. Después de estas instrucciones te paso la transcripción de
un vídeo o audio, con una marca de tiempo [mm:ss] o [h:mm:ss] al inicio de cada línea. Tu tarea es dividirlo en
capítulos y señalar sus puntos clave.

REGLAS
1. Cada capítulo marca un cambio real de tema o de idea, no un resumen cada pocos minutos. Cantidad: aproximadamente
uno cada 5-8 minutos; mínimo 4 y máximo 20.
2. El tiempo de cada capítulo debe copiarse EXACTAMENTE de una marca de tiempo que exista en la transcripción: la de
la línea donde empieza ese tema. Nunca calcules, redondees ni inventes tiempos. Si la transcripción no trae ninguna
marca de tiempo, reparte los capítulos por proporción del texto y redondea al minuto.
3. El primer capítulo empieza en 00:00. Entre un capítulo y el siguiente debe haber al menos 45 segundos. Orden
cronológico.
4. Título: máximo 7 palabras, concreto e informativo (qué se explica, se decide o se demuestra). Evita títulos vacíos
como "Introducción" o "Conclusión" salvo que realmente lo sean. Escríbelo en el mismo idioma que la transcripción, sin
emojis, sin comillas y sin signos al final.
5. Ignora saludos, publicidad, peticiones de suscripción y repeticiones, salvo que formen una sección con contenido
propio.
6. La transcripción puede tener errores de reconocimiento de voz; interprétalos por el contexto.
7. La transcripción puede venir con el tiempo en una línea aparte (encima del texto) o en párrafos largos: úsalo
igual, sin pedirme nada más.

FORMATO DE SALIDA (estricto)
Responde SOLO con los dos bloques de abajo, en este orden y sin texto antes ni después. Pon cada bloque dentro de un
bloque de código.

BLOQUE 1 — CAPÍTULOS
Una línea por capítulo, con este formato:
MM:SS Título
Si el vídeo dura más de una hora, usa H:MM:SS en todas las líneas.

BLOQUE 2 — PUNTOS CLAVE
De 8 a 12 viñetas, una idea por viñeta, con este formato:
- MM:SS — una frase

Ejemplo del formato (no copies el contenido):
BLOQUE 1 — CAPÍTULOS
00:00 Título del primer tema
07:42 Título del segundo tema
BLOQUE 2 — PUNTOS CLAVE
- 00:30 — Lo que se cuenta al principio
- 08:15 — Lo que se decide en el segundo tema

Antes de responder, comprueba que cada tiempo existe en la transcripción (o cae donde toca si no había marcas), que
están en orden y que el primero es 00:00.
]]

local state = { path = '', status = 'idle', osd = '', count = 0, source = '', file = '', pending = nil,
                transcript = nil, text_ready = false, text_cues = 0, text_lang = '' }

local function publish()
  local t = state.transcript
  mp.set_property_native('user-data/mu/chapters', {
    path = state.path, status = state.status, osd = state.osd, count = state.count, source = state.source,
    file = state.file,
    -- F5: si hay transcripción que copiar (el .srt de al lado o el que dejó «Conseguir la transcripción»), para que
    -- el menú enseñe «Copiar la transcripción» en vez de «Conseguirla». H76: con el idioma, para poder decir
    -- «Subtítulos en español · N líneas» sin que el menú vuelva a leer el archivo.
    text = { ready = state.text_ready, cues = state.text_cues, lang = state.text_lang or '' },
    -- F4: lo que va pasando con «conseguir la transcripción», para el menú y los avisos (lo hace mu-subs)
    transcript = t and { status = t.status, source = t.source, progress = t.progress or 0, lines = t.lines or 0,
                         chars = t.chars or 0, out = t.out or '', error = t.error or '' } or nil,
  })
end
local function osd(text)
  mp.osd_message(text, opts.osd_seconds)
  state.osd = text
  publish()
end

-- -- clipboard -------------------------------------------------------------------------------------------------------

local CLIPBOARD_TEST = 'user-data/mu/chapters-clipboard'

local function clipboard_set(text)
  if opts.test_clipboard then return mp.set_property_native(CLIPBOARD_TEST, text) == true end
  return mp.set_property('clipboard/text', text) == true
end

local function clipboard_get()
  local text
  if opts.test_clipboard then text = mp.get_property_native(CLIPBOARD_TEST) else text = mp.get_property('clipboard/text') end
  return type(text) == 'string' and text or ''
end

-- -- files -----------------------------------------------------------------------------------------------------------

local function read_file(path)
  local f = io.open(path, 'rb')
  if not f then return nil end
  local data = f:read('*a')
  f:close()
  return data
end

-- What is copied in front of the transcript: `prompt_file`, editable, read on every copy so a change applies at once.
-- If it is missing or empty (or somebody deletes it), the prompt of the script is used instead.
local function prompt_text()
  local path = mp.command_native({ 'expand-path', opts.prompt_file })
  local text = path and path ~= '' and read_file(path) or nil
  if text and text:find('%S') then return text end
  return DEFAULT_PROMPT
end

-- The video being played, when it is a file of this computer with a known duration: { path, dir, stem, duration }.
local function local_video()
  local path = mp.get_property('path') or ''
  if path == '' or path:find('://', 1, true) then return nil end
  local duration = mp.get_property_number('duration') or 0
  local info = utils.file_info(path)
  if duration <= 0 or not info or not info.is_file then return nil end
  local dir, file = utils.split_path(path)
  return { path = path, dir = dir, stem = (file:gsub('%.[^%.]*$', '')), duration = duration }
end

local function chapters_file(v) return utils.join_path(v.dir, v.stem .. '.capitulos.txt') end

-- <video>.srt, or else the first <video>.<anything>.srt (mpvd saves them as <video>.<lang>.srt).
local function find_srt(v)
  local names = utils.readdir(v.dir, 'files') or {}
  table.sort(names)
  local exact = v.stem .. '.srt'
  local prefix = v.stem .. '.'
  local found = nil
  for _, name in ipairs(names) do
    if name == exact then return utils.join_path(v.dir, name) end
    if not found and #name > #prefix + 4 and name:sub(1, #prefix) == prefix and name:sub(-4):lower() == '.srt' then
      found = name
    end
  end
  return found and utils.join_path(v.dir, found) or nil
end

local function srt_blocks(v)
  local srt = find_srt(v)
  -- Si no hay SRT junto al vídeo (carpeta sin permiso de escritura: mpvd lo deja en la carpeta de subtítulos), vale
  -- el que acaba de guardar «Conseguir la transcripción»: es la misma transcripción, en otro sitio.
  if not srt then
    local out = state.transcript and state.transcript.out or ''
    if out ~= '' and utils.file_info(out) then srt = out end
  end
  if not srt then return nil end
  return chapters.parse_srt(read_file(srt) or '')
end

-- El idioma de esa transcripción, en dos letras: el del nombre (<video>.es.srt, que es como los dejan mpvd y las
-- descargas) o, si el archivo se llama <video>.srt, el de la primera pista de subtítulos del vídeo. '' si no se sabe.
local ISO3 = { spa = 'es', eng = 'en', fre = 'fr', fra = 'fr', cat = 'ca', por = 'pt', ger = 'de', deu = 'de', ita = 'it' }
local function lang_code(code)
  code = tostring(code or ''):lower():gsub('[^%a]', '')
  if code == '' then return '' end
  return ISO3[code:sub(1, 3)] or code:sub(1, 2)
end

local function srt_lang(v, srt)
  local _, name = utils.split_path(srt)
  local rest = (#name > #v.stem + 1) and name:sub(#v.stem + 2) or ''
  local code = rest:match('^(%a%a%a?)%.[Ss][Rr][Tt]$')
  if code then return lang_code(code) end
  for _, t in ipairs(mp.get_property_native('track-list') or {}) do
    local l = t.type == 'sub' and lang_code(t.lang) or ''
    if l ~= '' then return l end
  end
  return ''
end

-- ¿Hay transcripción que se pueda copiar? El .srt de al lado o el que acaba de dejar «Conseguir la transcripción».
-- Se mira al abrir el archivo y cuando una transcripción termina, no en cada aviso: un SRT de una hora tarda en
-- parsearse y el menú se pinta muchas veces.
local function refresh_text()
  local v = local_video()
  state.text_ready, state.text_cues, state.text_lang = false, 0, ''
  if not v then return end
  local srt = find_srt(v)
  if not srt then
    local out = state.transcript and state.transcript.out or ''
    if out ~= '' and utils.file_info(out) then srt = out end
  end
  if not srt then return end
  state.text_cues = #chapters.parse_srt(read_file(srt) or '')
  state.text_ready = state.text_cues > 0
  if state.text_ready then state.text_lang = srt_lang(v, srt) end
end

-- Writes the file keeping the previous one as .bak. os.rename fails on Windows when the target exists, hence the
-- remove first.
local function save(path, content)
  local bak = path .. '.bak'
  if utils.file_info(path) then
    os.remove(bak)
    local ok, err = os.rename(path, bak)
    if not ok then return false, err end
  end
  local f, err = io.open(path, 'wb')
  if not f then return false, err end
  f:write(content)
  f:close()
  return true
end

-- -- chapters on the timeline ----------------------------------------------------------------------------------------

-- The file's own chapters: once mu-marks has written anything they are its `originals`; before that, chapter-list
-- is still the file's.
local function own_chapters()
  local marks = mp.get_property_native('user-data/mu/marks')
  if type(marks) == 'table' and (tonumber(marks.total) or 0) > 0 then return tonumber(marks.originals) or 0 end
  return #(mp.get_property_native('chapter-list') or {})
end

local function show_marks(list)
  mp.commandv('script-message-to', 'mu_marks', 'mu-marks-set', 'chapters',
              utils.format_json({ mode = 'instead', marks = list }))
  state.count = #list
end

-- Lo que se dice al aplicar: los capítulos que van a la barra y, si la respuesta los traía, los puntos clave (H76).
local function applied_text(n, points)
  if (tonumber(points) or 0) > 0 then return tr('%d capítulos · %d puntos clave'):format(n, points) end
  return tr('%d capítulos'):format(n)
end

local function apply(v, list, points)
  local file = chapters_file(v)
  local ok, err = save(file, chapters.format(list, v.duration))
  show_marks(list)
  state.status, state.source, state.file = 'applied', 'paste', file
  if ok then
    osd(tr('%s aplicados y guardados junto al vídeo'):format(applied_text(#list, points)))
  else
    msg.warn('cannot save ' .. file .. ': ' .. tostring(err))
    osd(tr('%s aplicados, pero no se han podido guardar: %s'):format(applied_text(#list, points), tostring(err)))
  end
end

-- -- the two keys ----------------------------------------------------------------------------------------------------

local function not_local()
  osd(tr('Los capítulos con IA son solo para archivos del equipo, no para directos ni enlaces'))
end

-- -- quitar los capítulos de este vídeo ------------------------------------------------------------------------------

-- Lo que se enseña en la barra se quita de la fuente `chapters` de mu-marks (así vuelven los del archivo, si los
-- tenía) y el fichero de al lado se borra: dejarlo sería un «quitar» que vuelve solo al reabrir el vídeo.
local function forget(v)
  mp.commandv('script-message-to', 'mu_marks', 'mu-marks-clear', 'chapters')
  state.count, state.status, state.source, state.file, state.pending = 0, 'cleared', '', '', nil
  local file = chapters_file(v)
  if not utils.file_info(file) then return osd(tr('Capítulos quitados de la barra')) end
  local ok, err = os.remove(file)
  if not ok then
    state.status = 'error'
    msg.warn('cannot remove ' .. file .. ': ' .. tostring(err))
    return osd(tr('Capítulos quitados, pero no se ha podido borrar el archivo: %s'):format(tostring(err)))
  end
  osd(tr('Capítulos quitados (y borrado el archivo de al lado)'))
end

local function remove()
  local v = local_video()
  if not v then return not_local() end
  local file = chapters_file(v)
  if state.count == 0 and not utils.file_info(file) then
    return osd(tr('Este vídeo no tiene capítulos que quitar'))
  end
  local token = 'mu-chapters-rm-' .. math.floor(mp.get_time() * 1000)
  state.pending = { token = token, path = v.path, video = v, remove = true }
  state.status = 'confirm'
  publish()
  mp.commandv('script-message-to', 'mu_menu', 'mu-confirm', token,
              tr('¿Quitar los capítulos de este vídeo? El archivo de al lado se borra'),
              tostring(opts.confirm_seconds), tr('Sí, quitarlos'), tr('No, dejarlos'))
end

-- F5: el prompt se copia solo (con la duración del vídeo y la línea donde va la transcripción). Así la persona pega
-- el prompt en la IA y, debajo, la transcripción — la del vídeo o una que tenga de donde sea — y se entiende qué es
-- cada cosa. La transcripción tiene su propia fila.
local function copy()
  local v = local_video()
  if not v then return not_local() end
  if not clipboard_set(chapters.prompt_head(prompt_text(), v.duration)) then
    state.status = 'error'
    return osd(tr('No se ha podido copiar: mpv no tiene portapapeles en este sistema'))
  end
  state.status = 'copied'
  osd(tr('Prompt copiado') .. '\n' .. tr('Pégalo en la IA y, debajo, la transcripción del vídeo'))
end

-- Las líneas que se copian de la transcripción: el SRT compactado, con sus [mm:ss]. nil si no hay de dónde.
local function transcript_lines(v)
  local blocks = srt_blocks(v)
  if not blocks or #blocks == 0 then return nil end
  local lines = chapters.compact(blocks, { duration = v.duration, line_seconds = opts.line_seconds }).lines
  return #lines > 0 and lines or nil
end

-- H76 · el pegado de una vez: el prompt, una línea en blanco y la transcripción. El prompt ya termina en
-- «TRANSCRIPCIÓN:», así que la transcripción cae justo debajo, donde toca. Sin transcripción no hay nada que juntar:
-- la misma tecla copia solo el prompt, que es lo único que se puede copiar.
local function copy_all()
  local v = local_video()
  if not v then return not_local() end
  local lines = transcript_lines(v)
  if not lines then return copy() end
  local text = chapters.prompt_head(prompt_text(), v.duration) .. '\n' .. table.concat(lines, '\n')
  if not clipboard_set(text) then
    state.status = 'error'
    return osd(tr('No se ha podido copiar: mpv no tiene portapapeles en este sistema'))
  end
  state.status = 'copied'
  local thousands = math.max(1, math.floor(chapters.tokens(text) / 1000 + 0.5))
  osd(tr('Prompt y transcripción copiados: %d líneas (~%d mil tokens)'):format(#lines, thousands))
end

-- Abrir la página donde se saca la transcripción, en el navegador del sistema y sin dependencias: la orden que ya
-- usa cada plataforma. `open_command` sirve para los tests (y para quien prefiera otro navegador).
local RIVERSIDE = 'https://riverside.com/transcription'

local function browser_command()
  if opts.open_command ~= '' then return { opts.open_command } end
  local platform = mp.get_property_native('platform') or ''
  if platform == 'windows' then return { 'cmd', '/c', 'start', '' } end   -- `start` es del cmd, no un programa
  if platform == 'darwin' then return { 'open' } end
  return { 'xdg-open' }
end

local function open_riverside()
  local args = browser_command()
  args[#args + 1] = RIVERSIDE
  mp.command_native_async({ name = 'subprocess', args = args, detach = true, playback_only = false }, function() end)
  osd(tr('Abriendo %s'):format((RIVERSIDE:gsub('^https?://', ''))))
end

local function copy_transcript()
  local v = local_video()
  if not v then return not_local() end
  local lines = transcript_lines(v)
  if not lines then
    state.status = 'error'
    return osd(tr('Este vídeo no trae transcripción: sácala en riverside.com/transcription o con «Conseguir la transcripción»'))
  end
  local text = table.concat(lines, '\n')
  if not clipboard_set(text) then
    state.status = 'error'
    return osd(tr('No se ha podido copiar: mpv no tiene portapapeles en este sistema'))
  end
  state.status = 'copied-transcript'
  local thousands = math.max(1, math.floor(chapters.tokens(text) / 1000 + 0.5))
  osd(tr('Transcripción copiada: %d líneas (~%d mil tokens)'):format(#lines, thousands) .. '\n'
      .. tr('Pégala debajo del prompt, en el mismo mensaje'))
end

local function paste()
  local v = local_video()
  if not v then return not_local() end
  local text = clipboard_get()
  if not text:find('%S') then return osd(tr('El portapapeles está vacío: copia antes la respuesta de la IA')) end
  -- H76 · de la respuesta se queda con los capítulos; los puntos clave solo se cuentan, para poder decirlo
  local answer = chapters.parse_answer(text)
  local list = answer.chapters
  if #list == 0 then
    return osd(tr('No reconozco capítulos en lo copiado: tienen que ser líneas «MM:SS Título»'))
  end
  if opts.respect_existing and own_chapters() > 0 then
    state.status = 'skipped'
    return osd(tr('Este vídeo ya trae sus propios capítulos: no se pisan (respect_existing en mu-chapters.conf)'))
  end
  local blocks = srt_blocks(v)
  local starts = nil
  if blocks and #blocks > 0 then
    starts = {}
    for i, b in ipairs(blocks) do starts[i] = b.start end
  end
  local res = chapters.fit(list, { duration = v.duration, starts = starts, tolerance = opts.tolerance,
                                   min_gap = opts.min_gap, first_title = tr('Inicio') })
  if #res.chapters == 0 then return osd(tr('Ninguno de esos capítulos cabe en este vídeo')) end
  if not res.suspicious then return apply(v, res.chapters, answer.points) end

  local token = 'mu-chapters-' .. math.floor(mp.get_time() * 1000)
  state.pending = { token = token, path = v.path, video = v, list = res.chapters, points = answer.points }
  state.status = 'confirm'
  publish()
  mp.commandv('script-message-to', 'mu_menu', 'mu-confirm', token,
              tr('Los tiempos no parecen de esta transcripción (%d de %d no cuadran)'):format(res.total - res.matched,
                                                                                              res.total),
              tostring(opts.confirm_seconds), tr('Aplicarlos igualmente'), tr('No, cancelar'))
end

mp.observe_property('user-data/mu/confirm', 'native', function(_, answer)
  local p = state.pending
  if not p or type(answer) ~= 'table' or answer.token ~= p.token then return end
  state.pending = nil
  if answer.answer == 'yes' and mp.get_property('path') == p.path then
    if p.remove then return forget(p.video) end
    return apply(p.video, p.list, p.points)
  end
  state.status = 'cancelled'
  osd(p.remove and tr('Se quedan como estaban') or tr('No se han aplicado los capítulos'))
end)

-- -- F4 · conseguir la transcripción (el trabajo lo hace mu-subs) -----------------------------------------------------
--
-- Un solo botón, sin elegir modelo ni pista: se lo pedimos a mu-subs (`mu-subs-transcript`), que usa los subtítulos de
-- texto que ya traiga el vídeo o transcribe con whisper, y deja el SRT al lado del vídeo. Aquí solo se cuenta lo que
-- va pasando (progreso de verdad, el que publica mu-subs) y se avisa al terminar. Nada de APIs ni de abrir nada.

local function transcribing(t)
  return t and (t.status == 'extracting' or t.status == 'transcribing' or t.status == 'saving')
end

-- Lo que se copiará: las líneas compactas y sus caracteres. Es el número que le sirve al usuario (no los cues del SRT).
local function transcript_size(out, duration)
  local text = out ~= '' and read_file(out) or nil
  if not text then return 0, 0 end
  local lines = chapters.compact(chapters.parse_srt(text), { duration = duration, line_seconds = opts.line_seconds }).lines
  local chars = 0
  for _, line in ipairs(lines) do chars = chars + #line end
  return #lines, chars
end

local function ask_transcript()
  local v = local_video()
  if not v then return not_local() end
  if transcribing(state.transcript) then return osd(tr('Ya se está consiguiendo la transcripción…')) end
  state.transcript = { status = 'asking', progress = 0, said = -10 }
  publish()
  -- mu-subs avisa de lo que ha decidido (los subtítulos del vídeo o whisper); aquí se sigue desde su estado
  mp.commandv('script-message-to', 'mu_subs', 'mu-subs-transcript')
end

mp.observe_property('user-data/mu/subs', 'native', function(_, subs)
  local t = state.transcript
  if not t or type(subs) ~= 'table' then return end
  local status = subs.transcript_status or ''
  if status == '' then return end                       -- mu-subs no tiene nada entre manos para este archivo
  t.source = subs.transcript_source ~= '' and subs.transcript_source or t.source
  if status == 'transcribing' then
    local pct = math.floor((tonumber(subs.progress) or 0) * 100 + 0.5)
    t.status, t.progress = 'transcribing', pct
    -- un aviso cada 5 %: ni un OSD por fotograma ni un silencio de minutos. El «listos en …» lo calcula mu-subs.
    if pct - (t.said or -10) < 5 then return publish() end
    t.said = pct
    local wait = subs.wait or ''
    return osd(tr('Transcribiendo… %d %%'):format(pct) .. (wait ~= '' and (' · ' .. wait) or ''))
  end
  if status ~= 'done' and status ~= 'failed' then
    t.status = status
    return publish()
  end
  if t.status == status then return end                  -- ya se avisó: mu-subs publica muchas veces más
  if status == 'failed' then
    t.status, t.error = 'failed', subs.transcript_error or ''
    return osd(tr('✗ No se pudo conseguir la transcripción: %s'):format(t.error ~= '' and t.error or tr('error')))
  end
  t.status, t.out = 'done', subs.transcript_out ~= '' and subs.transcript_out or (subs.save_out or '')
  local v = local_video()
  t.lines, t.chars = transcript_size(t.out, v and v.duration or 0)
  refresh_text()
  local what = t.source == 'track' and (tr('Son los subtítulos que ya traía el vídeo') .. '\n') or ''
  osd(tr('✓ Transcripción lista: %d líneas, %d caracteres'):format(t.lines, t.chars) .. '\n' .. what
      .. tr('Ya puedes copiarla desde «Capítulos con una IA»'))
end)

-- -- opening a file --------------------------------------------------------------------------------------------------

local function load_saved()
  local v = local_video()
  if not v then return end
  local file = chapters_file(v)
  if not utils.file_info(file) then return end
  local list = chapters.parse_reply(read_file(file) or '')
  if #list == 0 then
    msg.warn('nothing usable in ' .. file)
    return
  end
  if opts.respect_existing and own_chapters() > 0 then
    state.status = 'skipped'
    msg.info('the file has its own chapters; ' .. file .. ' is not applied (respect_existing)')
    return publish()
  end
  -- the file is the user's: no snapping and no minimum gap, only what cannot work goes
  local res = chapters.fit(list, { duration = v.duration, min_gap = 0, first_title = tr('Inicio') })
  if #res.chapters == 0 then return end
  show_marks(res.chapters)
  state.status, state.source, state.file = 'loaded', 'file', file
  publish()
end

mp.register_event('start-file', function()
  state.path, state.status, state.count, state.source, state.file, state.pending = '', 'idle', 0, '', '', nil
  state.transcript, state.text_ready, state.text_cues, state.text_lang = nil, false, 0, ''
  publish()
end)

mp.register_event('file-loaded', function()
  state.path = mp.get_property('path') or ''
  refresh_text()
  publish()
  load_saved()
end)

mp.add_key_binding(nil, 'chapters-copy', copy)
mp.add_key_binding(nil, 'chapters-copy-all', copy_all)                 -- H76: ctrl+alt+c cuando hay transcripción
mp.add_key_binding(nil, 'chapters-copy-transcript', copy_transcript)   -- F5: desde el menú, sin tecla propia
mp.add_key_binding(nil, 'chapters-paste', paste)
mp.add_key_binding(nil, 'chapters-remove', remove)
mp.add_key_binding(nil, 'chapters-transcript', ask_transcript)   -- F4: desde el menú, sin tecla propia
mp.add_key_binding(nil, 'chapters-open-riverside', open_riverside)   -- H76: la página de la transcripción, en el navegador

publish()
msg.info('mu-chapters loaded')
