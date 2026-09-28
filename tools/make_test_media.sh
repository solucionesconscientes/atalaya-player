#!/usr/bin/env bash
# Generate deterministic test media (git-ignored) for the headless tests.
# Requires ffmpeg (libx264, aac, flac) and espeak-ng. Output: tests/fixtures/media (or $1).
# Files:
#   video30.mkv     30 s testsrc2 640x360 25 fps + 440 Hz tone (h264 + aac)
#   chapters.mkv    same video with 3 chapters (0-10 s, 10-20 s, 20-30 s)
#   voz_es.flac     Spanish synthetic voice, 16 kHz mono   (+ voz_es.json: text, keywords)
#   voz_en.flac     English synthetic voice, 16 kHz mono   (+ voz_en.json)
#   voz_es_en.mkv   video with two audio tracks: spa (voz_es) and eng (voz_en)
#   manifest.json   list of files with expected properties
set -euo pipefail
ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
OUT="${1:-$ROOT/tests/fixtures/media}"
mkdir -p "$OUT"
cd "$OUT"
FF=(ffmpeg -y -hide_banner -v error)
VID=(-c:v libx264 -preset veryfast -crf 30 -pix_fmt yuv420p)

echo "media → $OUT"
"${FF[@]}" -f lavfi -i "testsrc2=size=640x360:rate=25:duration=30" \
  -f lavfi -i "sine=frequency=440:sample_rate=48000:duration=30" \
  "${VID[@]}" -c:a aac -b:a 96k -shortest video30.mkv

cat > chapters.ffmeta <<'META'
;FFMETADATA1
title=MPV-UOS prueba con capítulos
[CHAPTER]
TIMEBASE=1/1000
START=0
END=10000
title=Capítulo uno
[CHAPTER]
TIMEBASE=1/1000
START=10000
END=20000
title=Capítulo dos
[CHAPTER]
TIMEBASE=1/1000
START=20000
END=30000
title=Capítulo tres
META
"${FF[@]}" -i video30.mkv -i chapters.ffmeta -map 0 -map_metadata 1 -map_chapters 1 -c copy chapters.mkv

ES_TEXT="Bienvenido a MPV-UOS, el reproductor del futuro. El rápido zorro marrón salta sobre el perro perezoso. Hoy es un buen día para ver una película con subtítulos."
EN_TEXT="Welcome to MPV-UOS, the player of the future. The quick brown fox jumps over the lazy dog. Today is a good day to watch a movie with subtitles."
espeak-ng -v es -s 150 -w tmp_es.wav "$ES_TEXT"
espeak-ng -v en-us -s 150 -w tmp_en.wav "$EN_TEXT"
"${FF[@]}" -i tmp_es.wav -ar 16000 -ac 1 -c:a flac voz_es.flac
"${FF[@]}" -i tmp_en.wav -ar 16000 -ac 1 -c:a flac voz_en.flac
rm -f tmp_es.wav tmp_en.wav

dur() { ffprobe -v error -show_entries format=duration -of csv=p=0 "$1"; }
D_ES=$(dur voz_es.flac); D_EN=$(dur voz_en.flac)
D_MAX=$(python3 -c "print(max(float('$D_ES'), float('$D_EN')) + 0.5)")
"${FF[@]}" -f lavfi -i "testsrc2=size=640x360:rate=25" -i voz_es.flac -i voz_en.flac \
  -map 0:v -map 1:a -map 2:a "${VID[@]}" -c:a aac -b:a 96k -t "$D_MAX" \
  -metadata:s:a:0 language=spa -metadata:s:a:0 title="Español" \
  -metadata:s:a:1 language=eng -metadata:s:a:1 title="English" voz_es_en.mkv

python3 - "$ES_TEXT" "$EN_TEXT" "$D_ES" "$D_EN" "$D_MAX" <<'PY'
import json, sys
es, en, d_es, d_en, d_max = sys.argv[1:6]
json.dump({"lang": "es", "text": es, "keywords": ["bienvenido", "reproductor", "futuro", "zorro", "perro", "película", "subtítulos"]},
          open("voz_es.json", "w"), ensure_ascii=False, indent=1)
json.dump({"lang": "en", "text": en, "keywords": ["welcome", "player", "future", "fox", "dog", "movie", "subtitles"]},
          open("voz_en.json", "w"), ensure_ascii=False, indent=1)
json.dump({
  "video30.mkv": {"duration": 30.0, "video": True, "audio_tracks": 1},
  "chapters.mkv": {"duration": 30.0, "video": True, "chapters": ["Capítulo uno", "Capítulo dos", "Capítulo tres"]},
  "voz_es.flac": {"duration": float(d_es), "video": False, "audio_tracks": 1, "sample_rate": 16000, "channels": 1},
  "voz_en.flac": {"duration": float(d_en), "video": False, "audio_tracks": 1, "sample_rate": 16000, "channels": 1},
  "voz_es_en.mkv": {"duration": float(d_max), "video": True, "audio_tracks": 2, "audio_langs": ["spa", "eng"]},
}, open("manifest.json", "w"), ensure_ascii=False, indent=1)
PY
rm -f chapters.ffmeta
ls -la "$OUT"
echo "media OK"
