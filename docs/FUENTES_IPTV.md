# Fuentes de TV/radio para H2 (formato real verificado)

Fecha de verificación: 2026-09-28 (host dell, curl 8.x, sin proxy). Todo lo de aquí se ha comprobado contra
la fuente real; lo marcado "por verificar" es una hipótesis que hay que contrastar con `mpv --list-options`
antes de usarla. Fixtures recortadas de estas mismas descargas en `tests/fixtures/iptv/`.

## 1. TDTChannels (https://www.tdtchannels.com, repo LaQuay/TDTChannels)

| URL | HTTP | Bytes | #EXTINF | Grupos | Notas |
|---|---|---|---|---|---|
| https://www.tdtchannels.com/lists/tv.m3u8 | 200 | 148 900 | 576 | 30 | TV España + internacional |
| https://www.tdtchannels.com/lists/radio.m3u8 | 200 | 261 797 | 1 041 | 24 | radio, `radio="true"` |
| https://www.tdtchannels.com/lists/tvradio.m3u8 | 200 | 410 641 | 1 617 | 55 | concatenación tv+radio |
| https://www.tdtchannels.com/epg/TV.xml.gz | (no probado) | | | | EPG XMLTV referenciado en `url-tvg` |

Cabeceras (curl -I): `server: cloudflare`, `content-type: application/octet-stream`, `cache-control: no-store`,
`etag: "6ab8dd34-245a4"` (estilo nginx: mtime-tamaño; cambia por fichero), `last-modified: Sun, 27 Sep 2026 09:09:08 GMT`.
Peticiones condicionales verificadas: `If-None-Match` y `If-Modified-Since` devuelven **304** sin cuerpo.
Codificación UTF-8 sin BOM, finales de línea LF, una línea vacía final.

### Cabecera: DOS líneas `#EXTM3U` en tv/tvradio, comentario suelto en radio
```
#EXTM3U @LaQuay https://github.com/LaQuay/TDTChannels
#EXTM3U url-tvg="https://www.tdtchannels.com/epg/TV.xml.gz"
```
radio.m3u8 en cambio empieza con `#EXTM3U` a secas seguido de `# @LaQuay https://github.com/LaQuay/TDTChannels`
(línea que empieza por `#` pero no es directiva) y NO lleva `url-tvg`. No aparece `x-tvg-url` en ninguna.

### Entradas
```
#EXTINF:-1 tvg-id="La1.TV" tvg-logo="https://graph.facebook.com/la1detve/picture?width=200&height=200" group-title="Generalistas" tvg-name="La 1",La 1
https://ztnr.rtve.es/ztnr/1688877.m3u8
#EXTINF:-1 radio="true" tvg-id="CadenaS.Radio" tvg-logo="https://graph.facebook.com/cadenaser/picture?width=200&height=200" group-title="Radio_Populares" tvg-name="Cadena SER",Cadena SER
https://playerservices.streamtheworld.com/api/livestream-redirect/CADENASER.mp3
```
- Atributos observados (y solo estos): `tvg-id` (194/576 en TV, 326/1041 en radio; cuando falta, falta el atributo,
  nunca `tvg-id=""`), `tvg-logo`, `group-title`, `tvg-name` (siempre), `radio="true"` (todas las de radio, ningún otro valor).
  No hay `tvg-chno`, `tvg-language`, `tvg-country`, `catchup`, `#EXTGRP`, `#KODIPROP`.
- Duración siempre `-1`. Título = texto tras la última coma que sigue a los atributos.
- `tvg-name` != título en 124 entradas de TV: el título añade sufijos tipo `3CatInfo GEO CAT`, `RadioU TV USA EN`.
- 127 títulos con no-ASCII en TV (`El País`, `Top Barça`, `¡HOLA! Play`); 269 en radio.
- **Coma dentro de un atributo entrecomillado** (1 caso en TV, Tevecan 9): partir en la primera coma rompe el parseo.
  ```
  #EXTINF:-1 tvg-logo="https://static.wixstatic.com/media/...~mv2.png/v1/fill/w_200,h_200,al_c,q_85,usm_0.66_1.00_0.01,enc_auto/mosca%20logo%209_transparente.png" group-title="Cantabria" tvg-name="Tevecan 9",Tevecan 9
  ```
- `#EXTVLCOPT` en 36 entradas de TV (0 en radio), SIEMPRE entre `#EXTINF` y la URL; 23 llevan las dos, 7 solo referrer,
  6 solo user-agent; orden observado: user-agent antes que referrer (en iptv-org es al revés).
  ```
  #EXTINF:-1 tvg-id="ETBD.TV" tvg-logo="..." group-title="Deportivos" tvg-name="ETB Deportes (Kirolak 360)",ETB Deportes (Kirolak 360)
  #EXTVLCOPT:http-referrer=https://kirolakeitb.eus/es/kirolak-360/en-directo/
  https://multimedia.eitb.eus/live-content/oka1hd-hls/master.m3u8
  #EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36 CrKey/1.44.191160
  ```
- Duplicados: 0 URLs repetidas dentro de tv o radio por separado; 69 nombres repetidos con URL distinta en TV
  (`+tdp` x9, `Senado` x8, `+24` x6: son variantes/mirrors del mismo canal) y 44 en radio (`Cadena SER` x6 consecutivas
  con atributos idénticos). En tvradio, 1 URL con dos nombres (`CMM Radio` / `Castilla-La Mancha Radio (CMM)`).
- URLs: TV 567 https + 9 http, 575 acaban en `.m3u8` (48 con query string) y 1 en `.../smil:3abn_live.smil/` (sin extensión).
  Radio: 1010 https + 31 http; extensiones `.mp3` 425, sin extensión 431 (`.../stream`, `.../icecast`, `host:8443/mdtweb`),
  `.m3u8` 153, `.aac` 26, `.php?ch=1` 2, `.audio` 2, `.ogg` 1, `.com` 1 (`https://radioplaneta.emitironline.com`).
  Solo esquemas http/https; no rtmp/mpd.
- Grupos TV (top 15): Andalucía 79, Musicales 59, Cataluña 54, Eventuales 48, Int. América 48, Int. Europa 41,
  C. Valenciana 29, Int. Asia 21, Canarias 20, País Vasco 19, Galicia 18, Religiosos 18, Informativos 16, Int. Otros 14,
  Deportivos 13. Resto: Infantiles, Castilla-La Mancha, Generalistas (solo 9), C. de Madrid, Castilla y León, R. de Murcia,
  Int. África, Deportivos Int., Cantabria, C. Foral de Navarra, La Rioja, Extremadura, Illes Balears, Melilla, P. de Asturias.
- Grupos radio (top): Radio_Andalucía 160, Radio_C. Valenciana 110, Radio_Cataluña 107, Radio_Musicales 66,
  Radio_Castilla y León 57, Radio_C. de Madrid 54, Radio_Internacional 53, Radio_Galicia 48, ... Radio_Populares 27
  (las nacionales). Todos con prefijo `Radio_`.

## 2. iptv-org (https://github.com/iptv-org/iptv)

| URL | HTTP | Bytes | Notas |
|---|---|---|---|
| https://iptv-org.github.io/iptv/index.m3u | 200 | 2 483 628 | 10 976 canales, group-title = categoría |
| https://iptv-org.github.io/iptv/index.country.m3u | 200 | 2 846 564 | group-title = país (`Brazil`); un canal se repite por país |
| https://iptv-org.github.io/iptv/index.category.m3u | 200 | 2 635 573 | group-title = categoría; repite por categoría |
| https://iptv-org.github.io/iptv/index.language.m3u | 200 | 2 787 281 | group-title = idioma (`Abkhazian`); repite por idioma |
| https://iptv-org.github.io/iptv/index.region.m3u | **404** | | no existe |
| https://iptv-org.github.io/iptv/regions/eur.m3u | 200 | 1 036 463 | sí existen los por región |
| https://iptv-org.github.io/iptv/countries/es.m3u | 200 | 79 905 | 323 canales, 43 grupos |
| https://iptv-org.github.io/iptv/categories/news.m3u | 200 | 225 634 | |
| https://iptv-org.github.io/iptv/languages/spa.m3u | 200 | 481 777 | |

Cabeceras: GitHub Pages tras Fastly, `content-type: audio/x-mpegurl`, `cache-control: max-age=600`, `etag: "6ab9b32a-25e5ac"`,
`last-modified: Mon, 28 Sep 2026 00:22:02 GMT` (se regenera a diario de madrugada UTC), `accept-ranges: bytes`
(las peticiones `Range` funcionan). `If-None-Match` → **304** verificado. Finales de línea **CRLF** en todas las líneas, sin BOM.

### Entradas (index.m3u)
```
#EXTM3U
#EXTINF:-1 tvg-id="00sReplay.us@SD" tvg-logo="https://images.pluto.tv/channels/62ba60f059624e000781c436/colorLogoPNG.png" group-title="Movies",00s Replay
https://jmp2.uk/plu-62ba60f059624e000781c436.m3u8
#EXTINF:-1 tvg-id="2MMonde.ma@SD" tvg-logo="https://i.imgur.com/MvpntzA.png" http-referrer="http://www.radio2m.ma/" http-user-agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:134.0) Gecko/20100101 Firefox/134.0" group-title="General",2M Monde (360p)
#EXTVLCOPT:http-referrer=http://www.radio2m.ma/
#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:134.0) Gecko/20100101 Firefox/134.0
https://cdn-globecast.akamaized.net/live/eds/2m_monde/hls_video_ts_tuhawxpiemz257adfc/2m_monde.m3u8
#EXTINF:-1 tvg-id="News1.th@SD" tvg-logo="" group-title="News",News1
```
- Cabecera `#EXTM3U` pelada (sin url-tvg). Atributos: `tvg-id` (100 %, formato `Canal.cc@Feed`, feeds `SD`, `HD`, `4K`,
  `UK`, `India`...), `tvg-logo` (143 vacíos `tvg-logo=""`), `group-title` (100 %; 184 valores; `General` 2 638,
  `Undefined` 1 467, `News` 919...; **múltiples categorías separadas por `;`** en 608: `group-title="Public;Weather"`;
  existe `XXX` → filtrar NSFW), y `http-referrer="..."` (223) / `http-user-agent="..."` (580) **como atributos del #EXTINF
  y además repetidos como líneas `#EXTVLCOPT:`** (803 líneas; siempre las dos formas a la vez; referrer antes que UA).
- Título: nombre + sufijo de calidad `(1080p)` en 8 243, etiquetas `[Not 24/7]` (1 316) y `[Geo-blocked]` (495).
  Solo 5 títulos no-ASCII (`360° (1080p)`, `¡OPA! (720p)`, `The L Word – Wenn Frauen lieben`): los nombres se romanizan.
- 543 `#EXTINF` con comas dentro de atributos entrecomillados (logos de wixstatic/wikimedia): misma rareza que TDT.
- **No hay `#KODIPROP`, `#EXTGRP`, `#EXT-X-*`, `inputstream.*`, ni claves DRM** en index.m3u (28 apariciones de "drm" son
  trozos de URL). Los 123 `.mpd` (DASH) van sin cabeceras ni licencias (`https://cdn.stmify.com/bbc/stream/BBC_ALBA/hevc_iptv_mse_v0.mpd`).
- Esquemas: https 8 880, http 2 087, `rtmp://` 6, `mmsh://` 2, `srt://` 1. Extensiones: `.m3u8` 10 598, `.mpd` 123,
  `.php?…` 24, sin extensión 198 (`http://1.2.3.4:8001`, `http://91.214.48.158:4022/udp/224.2.2.4:10000`), `.m3u8s` 2,
  `.m3u8;session=live_stream_1341` 1, `.ts`, `.mp4`, `.flv`, `.m3u`, `.ogg`, `.htm` 2.
- 0 URLs duplicadas en index.m3u; en los índices por país/categoría/idioma el mismo canal+URL aparece bajo varios group-title.

### API JSON (https://iptv-org.github.io/api/) — todos 200, `cache-control: max-age=600`, regenerada a diario
| Fichero | Bytes | Campos (objeto real) |
|---|---|---|
| channels.json | 7 897 827 | `{"id":"002RadioTV.do","name":"002 Radio TV","alt_names":[],"network":null,"owners":[],"country":"DO","categories":["general"],"is_nsfw":false,"launched":null,"closed":null,"replaced_by":null,"website":"https://www.002radio.com/"}` |
| streams.json | 3 663 316 | `{"channel":null,"feed":null,"title":"TV Publica","url":"http://playcom.trapemn.tv:1935/transcoderip/tvpublica.stream/playlist.m3u8","quality":"1080p","labels":[],"user_agent":null,"referrer":null}`; labels `["Not 24/7"]`, `["Geo-blocked"]`; `channel` puede ser null |
| countries.json | 18 955 | `{"name":"Spain","code":"ES","languages":["spa"],"flag":"🇪🇸"}` |
| categories.json | 2 773 | `{"id":"auto","name":"Auto","description":"..."}` |
| languages.json | 269 003 | `{"code":"aaa","name":"Ghotuo"}` (ISO 639-3) |
| logos.json | 5 669 288 | `{"channel":"00sReplay.us","feed":null,"in_use":true,"tags":[],"width":576,"height":288,"format":"PNG","url":"https://images.pluto.tv/..."}` |
| feeds.json / guides.json / regions.json / subdivisions.json / blocklist.json | 8,0 MB / 25,6 MB / 9 207 / 333 852 / 149 332 | existen; guides es enorme, no descargar por defecto |

## 3. Radio Browser (https://api.radio-browser.info)
- Descubrimiento: la doc pide resolver `all.api.radio-browser.info` (A/AAAA) y hacer PTR de cada IP, en el cliente;
  `/json/servers` solo si el cliente no puede hacer DNS. Hoy: `91.98.4.78` y `2a01:4f8:1c1d:699::1` → un único
  `de1.api.radio-browser.info`. Elegir servidor al azar entre los resueltos y hacer fallback a `de1` si falla el DNS.
- User-Agent: la doc pide uno descriptivo `appname/appversion` (ej. `mpv-uos/0.1`). Técnicamente no se bloquea sin él
  (probado: 200), pero es obligatorio por cortesía y para que nos listen.
- Servidor `tiny-http (Rust)` v0.7.45, CORS `*`, admite GET o POST (form/JSON). Sin `cache-control`/`etag`: cachear en local.
- Sin límite de tasa documentado; `limit` por defecto es 100000 → pasar SIEMPRE `limit`; usar `hidebroken=true`.
- Stats hoy: 59 663 estaciones (6 959 rotas), 12 379 tags, 681 idiomas, 241 países.
- Endpoints verificados (base `https://de1.api.radio-browser.info`):
  - `/json/countries` → `[{"name":"Spain","iso_3166_1":"ES","stationcount":1461}, ...]` (241)
  - `/json/tags?order=stationcount&reverse=true&limit=20` → pop 6315, music 5302, rock 3285, news 3100, radio 2477,
    entretenimiento 2311, estación 2297, méxico 2132... (tags sucios y multiidioma: normalizar/agrupar en la UI)
  - `/json/stations/bycountrycodeexact/ES?order=votes&reverse=true&limit=5` → Cadena 100, Ibiza Global Radio, LOS 40, Chocolate FM, Cadena SER
  - `/json/stations/search?name=radio%203&limit=3` → 3 variantes de BBC Radio 3 (hay duplicados por bitrate)
  - `/json/url/<stationuuid>` (contador de clics; 1 por IP y estación al día; llamar al empezar a reproducir) →
    `{"ok":true,"message":"retrieved station url","stationuuid":"960c37c6-...","name":"Cadena 100","url":"http://cadena100-streamers-mp3.flumotion.com/cope/cadena100.mp3"}`
  - `/json/stats`, `/json/servers`.
- Campos de estación (todos presentes): `stationuuid`, `changeuuid`, `serveruuid`, `name`, `url`, `url_resolved`
  (playlists/redirecciones ya resueltas: usar este), `homepage`, `favicon`, `tags` (CSV), `country` (deprecated),
  `countrycode`, `iso_3166_2`, `state`, `language`, `languagecodes`, `votes`, `codec` (`MP3`, `AAC`, `AAC+`...),
  `bitrate` (kbps; 0 = desconocido), `hls` (0/1), `lastcheckok` (0/1), `lastchecktime[_iso8601]`, `lastcheckoktime`,
  `lastlocalchecktime`, `clicktimestamp`, `clickcount`, `clicktrend`, `ssl_error`, `geo_lat`, `geo_long`, `geo_distance`, `has_extended_info`.

## 4. Traducción de `#EXTVLCOPT` / `#KODIPROP` a mpv (POR VERIFICAR con `mpv --list-options`)
| Directiva de la lista | Opción mpv candidata (por verificar) |
|---|---|
| `#EXTVLCOPT:http-user-agent=X` / atributo `http-user-agent="X"` | `--user-agent=X` (verificado; sin ella, User-Agent de navegador: §7) |
| `#EXTVLCOPT:http-referrer=X` / atributo `http-referrer="X"` | `--referrer=X` |
| `#EXTVLCOPT:demuxer-lavf-o=k=v,…` / `stream-lavf-o` (extensión propia, no la usa ninguna fuente) | la misma opción por archivo (verificado); en HLS se le añaden las nuestras sin pisar sus claves (§7) |
| `#EXTVLCOPT:network-caching=N` (ms) | `--cache-secs=N/1000` o `--demuxer-readahead-secs` |
| `#EXTVLCOPT:http-reconnect=true` | `--stream-lavf-o=reconnect=1,reconnect_streamed=1` |
| `#KODIPROP:inputstream.adaptive.stream_headers=k=v&k2=v2` | `--http-header-fields="k: v","k2: v2"` |
| `#KODIPROP:inputstream.adaptive.manifest_type=mpd` | nada (ffmpeg autodetecta DASH) |
| `#KODIPROP:inputstream.adaptive.license_type=org.w3.clearkey` + `license_key=kid:key` | sin soporte DRM en mpv; ClearKey quizá `--demuxer-lavf-o=decryption_key=` (por verificar); si no, marcar canal "no reproducible" |
| certificados autofirmados (no visto en estas fuentes) | `--tls-verify=no` / `--tls-ca-file` |
Aplicación por canal: pasar las opciones en el `loadfile` (argumento `options` de `loadfile`, firma cambiada en mpv ≥0.38;
por verificar en el manual instalado) y no globalmente. Ninguna fuente actual trae KODIPROP: implementar el parseo, pero el
soporte DRM no es prioritario.

## 5. Resumen y TTL sugerido
| Fuente | URL | Canales | Tamaño | TTL sugerido |
|---|---|---|---|---|
| TDTChannels TV | https://www.tdtchannels.com/lists/tv.m3u8 | 576 | 149 KB | 12 h, revalidar con ETag/If-Modified-Since (304 OK) |
| TDTChannels radio | https://www.tdtchannels.com/lists/radio.m3u8 | 1 041 | 262 KB | 24 h, idem |
| iptv-org índice | https://iptv-org.github.io/iptv/index.m3u | 10 976 | 2,5 MB | 24 h (se regenera a diario), revalidar con ETag |
| iptv-org por país | https://iptv-org.github.io/iptv/countries/<cc>.m3u | 323 (es) | 80 KB (es) | 24 h; preferible al índice completo |
| iptv-org API | https://iptv-org.github.io/api/{channels,streams,countries,categories,languages,logos}.json | 10 976 | 7,9+3,7+... MB | countries/categories/languages 7 d; channels/streams/logos 24 h; guides.json no |
| Radio Browser | https://<srv>.api.radio-browser.info/json/... | 59 663 est. | por consulta | countries/tags 24 h; listados 1 h; búsquedas sin caché; click en play |

## 6. Rarezas que el parser debe manejar (checklist para tests)
1. Comas dentro de atributos entrecomillados (TDT 1, iptv-org 543): parsear atributos `k="v"` primero, título después.
2. Dos líneas `#EXTM3U` seguidas y líneas `# comentario`; `#EXTM3U` con texto libre tras él; `url-tvg` opcional.
3. `#EXTVLCOPT` entre `#EXTINF` y URL, en cualquier orden; mismos datos duplicados como atributos (iptv-org).
4. Atributos ausentes (no vacíos) en TDT; vacíos (`tvg-logo=""`) en iptv-org; `group-title` con `;`.
5. CRLF (iptv-org) y LF (TDT); línea vacía final; posible BOM (no visto, pero barato de soportar).
6. Nombres repetidos con URL distinta (mirrors) y una URL con dos nombres: no deduplicar por nombre, sí por URL exacta si se quiere.
7. URLs sin extensión, con puerto, con `;session=`, con query; esquemas rtmp/mmsh/srt (mpv los abre vía ffmpeg, por verificar).
8. `tvg-id` con `@Feed` en iptv-org; título con `(720p)`, `[Not 24/7]`, `[Geo-blocked]` → extraer a campos aparte.
9. `radio="true"` (TDT) y grupo `Radio_*` para separar TV/radio; `XXX`/`is_nsfw` para filtrar.

## 7. Calidad y fiabilidad de los directos (diagnóstico 2026-09-30, host dell)

Evidencia en `tmp/diag-tv/` (probe.json, survey.json, 101tv_long.log vs 101tv_np.log). Encuesta de las 442 entradas
de TV de TDTChannels sin los grupos internacionales, pidiendo cada lista maestra con curl.

### 7.1 Lo que depende de la fuente ("lo que hay")
- **Resolución y bitrate los decide la cadena.** De 315 listas maestras con variantes: 148 llegan a 1080p, 124 a
  720p, 2 a 576p y 15 se quedan en 360p. RTVE (La 1, La 2, Clan, Teledeporte) da como máximo **720p25 a 3,0 Mb/s**
  (`BANDWIDTH=3012608`); no hay 1080p ni 50 fps de RTVE en abierto por internet. 37 canales "HD" emiten por debajo de
  1,6 Mb/s (Real Madrid TV 720p a 1,0 Mb/s, Penedès TV 720p a 0,5 Mb/s…): se verán peor que un buen SD.
- **Casi todo va a 25 fps** (las que lo declaran: 25 → 87, 29,97 → 12, 30 → 5; 200 maestras no declaran
  `FRAME-RATE`). TVG (europa) es de las pocas a 50 fps.
- **Entrelazado sin marcar**: 7TV Andalucía emite 1080i (idet: 36 campos TFF de 251) sin `field_order`, así que ni mpv
  ni ffmpeg lo saben; Canal Extremadura y Clan muestran algo de entrelazado mezclado. Desentrelazar a mano (`d`).
- **80 entradas son listas de medios** (sin variantes): no se puede elegir calidad.
- **Copias FAST** (canal re-emitido con anuncios insertados): hosts `ottera`, `amagi`, `samsungtv.plus`, `rakuten`,
  `getpublica`, `pluto.tv`/`jmp2.uk`, y CloudFront/MediaTailor con `/v1/master/`. A veces tienen más resolución
  (La 1 de ottera: 1080p25 a 4,2 Mb/s, reescalado) pero cortan para publicidad.
- **Duplicados**: la misma cadena aparece varias veces con el mismo nombre y grupo (oficial + espejos + FAST).
- **Cabeceras**: Canal Sur Andalucía oficial (`live-24-canalsur.interactvty.pro`) responde **403** al User-Agent por
  defecto de mpv (`libmpv`) y 200 a uno de navegador; es el único de la encuesta (393 responden igual con ambos).
- **Servidores que cortan conexiones persistentes**: 101TV Málaga (Wowza) se congela a los ~12 s con
  `hls: Failed to reload playlist 0` porque ffmpeg reutiliza una conexión que el servidor ya ha cerrado.
- Canales caídos (404, sin respuesta) o geobloqueados fuera de España: no hay arreglo en el cliente.

### 7.2 Lo que hace el reproductor
- **Sin watch_later en directos**: mu-iptv ejecuta `delete-watch-later-config <url>` antes de cada `loadfile` de un
  canal (y también con la ruta simple si es `file://`, que es como mpv la guarda) y mpvd añade siempre
  `save-position-on-quit=no` como opción por archivo, así que ni el zapping ni salir escriben posición ni pistas.
  (Antes La 1 quedaba en 360p por un `vid` guardado por URL; ver ADR-036.)
- **HLS**: a toda URL HLS (`.m3u8`, `format=m3u8`, `hls=1` de Radio Browser o KODIPROP) se le pasa
  `demuxer-lavf-o=http_persistent=0,seg_max_retry=3` (opciones del demuxer hls de FFmpeg 8.0, `ffmpeg -h demuxer=hls`):
  una conexión nueva por petición y 3 reintentos por segmento. Si la lista trae su propio `demuxer-lavf-o`, se
  conservan sus claves y solo se añaden las que falten.
- **User-Agent**: si la lista no da uno, se envía el de un Chrome de escritorio actual (`model.BROWSER_USER_AGENT`,
  cambiable con `MPV_UOS_USER_AGENT`) tanto al reproducir como en la comprobación de salud. Verificado con un test
  `@network`: Canal Sur oficial abre con `--ytdl=no` (antes solo funcionaba si yt-dlp lo rescataba).
- **Calidad visible**: *Comprobar canales en segundo plano* (`iptv.health.check`) lee además la lista maestra HLS y
  guarda la mejor variante (resolución, `FRAME-RATE`, `BANDWIDTH`); si la maestra no declara resolución o fps, los
  toma de ffprobe. En el menú: `720p50 · 2,7 Mb`, más `bitrate bajo` si es HD (≥720p) por debajo de 1,6 Mb/s.
- **FAST y duplicados**: las copias FAST salen como `con anuncios`. Los canales repetidos (mismo nombre normalizado y
  grupo en la misma lista) se muestran una sola vez (`+N fuentes`), con la copia oficial primero y las FAST al final; el
  zapping se salta las copias. Si la primera no abre (`end-file` con error), mu-iptv prueba sola la siguiente con el
  aviso "Probando otra fuente de «La 1»…".
- **En español**: países por código ISO con los nombres del sistema (`/usr/share/iso-codes/json/iso_3166-1.json` +
  catálogo gettext `iso_3166-1` en español; sin ellos, `mpvd/iptv/data/countries_es.json`, copia de esos mismos nombres),
  con `uk`→Reino Unido y `xk`→Kosovo; categorías de iptv-org traducidas (News → Noticias, Undefined → Sin categoría…);
  grupos limpios ("General;Public" → "General · Público", "Radio_C. Valenciana" → "Radio C. Valenciana");
  "geobloqueado". El país del usuario (`MPV_UOS_COUNTRY`, si no el territorio del locale, si no España) va primero en
  *Mundo* y *Radio mundial*. Los valores originales no cambian (filtros, favoritos, MCP y mando siguen igual).
