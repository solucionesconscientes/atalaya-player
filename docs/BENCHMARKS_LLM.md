# Banco de pruebas del resumen en prosa (modelo local)

Medido el 2026-10-01 en `pc-latitude5480` (Intel i5-6200U, 4 núcleos, 23 GB RAM, **sin GPU**), con
`llama.cpp b11319` (binario de CPU de la release oficial), 3 hilos, `temp 0.3`, contexto 4096, y **con el portátil
ocupado con otras cosas** (carga de 1 min entre 8 y 12): los tiempos de un equipo libre serán algo mejores.

## Qué se midió
Entrada: el **índice de nivel 1** (`recap.outline`) de los primeros 15 minutos de una charla real en español bajada con
yt-dlp (subtítulos automáticos). Es la entrada de verdad del nivel 2: el modelo no ve el subtítulo entero, ve el índice
con sus minutos. Salida: un resumen corto (3-4 frases) y uno largo (8-12).

Lo que decide no es solo el tiempo: es si el modelo **respeta los minutos**. Cada salida se pasó por
`recap.validate_marks`, que comprueba cada `[mm:ss]` contra el subtítulo.

| Modelo (Q4_K_M) | Tamaño | Corto | Largo | Generación | Marcas inventadas | Veredicto |
|---|---|---|---|---|---|---|
| **gemma-3-1b-it** | 806 MB | **36-41 s** | **45 s** | 5,6-6,2 tok/s | 0 | **el de serie**: el doble de rápido y escribe español correcto |
| qwen2.5-3b-instruct | 1,9 GB | 79 s | 106 s | 2,6-2,8 tok/s | 0 | opción «escribe mejor»: prosa más limpia, el doble de tiempo |
| qwen2.5-1.5b-instruct | 1,1 GB | 69 s | 100 s | 4,7 tok/s | 0 | **descartado**: repetía frases enteras y escribía rangos `[0:04-0:36]` |

## Lo que se aprendió (y cambió el código)
1. **Ninguno de los tres se inventó un minuto.** Con el índice delante, los tres usaron marcas que existían: el
   validador casi nunca tiene que quitar nada. Aun así se queda, porque el día que un modelo falle, una marca falsa es
   lo peor que puede pasar (parece que el programa miente).
2. **Hay que pedir la marca explícitamente.** Con el prompt inicial, gemma escribió el resumen **corto sin ninguna
   marca** (un resumen sin minutos no sirve para lo que se quiere: pulsar y saltar). Cambiando el prompt a «cada frase
   EMPIEZA con su marca [mm:ss] del índice», 5 marcas de 5 válidas en 41 s. Eso está ahora en `mpvd/llm.py`.
3. **Los rangos existen.** qwen2.5-1.5b escribía `[0:04-0:36]`. El validador los acepta y los deja en su **instante
   inicial**: el menú solo puede llevarte a un sitio.
4. **La salida de `llama-cli` no es solo la respuesta.** Su interfaz de chat escribe en la misma salida el cartel, los
   datos del modelo, la lista de órdenes y el **eco recortado** del prompt (acabado en `(truncated)`), y al final una
   línea `[ Prompt: … t/s | Generation: … t/s ]` con coma decimal. La primera versión metió todo eso dentro del resumen.
   `mpvd.llm.clean_output` corta por ahí y hay un test que fija ese contrato, porque es lo que se romperá al actualizar.
5. **Un proceso por resumen, no un servidor.** 40 s cada pocas horas no justifica tener 1 GB de RAM ocupado y un puerto
   abierto todo el día (el mismo razonamiento que ADR-024 con whisper-cli).

## Cómo repetirlo
```bash
tools/vendor_llama.sh                     # llama.cpp de CPU en vendor/llama/bin (fijado en vendor.lock)
# el modelo se baja desde el menú (Resumen → modelo) o:
.venv/bin/python -c "import asyncio,pathlib;from mpvd.llm import *; \
  asyncio.run(LlmStore([pathlib.Path('vendor/models/llm')]).download(DEFAULT_MODEL, lambda f,m: print(f,m)))"
```
Y luego, con un vídeo con subtítulos abierto, `alt+I` → *Resumen en prosa*. El tiempo que tarda se dice antes de empezar.
