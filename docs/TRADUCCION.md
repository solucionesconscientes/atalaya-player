# Traducción offline de subtítulos (investigación verificada el 2026-09-29)

Objetivo: traducir cues de subtítulos es↔en (y otros pares) sin red, en un portátil de 4 hilos sin GPU (probado en un
i5-6200U, 2 núcleos/4 hilos, AVX2). **Recomendación: paquetes Argos Translate + `ctranslate2` + `sentencepiece`, sin
`argostranslate`** (que arrastra stanza→torch+CUDA, ~81 paquetes).

## 1. Paquetes Argos Translate
- Índice oficial: `https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json` (100 entradas, 50 idiomas origen;
  todos los pares pasan por inglés salvo pt↔es). Licencia del índice/argostranslate: MIT (repo `argospm-index`: MIT o CC0).
- Entrada (campos): `package_version`, `argos_version`, `from_code`, `from_name`, `to_code`, `to_name`, `links` (lista: HTTPS en
  `argos-net.com/v1/` e `ipfs://`), `code` (`translate-es_en`). Sin tamaño ni hash en el índice: hay que registrar el SHA-256 en
  `vendor.lock` tras descargar.
- Tamaños reales (`curl -sSLI … | grep content-length`):

| Paquete | URL | Zip | Tokenizador | Modelo CT2 | SHA-256 |
|---|---|---|---|---|---|
| en→es 1.0 (índice) | `https://argos-net.com/v1/translate-en_es-1_0.argosmodel` | 87,5 MB | sentencepiece 32k | `TransformerSpec` v4/rev3, 94 MB | `d698d0ef87ad70d5d184b7fa6965905bf4368f09a2bb9ffb165a79bac96af0c4` |
| es→en 1.9 (índice) | `https://argos-net.com/v1/translate-es_en-1_9.argosmodel` | 285 MB | BPE subword-nmt + Moses | v6/rev7, 314 MB fp32 | `c54df2b62fceaf54a3ce5d97db6bf56efd7940063329f6778f4212d2acb370d4` |
| es→en 1.0 (no indexado, sigue en el CDN) | `https://argos-net.com/v1/translate-es_en-1_0.argosmodel` | 87,4 MB | sentencepiece 32k | v4/rev3, 94 MB | `1b963aa0e0cb6e5ce874f0aa1a1949a19bc4d762e833532239a8834340f6b378` |

- Los tres están descargados en `vendor/dl/`; es_en 1.0 y en_es 1.0 descomprimidos en `tmp/argos_es_en_10/es_en` y `tmp/argos_en_es/en_es`. Derivan de
  OPUS-MT (Tiedemann & Thottingal 2020), licencia CC-BY 4.0 (README.md dentro del zip).
- Contenido del `.argosmodel` (zip; raíz `es_en/` o `translate-es_en-1_9/`):
  ```
  metadata.json         {"package_version","argos_version","from_code","from_name","to_code","to_name"} (+ opcional "target_prefix")
  README.md             cita y licencia del modelo OPUS
  sentencepiece.model   (1.0)  ó  bpe.model (1.9: fichero de merges "#version: 0.2", NO es sentencepiece)
  model/model.bin       modelo CTranslate2  ·  model/shared_vocabulary.txt|json  ·  model/config.json (1.9: add_source_eos=true, bos/eos)
  stanza/es/tokenize/ancora.pt + stanza/resources.json   segmentador de frases de stanza (ignorable)
  ```
- **El es→en 1.9 está roto**: con `compute_type` int8/int8_float32/int16 devuelve basura repetida (`mainstremainstre…`); solo va en
  float32 (2,4 s/frase). Es el issue abierto argos-translate #504 ("Argos broken from es to en", 2025-12); allí recomiendan el 1.0.
  Además exige Moses (`sacremoses`) + `apply_bpe.py`. **Usar es→en 1.0.**

## 2. Uso sin argostranslate (verificado)
`uv pip install --python .venv/bin/python ctranslate2 sentencepiece` → `ctranslate2==4.8.2` (wheel cp312 manylinux x86_64 37,7 MB; MIT;
depende de `numpy==2.5.3` 15,9 MB y `pyyaml==6.0.3` 0,8 MB) + `sentencepiece==0.2.2` (1,3 MB; Apache-2.0). Descarga ≈ 56 MB,
**instalado 192 MB** en `.venv` (ctranslate2 60 MB + ctranslate2.libs 75 MB + numpy 57 MB). Instalación 1,3 s desde caché.
Snippet mínimo (`tmp/argos_ct2_min.py`, funciona igual con el paquete en→es):
```python
import ctranslate2, sentencepiece as spm
pkg = "vendor/models/argos/es_en"                       # zip descomprimido
sp = spm.SentencePieceProcessor(model_file=f"{pkg}/sentencepiece.model")
tr = ctranslate2.Translator(f"{pkg}/model", device="cpu", compute_type="int8", inter_threads=1, intra_threads=4)
def translate(sents, beam=2):
    toks = [sp.encode(s, out_type=str) for s in sents]
    res = tr.translate_batch(toks, beam_size=beam, max_decoding_length=256)
    return [sp.decode(r.hypotheses[0]) for r in res]
translate(["Bienvenido a MPV-UOS, el reproductor del futuro.", "Hoy es un buen día para ver una película con subtítulos."])
# -> ['Welcome to MPV-UOS, the future player.', 'Today is a good day to see a movie with subtitles.']
```
- **No hace falta token de idioma ni prefijo**: modelos bilingües; `sentencepiece` no añade `<s>/</s>` y CTranslate2 pone el EOS
  (`add_source_eos`) y el `decoder_start_token` según `config.json`. `metadata.target_prefix` está vacío en estos paquetes (en los
  multilingües futuros habría que pasarlo como `target_prefix=[[prefix]]*n`).
- Medidas (es→en 1.0, int8, 4 hilos, i5-6200U): carga 0,19 s; 2 frases 0,23 s; **20 frases en lote: beam 1 → 0,30 s (15 ms/frase),
  beam 2 → 0,42 s (21 ms), beam 4 → 0,65 s (33 ms)**; una frase suelta con beam 2: ~110 ms; RSS máx. 224 MB. en→es 1.0: idéntico
  (0,17 s carga; 14/23/32 ms/frase). El int8 se cuantiza al cargar (en disco el modelo va en float32 → 94 MB); RAM ≈ 100 MB/modelo.
- Calidad muestra: "Te llamo mañana por la mañana." → "I'll call you tomorrow morning."; "¿Dónde está la estación de tren más
  cercana?" → "Where is the nearest train station?"; "el reproductor" → "player" (en→es devuelve "jugador": sesgo de dominio).
- `ctranslate2.get_supported_compute_types("cpu")` en esta CPU: `int8, int8_float32, int16, float32`. Usar `compute_type="auto"`
  o `int8` con fallback si no está soportado.

## 3. Alternativas
- **opus-mt (Helsinki-NLP) ya convertido a CTranslate2 en Hugging Face** (misma familia que Argos; tokenizador sentencepiece con
  `source.spm`/`target.spm` separados): `michaelfeil/ct2fast-opus-mt-es-en` y `…-en-es` (model.bin 148 MB fp16, 152 MB total),
  `gaudi/opus-mt-<xx>-<yy>-ctranslate2` (152 MB; cubre decenas de pares con es), `Prukario/opus-mt-es-en-ct2-int8` (76 MB,
  ya int8), `ooeoeo/opus-mt-tc-big-en-es-ct2-float16` (modelo "big", mejor calidad, más pesado). Originales
  `Helsinki-NLP/opus-mt-es-en` / `opus-mt-en-es`: `pytorch_model.bin` 297,6 MB, requieren torch+transformers para convertir
  (`ct2-transformers-converter`), por eso interesa lo ya convertido. Ventaja: catálogo mucho mayor (pares directos sin pivotar por
  inglés). Desventaja: mirrors de terceros; fijar SHA-256 en `vendor.lock`.
- **argostranslate completo** (`uv pip install --dry-run argostranslate`): `argostranslate==1.11.0` resuelve **81 paquetes**, entre
  ellos `stanza==1.10.1`, `torch==2.14.0`, `triton==3.8.0`, 18 paquetes `nvidia-*` (CUDA 13), `spacy==3.8.16`, `onnxruntime`,
  `sacremoses`, `minisbd`, `pydantic`, `rich`. Varios GB. Descartado (local-first y portátil sin GPU).
- Descartado también `sacremoses` (necesario solo para paquetes BPE como el 1.9; +6 paquetes: click, joblib, regex, tqdm…).

## 4. Segmentación de frases sin stanza
Los cues de subtítulos ya son cortos; basta separar por puntuación fuerte y proteger abreviaturas frecuentes (probado):
```python
import re
_ABBR = re.compile(r"(?:^|\s)(?:Sr|Sra|Srta|Dr|Dra|Ud|Uds|Vd|EE\.UU|p\.ej|etc|Mr|Mrs|Ms|St|vs|No|núm|art|pág|tel|aprox)\.$", re.I)
_SPLIT = re.compile(r"(?<=[.!?…])\s+(?=[¿¡\"'(\[A-ZÁÉÍÓÚÑ0-9])")
def split_sentences(text: str) -> list[str]:
    out: list[str] = []
    for chunk in _SPLIT.split(" ".join(text.split())):
        if out and _ABBR.search(out[-1]): out[-1] += " " + chunk
        else: out.append(chunk)
    return [s for s in out if s]
# '¡Hola! ¿Qué tal? El Sr. López llegó a las 3.30. Ok' -> ['¡Hola!', '¿Qué tal?', 'El Sr. López llegó a las 3.30. Ok']
```
Para cues partidos a mitad de frase conviene unir cues consecutivos hasta puntuación final (o ≤2 cues) antes de traducir y
repartir la traducción proporcionalmente a la longitud de cada cue; los diálogos con guion (`- ¿Vienes? - Sí.`) se traducen enteros.

## 5. Plan sugerido para mpvd
1. Dependencia opcional `translate = ["ctranslate2>=4.8,<5", "sentencepiece>=0.2"]` (extra, no en `dependencies`).
2. Catálogo en `mpvd/translate/models.py`: leer `index.json` (o copia vendorizada), preferir paquetes con `sentencepiece.model`,
   fijar `translate-es_en-1_0` y `translate-en_es-1_0` con SHA-256 en `vendor.lock`; descomprimir en `vendor/models/argos/<from>_<to>/`
   (dev) o `<data_dir>/models/argos` (XDG). Pivotar por inglés para pares sin paquete directo.
3. Servicio `translate.*` (JSON-RPC) que traduce SRT por lotes de 32 cues con `beam_size=2`, cachea por
   (hash, "translate", paquete, versión, beam) y produce un `.srt` nuevo para `sub-add` (mismo flujo que `asr.*`).
4. Estado del `.venv`: tras esta investigación se desinstalaron ctranslate2/sentencepiece/numpy/pyyaml/sacremoses (192 MB > 60 MB).

## 6. Implementación (H6, 2026-09-29)
- `mpvd/subs/translate.py`: `ArgosStore` (paquetes desempaquetados en `vendor/models/argos/<from>_<to>/` o `<data_dir>/models/argos`;
  zips en `vendor/dl`; `PINNED` es↔en 1.0 con SHA-256; índice oficial cacheado en `vendor/dl/argospm-index.json`), `ArgosEngine`
  (CTranslate2 int8, ≤2 traductores cargados, pivote por inglés), `group_cues`/`distribute`/`translate_cues` (agrupa ≤3 cues hasta
  puntuación final, separa diálogos con guion, reparte la traducción por longitud sin tocar tiempos).
- Servicio `subs.translate {srt, target, source=auto, path?, notify}` → `done` (caché) o `queued` + eventos `subs-translate`;
  `subs.translate.models [index]`, `subs.translate.download {source,target}` (eventos `subs-translate-model`), `subs.translate.remove`.
  Errores: `-32602` idioma de origen desconocido (la pista IA lo sabe; para otras, el menú Idioma), `-32002` con `data.missing` si
  faltan paquetes (mu-subs los descarga y reintenta solo).
- mu-subs: menú "Traducir la pista seleccionada a…" (lista de idiomas con estado del paquete), pista "Traducción (xx)" añadida al
  terminar, "Duales" = `secondary-sid` original + `sid` traducción. Runtime: extra `translate` del pyproject (`uv sync --extra translate`).
- Tests: `tests/test_subs_translate.py` (unidad + traducción real es→en), `tests/test_subs_service.py` (job, caché, errores),
  `tests/test_mu_subs.py` (menú, pista traducida, duales con `secondary-sub-text`).

## 7. Segundo motor: OPUS-MT "tc-big" (2026-09-30)
Diagnóstico (`tmp/diag-subs`, diálogo coloquial es→en de 39 cues): Argos 1.0 (OPUS-MT *base* de 2020, 94 MB, int8) acierta 1 de 10
giros ("No me tomes el pelo" → "Don't take my hair"); OPUS-MT **tc-big** acierta 5 de 10 ("Don't tease me"). NLLB (licencia CC-BY-NC)
y un LLM local (lento en CPU) se descartaron.

| Modelo (Helsinki-NLP, CC-BY 4.0) | Zip oficial (object storage de CSC) | SHA-256 | Tras convertir |
|---|---|---|---|
| `cat+oci+spa-eng` (es/ca→en) | `https://object.pouta.csc.fi/Tatoeba-MT-models/cat+oci+spa-eng/opusTCv20210807+bt_transformer-big_2022-03-13.zip` (863 152 463 B) | `a2d13b90c2d59fdd6b5db723fea7aaf32f1a5ef25d7119806c94df753e70f17f` | 234 MB |
| `eng-cat+oci+spa` (en→es/ca, token `>>spa<<`/`>>cat<<`) | `…/eng-cat+oci+spa/opusTCv20210807+bt_transformer-big_2022-03-13.zip` (862 895 279 B) | `a5f01f26b1f22cc840b9f94e98a4fe2b517fca85296f7f802771272fbfcd659e` | 234 MB |
| `fra-eng` (fr→en, sin token) | `…/fra-eng/opusTCv20210807+bt_transformer-big_2022-03-09.zip` (856 639 098 B) | `61f0684da189ef6cacbf4202284a390648312f13c87a37cde079f2c18829bac2` | 234 MB |
| `eng-fra` (en→fr, sin token) | `…/eng-fra/opusTCv20210807+bt_transformer-big_2022-03-09.zip` (856 664 877 B) | `65218bc83ae8983c0c78eb10a810f7d83756d62decb7144c2f5efaae6d4fae7f` | 234 MB |

### Francés (2026-10-01, H36/C6)
Con esos dos modelos el triángulo **es/en/fr** es OPUS-MT de punta a punta. No hay par directo es↔fr: listando el bucket
(`?delimiter=/&prefix=fra` y `prefix=spa-f`) solo existen `fra-eng`, `fra-cat` y `fra-ita`, así que es→fr pivota por inglés con
**dos tramos OPUS-MT** (antes uno de los dos tramos lo hacía Argos). Los dos son de un solo idioma a cada lado, así que **no**
llevan token `>>lang<<` (README y `preprocess.sh` del propio zip). Comprobado aquí descargando los zips, verificando el SHA-256,
convirtiendo y traduciendo: «The meeting starts at nine and nobody knows why.» → «La réunion commence à neuf heures et personne
ne sait pourquoi.», y de vuelta → «The meeting begins at 9 a.m. and no one knows why.».

- El zip trae el modelo Marian en fp32 (`.npz` de 930 MB, casi incompresible), `decoder.yml`, vocabulario YAML y `source.spm`/`target.spm`.
  `mpvd/subs/opus.py` lo descarga (progreso), verifica el SHA-256, extrae solo esos miembros, borra el zip, convierte con
  `ctranslate2.converters.OpusMTConverter(dir).convert(out, quantization="int8")` (solo numpy + pyyaml, ya dependencias de
  ctranslate2; sin torch; ≈30 s y ≈1,7 GB de RAM de pico en el i5-6200U), copia los `.spm`, LICENSE y README y escribe
  `mpv-uos.json` (URL, SHA-256, versión de CTranslate2). Destino: `<data_dir>/models/opus-mt/<id>/` (o `MPV_UOS_OPUS_MODELS`);
  un zip ya descargado en `vendor/dl` o `MPV_UOS_OPUS_DL` se reutiliza (y no se borra).
- Uso: `Translator(dir, compute_type="int8")`, piezas de `source.spm` (con `>>spa<<` delante para en→es), `beam_size=4`
  (`MPV_UOS_TRANSLATE_BEAM_OPUS`), `target.spm` para decodificar; normalización ligera del `preprocess.sh` oficial (comillas
  tipográficas, `…`, caracteres de control). ≈430 MB de RAM por modelo: se descarga de memoria al terminar cada trabajo.
- `subs.translate {…, engine: auto|argos|opus-big}`: el `TranslationRouter` elige motor por tramo — `opus-big` donde OPUS-MT cubre el
  par (con `auto`, solo si ya está descargado), Argos para el resto y para pivotar. **Desde H36/C6 el motor por defecto es
  `opus-big`** (el de más calidad, que es lo que se pidió): no significa «solo OPUS-MT», significa «OPUS-MT donde llegue» y Argos
  para los demás idiomas; `auto` queda como «no descargues nada grande». Si falta
  un modelo, error `-32002` con `data.missing = [[origen, destino, motor], …]`; mu-subs llama a
  `subs.translate.download {source, target, engine}` (trabajo con progreso, eventos `subs-translate-model`) y reintenta al acabar.
  `subs.translate.models` devuelve además `engines` (id, nombre, tamaño, pares y cuáles están descargados).
- Caché: `TRANSLATE_VERSION` 2; ruta `motor:origen_destino[+…]` y beams en los parámetros.
- Medido (i5-6200U, 3 hilos, carga ≈2): las 39 líneas del diagnóstico en 4,8 s con OPUS-MT beam 4 (≈110 ms/cue, 0,4 s de carga
  del modelo) frente a 1,0 s con Argos beam 2 (≈26 ms/cue); una película de ~1200 cues ≈ 2 min en segundo plano.

## 8. Reparto de la traducción entre cues (2026-09-30)
- `group_cues`: si ≥25 % de los cues acaban frase, une hasta el punto final (≤4 cues, pausa ≤1,5 s); si no hay puntuación (Whisper
  base), corta por pausas ≥0,6 s (≤4 cues). Un cue de diálogo (o que empieza con guion) nunca se pega a sus vecinos.
- Diálogos: los turnos se traducen por separado sin el guion y se reconstruyen con `- ` y el mismo separador (salto de línea o espacio)
  que el original; `load_cues` conserva los saltos de línea solo en cues de diálogo (`lines="dialogue"`).
- `distribute`: cada corte parte del punto proporcional (recalculado sobre lo que queda) y se mueve ±3 palabras al mejor límite:
  fin de frase o guion (10) > coma/punto y coma (8) > antes de conjunción (6); nunca tras artículo/preposición/conjunción (−10); la
  distancia en palabras resta. Si la traducción no llena todos los cues de la frase, el cue vacío cede su tiempo al vecino (antes
  se mostraba el texto original sin traducir). Casos del diagnóstico en `tests/test_subs_translate.py`.

