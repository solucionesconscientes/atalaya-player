# Parches a componentes de terceros

La regla es no editar uosc (docs/ARQUITECTURA.md): solo su API pública. Cuando algo no se puede hacer con esa API, se
deja aquí el parche, con el motivo en un ADR, y `tools/vendor.sh` lo vuelve a aplicar después de descomprimir el
componente. Así actualizar uosc no pierde el cambio sin que nadie se entere.

| Parche | Componente | Qué añade | Decisión |
|---|---|---|---|
| `uosc-5.13.0-panel.patch` | uosc 5.13.0 (`scripts/uosc/elements/Menu.lua`) | Campos de menú `panel_width` (tanto por uno del ancho), `panel_align = 'left'` (pegar a la izquierda) y `follow_selection` (al actualizar, mover el resalte a `selected_index` sin perder búsqueda ni scroll) | ADR-136, ADR-138 |

Se aplican solos al vendorizar:

```sh
tools/vendor.sh                     # descomprime uosc y aplica patches/*.patch
patch -p1 < patches/uosc-5.13.0-panel.patch   # o a mano, desde la raíz del repositorio
```
