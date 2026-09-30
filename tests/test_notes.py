"""H17 · «Mis notas» (mpvd/notes.py): readable file names with a front matter tied to the content key, migration of the
first-version files, edit/delete/export and ``mpv-uos://`` links."""

from __future__ import annotations

from pathlib import Path

import pytest

from mpvd.notes import NoteFile, NotesStore, link, parse_link, safe_name


def test_links_round_trip_local_paths_and_urls():
    p = "/home/ser/Vídeos/La película: parte 1 (2024).mkv"
    url = link(p, 83.04)
    assert url.startswith("mpv-uos://open?path=/home/ser/V%C3%ADdeos/") and url.endswith("&t=83.0")
    assert parse_link(url) == {"path": p, "t": 83.0}
    yt = "https://www.youtube.com/watch?v=aqz-KE-bpKQ&t=5"
    assert parse_link(link(yt, 10)) == {"path": yt, "t": 10.0}
    assert parse_link(link("file:///x/y.mkv")) == {"path": "/x/y.mkv", "t": None}
    assert parse_link("mpv://seek?t=12.5") == {"path": None, "t": 12.5}
    assert parse_link("https://example.com") is None


def test_safe_names():
    assert safe_name('Él: "¿Quién?" / 2') == "Él ¿Quién 2"
    assert safe_name("  ...  ") == "Notas" and len(safe_name("x" * 300)) == 100


def test_add_list_get_edit_delete(tmp_path):
    store = NotesStore(tmp_path)
    r = store.add("mu:abc", "Los tres días del cóndor", "/v/condor.mkv", "Buena escena", 83.0)
    assert r["new_file"] and Path(r["file"]).name == "Los tres días del cóndor.md"
    store.add("mu:abc", "otro título", "/v/condor.mkv", "Al principio", 5.0)
    store.add("mu:abc", "", "/v/condor.mkv", "Sin tiempo", None)
    md = Path(r["file"]).read_text(encoding="utf-8")
    assert md.startswith("---\ntitulo: Los tres días del cóndor\nvideo: /v/condor.mkv\nclave: \"mu:abc\"\n---\n")
    assert "[▶ Abrir el vídeo](mpv-uos://open?path=/v/condor.mkv)" in md
    assert "- [00:01:23](mpv-uos://open?path=/v/condor.mkv&t=83.0) · " in md
    got = store.get("mu:abc")
    assert [n["text"] for n in got["items"]] == ["Al principio", "Buena escena", "Sin tiempo"]  # by time, untimed last
    assert got["items"][0]["time"] == 5.0 and got["path"] == "/v/condor.mkv"
    # a second video with the same title gets its own file
    r2 = store.add("mu:def", "Los tres días del cóndor", "/w/condor.mkv", "Otra copia", 1.0)
    assert Path(r2["file"]).name == "Los tres días del cóndor (2).md"
    assert [d["key"] for d in store.list()] == ["mu:def", "mu:abc"]
    got = store.edit("mu:abc", 1, "  Escena   buena de verdad ")
    assert got["items"][1]["text"] == "Escena buena de verdad" and got["items"][1]["time"] == 83.0
    got = store.delete("mu:abc", 0)
    assert [n["text"] for n in got["items"]] == ["Escena buena de verdad", "Sin tiempo"]
    with pytest.raises(KeyError):
        store.edit("mu:abc", 9, "x")
    store.delete("mu:def", 0)                       # the last note takes the file with it
    assert not Path(r2["file"]).exists() and [d["key"] for d in store.list()] == ["mu:abc"]
    # moved video: the next note updates the path of every link
    store.add("mu:abc", "", "/nuevo/condor.mkv", "Movido", 90.0)
    assert "/nuevo/condor.mkv&t=83.0" in Path(r["file"]).read_text(encoding="utf-8")


def test_first_version_files_are_migrated(tmp_path):
    d = tmp_path / "notas"
    d.mkdir()
    (d / "mu_abc.md").write_text("# Mi vídeo\n\n`/v/video.mkv`\n\n"
                                 "- [00:00:03](mpv://seek?t=3.2) · 2026-09-29 12:00 — Ojo aquí\n"
                                 "- 2026-09-29 12:01 — Sin tiempo\n", encoding="utf-8")
    store = NotesStore(tmp_path)
    [entry] = store.list()
    assert entry["title"] == "Mi vídeo" and entry["key"] == "mu_abc" and entry["notes"] == 2
    assert not (d / "mu_abc.md").exists() and (d / "Mi vídeo.md").exists()
    nf = NoteFile.parse(d / "Mi vídeo.md")
    assert nf.media == "/v/video.mkv" and nf.notes[0]["time"] == 3.2 and nf.notes[1]["time"] is None


def test_export_next_to_the_video_or_into_a_folder(tmp_path):
    video = tmp_path / "pelis" / "Mi peli.mkv"
    video.parent.mkdir()
    video.write_bytes(b"x")
    store = NotesStore(tmp_path / "data")
    store.add("k1", "Mi peli", str(video), "Nota", 1.0)
    out = store.export("k1")
    assert out["file"] == str(tmp_path / "pelis" / "Mi peli.notas.md") and "Nota" in Path(out["file"]).read_text()
    vault = tmp_path / "Obsidian" / "Cine"
    out = store.export("k1", str(vault))
    assert Path(out["file"]) == vault / "Mi peli.md" and out["notes"] == 1
    store.add("k2", "Vídeo web", "https://example.com/v", "Nota", 1.0)
    with pytest.raises(ValueError):
        store.export("k2")


def test_what_the_owner_writes_in_the_file_survives_new_notes(tmp_path):
    """H34 · these files are meant to be edited in Obsidian: adding, editing or deleting a note must not eat the text."""
    store = NotesStore(tmp_path)
    r = store.add("mu:abc", "Mi peli", "/v/peli.mkv", "Primera", 3.0)
    p = Path(r["file"])
    p.write_text(p.read_text(encoding="utf-8")
                 + "\n## Lo que pienso\n\nUna reflexión larga.\n\n- [ ] buscar el guion\n", encoding="utf-8")

    store.add("mu:abc", "Mi peli", "/v/peli.mkv", "Segunda", 9.0)
    md = p.read_text(encoding="utf-8")
    assert "## Lo que pienso" in md and "Una reflexión larga." in md and "- [ ] buscar el guion" in md
    assert "Segunda" in md and md.count("Una reflexión larga.") == 1

    notes = store.get("mu:abc")["items"]
    assert [n["text"] for n in notes] == ["Primera", "Segunda"]   # the checklist line is not read as a note
    store.edit("mu:abc", 0, "Primera, corregida")
    store.delete("mu:abc", 1)
    md = p.read_text(encoding="utf-8")
    assert "## Lo que pienso" in md and "Primera, corregida" in md


def test_a_foreign_markdown_in_the_notes_folder_is_left_alone(tmp_path):
    d = tmp_path / "notas"
    d.mkdir()
    foreign = d / "Mi diario.md"
    foreign.write_text("# Mi diario\n\nHoy he ido al cine.\n", encoding="utf-8")
    store = NotesStore(tmp_path)
    assert store.list() == []
    assert foreign.read_text(encoding="utf-8") == "# Mi diario\n\nHoy he ido al cine.\n"


def test_export_to_a_folder_never_overwrites_someone_elses_file(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    mine = vault / "Mi peli.md"
    mine.write_text("# Mi peli\n\nMi reseña.\n", encoding="utf-8")
    store = NotesStore(tmp_path / "data")
    store.add("k1", "Mi peli", "/v/peli.mkv", "Nota", 1.0)

    out = store.export("k1", str(vault))
    assert Path(out["file"]) == vault / "Mi peli (2).md"
    assert mine.read_text(encoding="utf-8") == "# Mi peli\n\nMi reseña.\n"
    # exporting again replaces our own copy instead of piling up
    store.add("k1", "Mi peli", "/v/peli.mkv", "Otra", 2.0)
    again = store.export("k1", str(vault))
    assert Path(again["file"]) == vault / "Mi peli (2).md" and again["notes"] == 2


def test_safe_name_fits_in_a_real_file_name(tmp_path):
    long_cjk = "あ" * 200
    assert len(safe_name(long_cjk).encode("utf-8")) <= 200
    store = NotesStore(tmp_path)
    r = store.add("mu:cjk", long_cjk, "/v/anime.mkv", "Nota", 1.0)
    assert Path(r["file"]).exists()
