# Búsqueda semántica y capítulos automáticos — stack verificado (H10)

Investigación del 2026-09-29 contra fuentes reales (PyPI JSON, API de Hugging Face con `blobs=true`, pruebas locales en
`tmp/h10/venv` con Python 3.12.14 vía uv). Hardware de prueba: 4 núcleos, AVX2+FMA, **sin AVX-512/VNNI**, sin GPU.
Restricciones: sin torch, dependencias mínimas, local-first, modelos descargados bajo demanda y fijados por SHA-256.

## 1. Paquetes (PyPI, linux x86_64, cp312)

| Paquete | Versión | Wheel | Tamaño | SHA-256 del wheel | Dependencias |
|---|---|---|---|---|---|
| `onnxruntime` | 1.30.0 | `onnxruntime-1.30.0-cp312-cp312-manylinux_2_28_x86_64.whl` | 23,6 MB (62 MB instalado) | `fa688e7891a6aa206636fe7372e27ee75fd17713289f6b4fc7b190e0a7de9328` | flatbuffers, numpy>=1.21.6, packaging, protobuf>=4.25.8 |
| `numpy` | 2.5.3 | `numpy-2.5.3-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl` | 16,7 MB | `b7e18c623bb5c95acb3b3328861272816ba199fb531921c5d6d0b675f1fde9e3` | ninguna |
| `sentencepiece` | 0.2.2 | `sentencepiece-0.2.2-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl` | 1,4 MB | `c8a168b040bc61681293f79a949b5d911c8e25086f4260285b8d97ab5f1195da` | ninguna |
| `tokenizers` | 0.23.2 | `tokenizers-0.23.2-cp310-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl` | 3,4 MB | `41c2f84d172449b4dadb9cdc508e3e364076613c35b16e76ecfe47a60d1e3305` | **huggingface-hub<2.0** → click, filelock, fsspec, hf-xet (12 MB), httpx, pyyaml, tqdm, typing-extensions |
| `sqlite-vec` | 0.1.9 | `sqlite_vec-0.1.9-py3-none-manylinux_2_17_x86_64...whl` | 0,2 MB | `1515727990b49e79bcaf75fdee2ffc7d461f8b66905013231251f1c8938e7786` | ninguna |
| `optimum` | 2.3.0 | — | — | — | **requiere `torch>=1.11` y `transformers`** → descartado |

Conclusiones:
- `onnxruntime` solo CPU es viable: Providers disponibles `CPUExecutionProvider` (y Azure, irrelevante).
- `tokenizers` solo (sin transformers) **sí** carga `tokenizer.json`, pero arrastra 10 paquetes vía huggingface-hub y consume
  **~270 MB de RSS** por el vocabulario de 250k del XLM-R. **Sustituido por `sentencepiece`** (ya en el extra `translate`):
  mismos ids verificados (6 frases con acentos, CJK, símbolos, espacios dobles, textos largos truncados), 5 MB de modelo,
  ~70 MB de RSS, 0 dependencias. Mapeo fairseq de XLM-R: `<s>=0 <pad>=1 </s>=2 <unk>=3`; pieza spm `p` → `p+1`, `<unk>` spm (0) → 3.

## 2. Modelos de embeddings multilingües (API HF, `blobs=true`)

Los cuatro repos son el **mismo backbone** (BERT 12 capas, hidden 384, vocab 250 037, XLM-R tokenizer; `sentencepiece.bpe.model`
idéntico en los cuatro: `cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865`). Dimensión **384**, pooling **mean**
(`1_Pooling/config.json: pooling_mode_mean_tokens=true`), salida `last_hidden_state`, entradas `input_ids, attention_mask, token_type_ids` (int64).

| Repo | Licencia | max_seq_length | Prefijo | Archivos ONNX relevantes (tamaño · lfs.sha256) |
|---|---|---|---|---|
| `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | Apache-2.0 | 128 | no | `onnx/model.onnx` 470 MB `10f7a088…`; `onnx/model_O4.onnx` (fp16) 235 MB; `onnx/model_quint8_avx2.onnx` 118 453 870 B `98a01d88b7de996cdea58c32ca71208c09968d143798814b2ea09d3439dc334f`; `onnx/model_qint8_avx512_vnni.onnx` 118 MB `783fea82…` |
| `Xenova/paraphrase-multilingual-MiniLM-L12-v2` | (sin declarar; conversión del anterior, Apache-2.0) | 128 | no | `onnx/model_quantized.onnx` 118 308 126 B `66fc00f5f29afcaff34092e1bdd20008ca3918265a82fb9695a551e510cc4ebc`; `onnx/model_int8.onnx` 118 MB `d6ea442f…`; `onnx/model_fp16.onnx` 235 MB; `onnx/model_q4f16.onnx` 205 MB |
| `intfloat/multilingual-e5-small` | MIT | 512 | **sí** (`query: ` / `passage: `) | `onnx/model.onnx` 470 MB `ca456c06…`; `onnx/model_O4.onnx` 235 MB; `onnx/model_qint8_avx512_vnni.onnx` 118 MB `dd476dd0…` (**no hay variante avx2**) |
| `Xenova/multilingual-e5-small` | (sin declarar; conversión, MIT) | 512 | sí | `onnx/model_quantized.onnx` 118 308 185 B `f80102d3f2a1229f387d3c81909990d8945513e347b0eab049f7de3c6f98c193`; `onnx/model_int8.onnx` 118 MB `4d24e2bc…`; `onnx/model_fp16.onnx` 235 MB |

URLs de descarga (patrón `https://huggingface.co/<repo>/resolve/main/<archivo>`), todas verificadas con `sha256sum` tras descargar:

```
https://huggingface.co/Xenova/paraphrase-multilingual-MiniLM-L12-v2/resolve/main/onnx/model_quantized.onnx   66fc00f5f29afcaff34092e1bdd20008ca3918265a82fb9695a551e510cc4ebc
https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2/resolve/main/sentencepiece.bpe.model   cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865
https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2/resolve/main/onnx/model_quint8_avx2.onnx   98a01d88b7de996cdea58c32ca71208c09968d143798814b2ea09d3439dc334f
https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2/resolve/main/tokenizer.json   2c3387be76557bd40970cec13153b3bbf80407865484b209e655e5e4729076b8
https://huggingface.co/Xenova/multilingual-e5-small/resolve/main/onnx/model_quantized.onnx   f80102d3f2a1229f387d3c81909990d8945513e347b0eab049f7de3c6f98c193
https://huggingface.co/Xenova/multilingual-e5-small/resolve/main/tokenizer.json   0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39
```

Consulta de metadatos: `curl -s "https://huggingface.co/api/models/<repo>?blobs=true"` → `siblings[].lfs.sha256` (solo para archivos LFS;
los JSON pequeños no traen sha).

## 3. Prueba real (venv temporal, 4 núcleos con carga ≈4 de los tests en paralelo → cifras conservadoras)

```
mkdir -p tmp/h10 && uv venv tmp/h10/venv --python 3.12
uv pip install --python tmp/h10/venv/bin/python onnxruntime sentencepiece numpy sqlite-vec   # (+ tokenizers solo para comparar)
THREADS=2 tmp/h10/venv/bin/python tmp/h10/bench.py tmp/h10/models/minilm-xenova
```

| Modelo (int8 dinámico) | Carga | RSS tras carga / pico | Ventana ~80 tokens (lote 32) | Segmento Whisper ~15 tokens | ES↔EN equivalentes | No relacionadas |
|---|---|---|---|---|---|---|
| **Xenova MiniLM `model_quantized` + sentencepiece, 2 hilos** | **1,4 s** | **268 / 308 MB** | **31–33 ms/ítem** (30 ítems/s) | **6 ms/ítem** (160/s) | 0,981 · 0,858 | 0,065 · 0,089 · −0,028 · −0,080 |
| ídem, 4 hilos | 1,4 s | 268 / 308 MB | 31 ms | 5,6 ms | ídem | ídem |
| ídem, 1 hilo | — | — | 57 ms | 11 ms | ídem | ídem |
| ST MiniLM `model_quint8_avx2` (+tokenizers) | 2,7 s | 472 / 567 MB | 66–81 ms | 12–15 ms | 0,984 · 0,878 | 0,068 · 0,072 · −0,028 · −0,083 |
| Xenova e5-small `model_quantized` (+tokenizers, `query: `) | 1,9 s | 479 / 556 MB | 36–45 ms | 8–10 ms | 0,889 · 0,920 | **0,779 · 0,742 · 0,701 · 0,753** |

- Check cruzado (par traducido > cualquier par no relacionado + 0,2): **PASS** en MiniLM, **FAIL** en e5-small. e5 ordena bien
  (el traducido sigue siendo el más cercano) pero su rango de coseno está comprimido (0,70–0,92), típico de e5; para umbrales por
  percentil funcionaría, pero MiniLM da márgenes de ~0,8 y no necesita prefijos.
- Sorpresa: `model_quint8_avx2` (u8s8 con `reduce_range`) es **2× más lento** que el `model_quantized` de Xenova (u8u8) en una CPU
  AVX2 sin VNNI. Y el `qint8_avx512_vnni` de intfloat no aplica al hardware objetivo.
- Más de 2 hilos no aporta nada; dejar `intra_op_num_threads=2` para no pelear con whisper.cpp.
- Extrapolación: película de 2 h → ~1 500 segmentos Whisper ≈ 10 s; 120 ventanas de 60 s ≈ 4 s. Vectores: 1 500 × 384 × 4 B ≈ 2,3 MB.

Código de la prueba (`tmp/h10/bench.py`; los modelos quedan en `tmp/h10/models/{minilm-xenova,minilm,e5s}`):

```python
#!/usr/bin/env python3
"""Benchmark: ONNX Runtime + sentencepiece (no torch, no transformers) multilingual embeddings.

Usage: THREADS=2 python bench.py <model_dir> [--e5]
  model_dir: model*.onnx + sentencepiece.bpe.model (or tokenizer.json as fallback)
  --e5     : prepend "query: " and use max_len 512 (multilingual-e5 convention)
"""
import glob, os, resource, sys, time
import numpy as np
import onnxruntime as ort

model_dir = sys.argv[1]
is_e5 = "--e5" in sys.argv
threads = int(os.environ.get("THREADS", "2"))
max_len = 512 if is_e5 else 128  # max_seq_length from sentence_bert_config.json

# XLM-R special ids (fairseq layout): <s>=0 <pad>=1 </s>=2 <unk>=3; spm piece p -> p+1, spm <unk>(0) -> 3
CLS, PAD, SEP, UNK = 0, 1, 2, 3


def rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


class SpmTokenizer:
    """sentencepiece.bpe.model (5 MB, ~70 MB RSS) -> same ids as tokenizer.json (verified)."""

    def __init__(self, path):
        import sentencepiece as spm
        self.sp = spm.SentencePieceProcessor(model_file=path)

    def encode_batch(self, texts, max_len):
        rows = []
        for ids in self.sp.encode(texts, out_type=int):
            ids = [UNK if i == 0 else i + 1 for i in ids][: max_len - 2]
            rows.append([CLS] + ids + [SEP])
        return rows


class JsonTokenizer:
    """tokenizer.json via `tokenizers` (Rust; ~270 MB RSS, pulls huggingface-hub). Fallback only."""

    def __init__(self, path):
        from tokenizers import Tokenizer
        self.tok = Tokenizer.from_file(path)

    def encode_batch(self, texts, max_len):
        self.tok.enable_truncation(max_length=max_len)
        self.tok.no_padding()
        return [e.ids for e in self.tok.encode_batch(texts)]


class Embedder:
    def __init__(self, model_dir):
        model_path = glob.glob(os.path.join(model_dir, "model*.onnx"))[0]
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        so.inter_op_num_threads = 1
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.sess = ort.InferenceSession(model_path, so, providers=["CPUExecutionProvider"])
        self.input_names = [i.name for i in self.sess.get_inputs()]
        spm_path = os.path.join(model_dir, "sentencepiece.bpe.model")
        self.tok = SpmTokenizer(spm_path) if os.path.exists(spm_path) else JsonTokenizer(os.path.join(model_dir, "tokenizer.json"))

    def encode(self, texts, batch_size=32):
        if is_e5:
            texts = ["query: " + t for t in texts]
        out = []
        for i in range(0, len(texts), batch_size):
            rows = self.tok.encode_batch(texts[i:i + batch_size], max_len)
            L = max(len(r) for r in rows)
            ids = np.full((len(rows), L), PAD, dtype=np.int64)
            mask = np.zeros((len(rows), L), dtype=np.int64)
            for j, r in enumerate(rows):
                ids[j, : len(r)] = r
                mask[j, : len(r)] = 1
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self.input_names:
                feed["token_type_ids"] = np.zeros_like(ids)
            hid = self.sess.run(None, feed)[0]  # (batch, seq, 384) last_hidden_state
            m = mask[..., None].astype(np.float32)
            emb = (hid * m).sum(1) / np.clip(m.sum(1), 1e-9, None)  # mean pooling (1_Pooling/config.json)
            out.append(emb / np.linalg.norm(emb, axis=1, keepdims=True))
        return np.concatenate(out).astype(np.float32)


if __name__ == "__main__":
    t0 = time.perf_counter()
    E = Embedder(model_dir)
    t_load = time.perf_counter() - t0
    print(f"model={model_dir} tokenizer={type(E.tok).__name__} inputs={E.input_names} threads={threads}")
    print(f"load: {t_load*1000:.0f} ms  RSS after load: {rss_mb():.0f} MB")

    sents = [
        "El gato duerme sobre el sofá toda la tarde.",           # 0 ES
        "The cat sleeps on the couch all afternoon.",            # 1 EN (translation of 0)
        "La reunión de presupuesto se aplaza hasta el lunes.",   # 2 ES
        "The budget meeting is postponed until Monday.",         # 3 EN (translation of 2)
        "Quantum computers use qubits instead of classical bits.",  # 4 EN unrelated
    ]
    E.encode(sents[:1])  # warm-up
    emb = E.encode(sents)
    sim = emb @ emb.T
    np.set_printoptions(precision=3, suppress=True)
    print("cosine matrix:\n", sim)
    print(f"ES<->EN equivalent: 0-1={sim[0,1]:.3f} 2-3={sim[2,3]:.3f}")
    print(f"unrelated:          0-2={sim[0,2]:.3f} 1-3={sim[1,3]:.3f} 0-4={sim[0,4]:.3f} 2-4={sim[2,4]:.3f}")
    ok = min(sim[0, 1], sim[2, 3]) > max(sim[0, 2], sim[1, 3], sim[0, 4], sim[2, 4]) + 0.2
    print("cross-lingual check:", "PASS" if ok else "FAIL")

    def bench(label, batch):
        n_tok = np.mean([len(r) for r in E.tok.encode_batch(batch, max_len)])
        t0 = time.perf_counter()
        for _ in range(3):
            E.encode(batch)
        dt = (time.perf_counter() - t0) / 3
        print(f"{label}: 32 x ~{int(n_tok)} tokens: {dt*1000:.0f} ms total, {dt*1000/32:.1f} ms/item, "
              f"{32/dt:.1f} items/s  RSS peak: {rss_mb():.0f} MB")

    window = ("Hoy vamos a hablar de cómo configurar el reproductor para que cargue subtítulos "
              "automáticamente y de los atajos de teclado más útiles para navegar por los capítulos. ")
    bench("windows ~40 words", [window * 2 + f" (frase {i})" for i in range(32)])
    bench("whisper segments  ", [f"Y entonces le dije que no podía venir el martes, frase {i}." for i in range(32)])
```

## 4. sqlite-vec

- Wheel `py3-none-manylinux_2_17_x86_64` 0.2 MB, sin dependencias, válido para 3.12.
- `enable_load_extension` existe tanto en el Python 3.12.14 de uv (SQLite 3.53.1) como en el 3.14 del sistema (SQLite 3.46.1).
  Probado: `sqlite_vec.load(c)` → `vec_version() = v0.1.9`, tabla `vec0` y KNN funcionan.

```python
import sqlite3, sqlite_vec, numpy as np
c = sqlite3.connect(":memory:")
c.enable_load_extension(True); sqlite_vec.load(c); c.enable_load_extension(False)
c.execute("create virtual table seg using vec0(id integer primary key, emb float[384])")
c.execute("insert into seg(id, emb) values (?, ?)", (1, vec.astype(np.float32).tobytes()))
# KNN (L2; con vectores normalizados el orden coincide con el coseno). `k = ?` es obligatorio en vec0.
c.execute("select id, distance from seg where emb match ? and k = 10 order by distance", (q.tobytes(),)).fetchall()
# o coseno explícito sobre cualquier tabla:
c.execute("select id, vec_distance_cosine(emb, ?) d from seg order by d limit 10", (q.tobytes(),))
```

Recomendación: **NumPy como motor principal** y sqlite-vec fuera del extra por ahora. Un archivo tiene ~1 500 vectores (2,3 MB);
`emb @ q` sobre esa matriz tarda <1 ms y evita otra extensión nativa y otra columna virtual en la caché. Guardar los embeddings
como BLOB float32 (`np.tobytes()`) en la caché SQLite existente, por `(hash_archivo, "embeddings", modelo, versión, parámetros)`.
sqlite-vec queda como opción documentada si algún día se indexa toda la biblioteca (>50k vectores) o hace falta KNN desde SQL.

## 5. Recomendación final

Extra opcional en `pyproject.toml` (comparte `sentencepiece` con `translate`):

```toml
[project.optional-dependencies]
semantic = [
    "onnxruntime>=1.30,<2",
    "sentencepiece>=0.2",
    "numpy>=2.0",
]
```

Modelo: **`Xenova/paraphrase-multilingual-MiniLM-L12-v2` · `onnx/model_quantized.onnx`** (118 308 126 B,
`66fc00f5f29afcaff34092e1bdd20008ca3918265a82fb9695a551e510cc4ebc`) + `sentencepiece.bpe.model` de
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (5 069 051 B, `cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865`).
Dim 384, mean pooling + normalización L2, sin prefijos, max_len 128 (las ventanas largas se parten en frases y se promedian),
Apache-2.0. Descarga bajo demanda con el mismo patrón `(url, sha256)` de `mpvd/subs/translate.py`. Alternativa documentada si se
quiere más contexto por ventana (512 tokens): `Xenova/multilingual-e5-small/onnx/model_quantized.onnx` con prefijos `query:`/`passage:`.

Capítulos por cambio de tema (sobre los segmentos Whisper con tiempos):
1. **Ventanas**: agrupar segmentos en ventanas de 45 s (rango 30–60) con solape del 50 %; cada ventana = media de los embeddings de sus
   frases (cada frase ≤128 tokens), normalizada.
2. **Señal**: `d[i] = 1 − cos(w[i], w[i+1])`; opcionalmente comparar el promedio de las 2–3 ventanas anteriores con las 2–3 siguientes
   (TextTiling) para reducir ruido.
3. **Suavizado**: media móvil de 3 puntos sobre `d`.
4. **Umbral**: candidatos = máximos locales de `d` por encima del percentil 85–90 (adaptativo por archivo; en contenidos muy
   homogéneos no habrá capítulos y es correcto).
5. **Mínimo 3 min** por capítulo: greedy de izquierda a derecha, descartando candidatos a <180 s del último corte aceptado (si hay
   dos cerca, gana el de mayor `d`). Cerrar cortes al inicio del segmento Whisper más cercano.
6. **Título**: frase del capítulo más cercana al centroide (coseno) recortada a ~60 caracteres; si empata o es muy corta, primeras
   6–8 palabras de la primera frase. Sin LLM; los títulos son "citas".
7. **Salida**: lista `[{start, end, title}]` → `chapter-list` de mpv (o archivo de capítulos), cacheada por
   `(hash_archivo, "chapters", modelo, versión, parámetros)`; el usuario puede regenerar con otro umbral desde el menú.

Búsqueda semántica: embeddings por segmento Whisper (o por pares de segmentos si son <4 palabras), consulta con el mismo modelo,
`scores = emb @ q`, top-k con `np.argpartition`, y fusión opcional con la búsqueda por texto ya existente (`asr.search`) sumando
puntuaciones normalizadas. Un mismo modelo y una sola pasada sirven para capítulos y búsqueda.
