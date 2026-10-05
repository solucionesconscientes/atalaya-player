"""El trozo de Markdown que usa docs/ARQUITECTURA.md, convertido a HTML sin dependencias.

No es un conversor de Markdown: es el subconjunto exacto que ese documento usa (encabezados, párrafos, listas,
tablas, código entre comillas simples invertidas, negrita, cursiva y enlaces), y si aparece algo que no entiende
lo deja como texto. Escribirlo así —50 líneas y ninguna dependencia— es coherente con el resto del proyecto: el
servidor HTTP, los códigos QR y el cliente IPC también son propios porque son pocas líneas y una cosa menos que
mantener, traducir y auditar.
"""

from __future__ import annotations

import html
import re

NEGRITA = re.compile(r"\*\*(.+?)\*\*")
CURSIVA = re.compile(r"(?<!\*)\*([^*]+?)\*(?!\*)")
CODIGO = re.compile(r"`([^`]+?)`")
ENLACE = re.compile(r"\[([^\]]+?)\]\(([^)]+?)\)")


def en_linea(texto: str) -> str:
    """Lo de dentro de una línea. Se escapa ANTES de meter etiquetas, para que un `<` del texto no se cuele."""
    salida = html.escape(texto, quote=False)
    salida = CODIGO.sub(lambda m: f"<code>{m.group(1)}</code>", salida)
    salida = ENLACE.sub(lambda m: f'<a href="{_destino(m.group(2))}">{m.group(1)}</a>', salida)
    salida = NEGRITA.sub(lambda m: f"<strong>{m.group(1)}</strong>", salida)
    salida = CURSIVA.sub(lambda m: f"<em>{m.group(1)}</em>", salida)
    return salida


def _destino(href: str) -> str:
    """Los enlaces entre documentos del repositorio apuntan a .md; en la web se quedan como están (la página de
    arquitectura es la única que se publica) salvo el de sí misma."""
    return html.escape(href, quote=True)


def a_html(fuente: str) -> str:
    salida: list[str] = []
    lista: str | None = None
    tabla = False
    bloque = False
    for linea in fuente.splitlines():
        cruda = linea.rstrip()
        if cruda.startswith("```"):
            if bloque:
                salida.append("</code></pre>")
            else:
                salida.append("<pre><code>")
            bloque = not bloque
            continue
        if bloque:
            salida.append(html.escape(cruda, quote=False))
            continue
        if not cruda.strip():
            if lista:
                salida.append(f"</{lista}>")
                lista = None
            if tabla:
                salida.append("</tbody></table>")
                tabla = False
            continue
        if cruda.startswith("#"):
            nivel = len(cruda) - len(cruda.lstrip("#"))
            if lista:
                salida.append(f"</{lista}>")
                lista = None
            salida.append(f"<h{nivel}>{en_linea(cruda[nivel:].strip())}</h{nivel}>")
            continue
        if cruda.lstrip().startswith("|"):
            celdas = [c.strip() for c in cruda.strip().strip("|").split("|")]
            if all(set(c) <= set("-: ") for c in celdas):      # la línea de guiones que separa la cabecera
                continue
            if not tabla:
                salida.append("<table><tbody>")
                tabla = True
            salida.append("<tr>" + "".join(f"<td>{en_linea(c)}</td>" for c in celdas) + "</tr>")
            continue
        marca = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", cruda)
        if marca:
            quiere = "ol" if marca.group(2)[0].isdigit() else "ul"
            if lista and lista != quiere:
                salida.append(f"</{lista}>")
                lista = None
            if not lista:
                salida.append(f"<{quiere}>")
                lista = quiere
            salida.append(f"<li>{en_linea(marca.group(3))}</li>")
            continue
        if lista:
            salida.append(f"</{lista}>")
            lista = None
        salida.append(f"<p>{en_linea(cruda.strip())}</p>")
    if lista:
        salida.append(f"</{lista}>")
    if tabla:
        salida.append("</tbody></table>")
    if bloque:
        salida.append("</code></pre>")
    return "\n".join(salida)
