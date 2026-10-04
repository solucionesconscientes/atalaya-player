"""H72 · el paquete .deb: lo que tiene que llevar dentro y lo que NO tiene que llevar.

Construir el paquete tarda un minuto y pide red la primera vez, así que estos tests solo corren si hay uno hecho
en `dist/` o si se pide con MU_BUILD_DEB=1 (igual que los del AppImage). Lo que se comprueba es lo que ya ha
fallado alguna vez o lo que, si falla, no se nota hasta que alguien instala el paquete:

* los **catálogos de idiomas**, porque el AppImage se construyó una vez sin ellos y todo caía al castellano sin
  decir nada (ese respaldo silencioso es justo lo que se diseñó, así que no hay síntoma);
* la **regla de sudoers**, porque un fichero roto en /etc/sudoers.d puede dejar a alguien sin poder usar sudo, y
  porque tiene que autorizar SOLO el ayudante del despertador y no `rtcwake` entero;
* los **permisos** (0644/0755) y que no viaje ningún `__pycache__`, que es lo que mete una comprobación mal hecha;
* que `Depends` no fije una versión de mpv: en Debian 13 o Raspberry Pi OS el del sistema puede ser más viejo, y
  la decisión fue instalarse y avisar al arrancar en vez de negarse a instalar.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from tests.conftest import ROOT

DIST = ROOT / "dist"
VERSION = json.loads(subprocess.run(
    ["python3", "-c", "import json,tomllib,sys;"
     "print(json.dumps(tomllib.load(open('pyproject.toml','rb'))['project']))"],
    cwd=ROOT, capture_output=True, text=True, check=True).stdout)["version"]
PAQUETE = DIST / f"atalaya-player_{VERSION}_amd64.deb"


def construir() -> Path:
    if PAQUETE.exists():
        return PAQUETE
    if os.environ.get("MU_BUILD_DEB") != "1":
        pytest.skip(f"sin {PAQUETE.name} (tools/build_deb.sh o MU_BUILD_DEB=1)")
    subprocess.run([str(ROOT / "tools/build_deb.sh"), "--arch", "amd64"], cwd=ROOT, check=True,
                   stdout=subprocess.DEVNULL)
    return PAQUETE


@pytest.fixture(scope="module")
def deb() -> Path:
    if not shutil.which("dpkg-deb"):
        pytest.skip("sin dpkg-deb")
    return construir()


@pytest.fixture(scope="module")
def contenido(deb: Path) -> dict[str, tarfile.TarInfo]:
    salida = subprocess.run(["dpkg-deb", "--fsys-tarfile", str(deb)], capture_output=True, check=True).stdout
    import io
    with tarfile.open(fileobj=io.BytesIO(salida)) as tf:
        return {m.name.lstrip("."): m for m in tf.getmembers()}


@pytest.fixture(scope="module")
def control(deb: Path) -> dict[str, str]:
    texto = subprocess.run(["dpkg-deb", "--field", str(deb)], capture_output=True, text=True, check=True).stdout
    campos = {}
    clave = None
    for linea in texto.splitlines():
        if linea[:1].isspace() and clave:
            campos[clave] += " " + linea.strip()
        elif ":" in linea:
            clave, _, valor = linea.partition(":")
            campos[clave] = valor.strip()
    return campos


def test_el_paquete_se_identifica_y_no_fija_la_version_de_mpv(control):
    assert control["Package"] == "atalaya-player"
    assert control["Architecture"] == "amd64"
    # mpv SIN versión mínima: la decisión de H72 fue instalarse y avisar, no negarse a instalar
    assert "mpv" in control["Depends"] and "mpv (" not in control["Depends"], control["Depends"]
    assert "ffmpeg" in control["Depends"], "grabar y extraer audio es ffmpeg"
    assert "Homepage" in control and control["Homepage"].startswith("http")


def test_los_catalogos_de_idiomas_viajan_dentro(contenido):
    """El fallo que ya pasó con el AppImage: sin locales/, todo cae al castellano y nadie se entera."""
    for lang in ("en", "fr"):
        ruta = f"/usr/lib/mpv-uos/locales/{lang}.json"
        assert ruta in contenido, sorted(k for k in contenido if "locales" in k)
        assert contenido[ruta].size > 10000


def test_lo_que_hace_falta_para_arrancar_esta(contenido):
    for ruta in ("/usr/bin/mpv-uos", "/usr/bin/atalaya", "/usr/lib/mpv-uos/bin/mpv-uos",
                 "/usr/lib/mpv-uos/bin/wake", "/usr/lib/mpv-uos/python/bin/python3.12",
                 "/usr/lib/mpv-uos/.venv/bin/python", "/usr/lib/mpv-uos/mpv-config/mpv.conf",
                 "/usr/lib/mpv-uos/mpv-config/scripts/mu-core.lua", "/usr/lib/mpv-uos/mpvd/server.py",
                 "/usr/share/applications/mpv-uos.desktop", "/usr/share/man/man1/atalaya.1.gz",
                 "/usr/share/doc/atalaya-player/copyright"):
        assert ruta in contenido, f"falta {ruta}"
    # el enlace del .venv tiene que apuntar al intérprete que va dentro: es donde lo buscan mu-core y el lanzador
    assert contenido["/usr/lib/mpv-uos/.venv/bin/python"].linkname.endswith("python/bin/python3.12")


def test_no_viaja_nada_que_no_toca(contenido):
    """Ni tests, ni .venv de desarrollo, ni cachés de Python (las mete una comprobación mal hecha al construir)."""
    sobra = [k for k in contenido if "__pycache__" in k or "/tests/" in k or k.endswith(".pyc")
             or "/.git" in k or "/site-packages/pip/" in k]
    assert not sobra, sobra[:5]


def test_los_permisos_son_los_de_un_paquete(contenido):
    raros = []
    for nombre, m in contenido.items():
        if m.isdir() and m.mode != 0o755:
            raros.append((nombre, oct(m.mode)))
        elif m.isfile() and m.mode not in (0o644, 0o755, 0o440):
            raros.append((nombre, oct(m.mode)))
    assert not raros, raros[:5]
    assert contenido["/usr/bin/mpv-uos"].mode == 0o755
    assert contenido["/usr/lib/mpv-uos/bin/wake"].mode == 0o755
    assert contenido["/etc/sudoers.d/mpv-uos-rtcwake"].mode == 0o440, "sudoers manda 0440"


def test_la_regla_de_sudoers_es_valida_y_solo_autoriza_el_ayudante(deb, contenido, tmp_path):
    """Dos cosas: que sudo la acepte (un fichero roto ahí puede dejar a alguien sin sudo) y que NO autorice
    `rtcwake` entero, porque eso dejaría pasar también `rtcwake -m off`, que apaga la máquina."""
    salida = subprocess.run(["dpkg-deb", "--fsys-tarfile", str(deb)], capture_output=True, check=True).stdout
    import io
    with tarfile.open(fileobj=io.BytesIO(salida)) as tf:
        texto = tf.extractfile("./etc/sudoers.d/mpv-uos-rtcwake").read().decode()
    assert "/usr/lib/mpv-uos/bin/wake" in texto
    assert "/usr/sbin/rtcwake" not in texto, "la regla no debe autorizar rtcwake directamente"
    assert "NOPASSWD" in texto and "%sudo" in texto
    if shutil.which("visudo"):
        f = tmp_path / "regla"
        f.write_text(texto, encoding="utf-8")
        r = subprocess.run(["visudo", "-c", "-f", str(f)], capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr


def test_el_ayudante_del_despertador_solo_sabe_tres_cosas():
    """Lo que valida la seguridad de la regla no es la regla: es que este programa no acepte otra cosa."""
    wake = ROOT / "bin/wake"
    assert wake.is_file() and os.access(wake, os.X_OK)
    for args in (["set", "abc"], ["set", "12; rm -rf /"], ["set"], ["set", "12", "y", "más"],
                 ["apagar"], [], ["clear", "extra"], ["check", "extra"], ["-m", "off"]):
        r = subprocess.run([str(wake), *args], capture_output=True, text=True)
        assert r.returncode == 2, (args, r.returncode, r.stdout, r.stderr)
        assert "uso:" in r.stderr, (args, r.stderr)
        assert "rtcwake" not in r.stderr or "falta rtcwake" in r.stderr, "no debe haber ejecutado nada"


def test_el_lanzador_instalado_apunta_a_la_carpeta_del_paquete(deb):
    salida = subprocess.run(["dpkg-deb", "--fsys-tarfile", str(deb)], capture_output=True, check=True).stdout
    import io
    with tarfile.open(fileobj=io.BytesIO(salida)) as tf:
        texto = tf.extractfile("./usr/bin/mpv-uos").read().decode()
    assert 'APP="/usr/lib/mpv-uos"' in texto
    assert "MPV_UOS_CACHE_DIR" in texto and "MPV_UOS_VENDOR_BIN" in texto, "la app es de solo lectura"
    assert "MPV_UOS_PACKAGED=deb" in texto
    assert "apt install mpv" in texto, "si falta mpv hay que decir cómo instalarlo"


@pytest.mark.skipif(not shutil.which("lintian"), reason="sin lintian")
def test_lo_que_lintian_encuentra_es_solo_lo_que_sabemos(deb):
    """lintian tiene que estar en cero menos lo inherente a llevar el intérprete dentro: el CPython de
    python-build-standalone trae zlib, bzip2, expat y ncurses enlazados, su binario no se puede despojar sin
    dejarlo sin arrancar (probado) y el ayudante `ziggy` de uosc viene estático de uosc. Si aparece otra cosa,
    este test lo dice: es la forma de que un descuido en los permisos o un fichero de más no pase callando."""
    r = subprocess.run(["lintian", "--tag-display-limit", "0", str(deb)], capture_output=True, text=True)
    conocidos = ("embedded-library", "statically-linked-binary", "unstripped-binary-or-object",
                 "hardening-no-pie", "no-copyright-file")
    inesperados = [l for l in r.stdout.splitlines()
                   if l.startswith(("E:", "W:")) and not any(c in l for c in conocidos)]
    assert not inesperados, inesperados[:10]


def test_el_paquete_extraido_arranca_y_el_demonio_se_conecta(deb, tmp_path):
    """La prueba que de verdad dice que el paquete sirve: se extrae, se ejecuta su lanzador con el mpv del sistema
    y mu-core tiene que levantar mpvd con el intérprete QUE VA DENTRO —no el .venv del repositorio— y conectarse.
    Es lo mismo que comprueba el test del AppImage, porque el fallo que se busca es el mismo: un paquete que se
    construye, se instala, y al abrirlo no hace nada porque dentro le falta algo."""
    from tests.conftest import start_mpv

    destino = tmp_path / "raiz"
    destino.mkdir()
    subprocess.run(["dpkg-deb", "-x", str(deb), str(destino)], check=True)
    app = destino / "usr/lib/mpv-uos"
    assert (app / "bin/mpv-uos").is_file()

    home = tmp_path / "home"
    env = {k: v for k, v in os.environ.items() if not k.startswith("MPV_UOS_")}
    env |= {"HOME": str(home), "XDG_CACHE_HOME": str(home / ".cache"),
            "XDG_DATA_HOME": str(home / ".local/share")}
    h = start_mpv(tmp_path / "run",
                  ["--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=5"],
                  env=env, launcher=app / "bin/mpv-uos")
    try:
        core = h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=60)
        assert str(app) in core.get("root", ""), core
        # el intérprete usado es el del paquete, no el del repositorio
        assert (app / ".venv/bin/python").is_symlink()
        assert (app / ".venv/bin/python").resolve() == (app / "python/bin/python3.12").resolve()
        assert not h.script_errors(), h.script_errors()
    finally:
        h.stop()
