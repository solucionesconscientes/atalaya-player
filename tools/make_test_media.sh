#!/usr/bin/env bash
# Generate deterministic test media (git-ignored) for the headless tests.
# Requires ffmpeg (libx264, aac, flac) and espeak-ng. Output: tests/fixtures/media (or $1).
# Files:
#   video30.mkv     30 s testsrc2 640x360 25 fps + 440 Hz tone (h264 + aac)
#   chapters.mkv    same video with 3 chapters (0-10 s, 10-20 s, 20-30 s)
#   voz_es.flac     Spanish synthetic voice, 16 kHz mono   (+ voz_es.json: text, keywords)
#   voz_en.flac     English synthetic voice, 16 kHz mono   (+ voz_en.json)
#   voz_es_en.mkv   video with two audio tracks: spa (voz_es) and eng (voz_en)
#   serie/ep01..03.mkv  three "episodes" (~40 s): 1.5 s black+silence, the SAME 8 s intro (voice + melody over SMPTE bars),
#                   a different body each (voice + tone over testsrc2), 0.5 s silence, the SAME 6 s credits (melody over black)
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

# --- "episodes" for the intro/credits detector (H9) ------------------------------------------------------------------
mkdir -p serie tmp_ep
AUD=(-c:a aac -b:a 128k -ar 48000 -ac 2)
seg() { # seg <out> <duration> <video lavfi source> <audio lavfi/file args...>
  local out="$1" dur="$2" vsrc="$3"; shift 3
  "${FF[@]}" -f lavfi -i "$vsrc" "$@" -t "$dur" "${VID[@]}" "${AUD[@]}" -shortest "$out"
}
espeak-ng -v es -s 140 -p 60 -w tmp_ep/intro_voice.wav "Serie de prueba de MPV-UOS. Cabecera."
espeak-ng -v es -s 130 -p 40 -w tmp_ep/credits_voice.wav "Fin del episodio. Créditos."
# lead-in: 1.5 s black + silence
seg tmp_ep/lead.mkv 1.5 "color=c=black:size=640x360:rate=25" -f lavfi -i "anullsrc=r=48000:cl=stereo"
# intro: 8 s, SMPTE bars, melody (three tones) + voice
"${FF[@]}" -f lavfi -i "sine=frequency=523:duration=8" -f lavfi -i "sine=frequency=659:duration=8" \
  -f lavfi -i "sine=frequency=784:duration=8" -i tmp_ep/intro_voice.wav \
  -filter_complex "[0:a]volume=0.5,atrim=0:2.6[a0];[1:a]volume=0.5,atrim=0:2.6,adelay=2600|2600[a1];[2:a]volume=0.5,atrim=0:2.8,adelay=5200|5200[a2];[3:a]adelay=500|500,volume=1.4[v];[a0][a1][a2]amix=inputs=3:normalize=0[mel];[mel][v]amix=inputs=2:normalize=0,aresample=48000[a]" \
  -map "[a]" -t 8 "${AUD[@]}" tmp_ep/intro.wav.mka
seg tmp_ep/intro.mkv 8 "smptebars=size=640x360:rate=25" -i tmp_ep/intro.wav.mka -map 0:v -map 1:a
# credits: 6 s, black video, descending melody + voice
"${FF[@]}" -f lavfi -i "sine=frequency=440:duration=6" -f lavfi -i "sine=frequency=330:duration=6" -i tmp_ep/credits_voice.wav \
  -filter_complex "[0:a]volume=0.5,atrim=0:3[a0];[1:a]volume=0.5,atrim=0:3,adelay=3000|3000[a1];[2:a]adelay=300|300,volume=1.4[v];[a0][a1]amix=inputs=2:normalize=0[mel];[mel][v]amix=inputs=2:normalize=0,aresample=48000[a]" \
  -map "[a]" -t 6 "${AUD[@]}" tmp_ep/credits.wav.mka
seg tmp_ep/credits.mkv 6 "color=c=black:size=640x360:rate=25" -i tmp_ep/credits.wav.mka -map 0:v -map 1:a
seg tmp_ep/gap.mkv 0.5 "color=c=black:size=640x360:rate=25" -f lavfi -i "anullsrc=r=48000:cl=stereo"
BODIES=("Primer episodio. El detective llega a la ciudad y encuentra una carta misteriosa en el buzón."
        "Segundo episodio. La lluvia no para y el tren de las nueve nunca llega a la estación."
        "Tercer episodio. Alguien enciende la radio y suena una canción que nadie recuerda.")
FREQS=(220 300 380)
for i in 0 1 2; do
  n=$((i + 1))
  espeak-ng -v es -s 150 -w "tmp_ep/body$n.wav" "${BODIES[$i]}"
  # body bed: seeded coloured noise (different per episode, never "flat" for the fingerprinter) + a sine accent
  "${FF[@]}" -f lavfi -i "anoisesrc=color=brown:seed=$((1000 + n)):amplitude=0.25:duration=22" \
    -f lavfi -i "sine=frequency=${FREQS[$i]}:duration=22" -i "tmp_ep/body$n.wav" \
    -filter_complex "[0:a]volume=0.6[nz];[1:a]volume=0.15,tremolo=f=$((n + 1)):d=0.8[t];[2:a]adelay=1000|1000,volume=1.2[v];[nz][t][v]amix=inputs=3:normalize=0,aresample=48000[a]" \
    -map "[a]" -t 22 "${AUD[@]}" "tmp_ep/body$n.wav.mka"
  seg "tmp_ep/body$n.mkv" 22 "testsrc2=size=640x360:rate=25" -i "tmp_ep/body$n.wav.mka" -map 0:v -map 1:a
  printf "file '%s'\nfile '%s'\nfile '%s'\nfile '%s'\nfile '%s'\n" lead.mkv intro.mkv "body$n.mkv" gap.mkv credits.mkv > "tmp_ep/ep$n.txt"
  "${FF[@]}" -f concat -safe 0 -i "tmp_ep/ep$n.txt" "${VID[@]}" "${AUD[@]}" -metadata title="Serie de prueba · Episodio $n" "serie/ep0$n.mkv"
done
rm -rf tmp_ep

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
  "serie/ep01.mkv": {"duration": 38.0, "video": True, "audio_tracks": 1, "tolerance": 0.6,
                     "segments": {"intro": [1.5, 9.5], "credits": [32.0, 38.0]}},
  "serie/ep02.mkv": {"duration": 38.0, "video": True, "audio_tracks": 1, "tolerance": 0.6,
                     "segments": {"intro": [1.5, 9.5], "credits": [32.0, 38.0]}},
  "serie/ep03.mkv": {"duration": 38.0, "video": True, "audio_tracks": 1, "tolerance": 0.6,
                     "segments": {"intro": [1.5, 9.5], "credits": [32.0, 38.0]}},
}, open("manifest.json", "w"), ensure_ascii=False, indent=1)
PY
rm -f chapters.ffmeta
ls -la "$OUT"
echo "media OK"
