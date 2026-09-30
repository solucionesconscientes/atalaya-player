"""H22 · File-name parser of the library: real-world movie and episode names (scene releases, Spanish naming, anime,
folder layouts) → movie/episode, display title, year, season, episode."""

from __future__ import annotations

import pytest

from mpvd.library.parse import clean_title, find_marker, folder_season, is_sample, is_video, norm, parse_path, split_year

MOVIES = [
    ("Blade.Runner.2049.2017.2160p.UHD.BluRay.x265-TERMiNAL.mkv", "Blade Runner 2049", 2017),
    ("Blade Runner 2049.mkv", "Blade Runner 2049", None),
    ("2001 A Space Odyssey (1968).mkv", "2001 A Space Odyssey", 1968),
    ("1917 (2019).mp4", "1917", 2019),
    ("Tiburón (1975).mkv", "Tiburón", 1975),
    ("Tiburón (1975) [BDRip 1080p][Castellano AC3 5.1].mkv", "Tiburón", 1975),
    ("John.Wick.Chapter.2.2017.1080p.BluRay.x264-SPARKS.mkv", "John Wick Chapter 2", 2017),
    ("amelie.2001.dvdrip.xvid.avi", "Amelie", 2001),
    ("The.Matrix.1999.REMASTERED.1080p.BluRay.x264.DTS-HD.MA.5.1-FGT.mkv", "The Matrix", 1999),
    ("Parasite.2019.KOREAN.1080p.WEB-DL.DD5.1.H.264-NOGRP.mkv", "Parasite", 2019),
    ("El.laberinto.del.fauno.2006.SPANISH.720p.BRRip.x264.mkv", "El laberinto del fauno", 2006),
    ("Mad Max Fury Road (2015) 1080p BluRay x264 [Dual Audio].mkv", "Mad Max Fury Road", 2015),
    ("Star Wars Episode 4 A New Hope (1977).mkv", "Star Wars Episode 4 A New Hope", 1977),
    ("Dune Part Two 2024 2160p WEB-DL DDP5.1 Atmos DV HDR H 265-FLUX.mkv", "Dune Part Two", 2024),
    ("Oppenheimer.2023.IMAX.2160p.HDR.mkv", "Oppenheimer", 2023),
    ("Los otros (2001).avi", "Los otros", 2001),
    ("The Lord of the Rings - The Fellowship of the Ring (2001) Extended.mkv",
     "The Lord of the Rings - The Fellowship of the Ring", 2001),
    ("Mr. Nobody (2009).mkv", "Mr. Nobody", 2009),
    ("L.A.Confidential.1997.1080p.mkv", "L.A. Confidential", 1997),
    ("Casablanca.mp4", "Casablanca", None),
]

EPISODES = [
    ("Breaking.Bad.S01E02.Cats.in.the.Bag.720p.HDTV.x264-CTU.mkv", "Breaking Bad", 1, 2, "Cats in the Bag"),
    ("breaking.bad.s05e14.720p.hdtv.x264.mkv", "Breaking Bad", 5, 14, ""),
    ("Doctor.Who.2005.S01E01.Rose.1080p.WEB-DL.mkv", "Doctor Who", 1, 1, "Rose"),
    ("El Padre Brown [HDTV][Cap.102][Spanish].avi", "El Padre Brown", 1, 2, ""),
    ("El Ministerio del Tiempo [HDTV 720p][Cap.312].mkv", "El Ministerio del Tiempo", 3, 12, ""),
    ("Friends 1x06 El del mono.avi", "Friends", 1, 6, "El del mono"),
    ("Los Serrano 3x12.avi", "Los Serrano", 3, 12, ""),
    ("[SubsPlease] Frieren - 05 [1080p].mkv", "Frieren", 1, 5, ""),
    ("[Erai-raws] Spy x Family - 12 [720p][Multiple Subtitle].mkv", "Spy x Family", 1, 12, ""),
    ("Game.of.Thrones.S08E01E02.1080p.mkv", "Game of Thrones", 8, 1, ""),
    ("The Office US - S02E01 - The Dundies.mkv", "The Office US", 2, 1, "The Dundies"),
    ("S.W.A.T.2017.S01E01.720p.mkv", "S.W.A.T.", 1, 1, ""),
    ("Stranger Things - Temporada 1 Capitulo 3 [HDTV].mkv", "Stranger Things", 1, 3, ""),
    ("Sherlock Season 2 Episode 1.mp4", "Sherlock", 2, 1, ""),
    ("Cuéntame cómo pasó 21x05.mkv", "Cuéntame cómo pasó", 21, 5, ""),
    ("the.mandalorian.s02e08.2160p.web.h265-glhf.mkv", "The Mandalorian", 2, 8, ""),
    ("Fargo S3E4 The Narrow Escape Problem.mkv", "Fargo", 3, 4, "The Narrow Escape Problem"),
]


@pytest.mark.parametrize(("name", "title", "year"), MOVIES)
def test_movies(name, title, year):
    m = parse_path(f"/lib/{name}", "/lib")
    assert (m.kind, m.title, m.year) == ("movie", title, year), m


@pytest.mark.parametrize(("name", "title", "season", "episode", "ep_title"), EPISODES)
def test_episodes(name, title, season, episode, ep_title):
    m = parse_path(f"/lib/{name}", "/lib")
    assert (m.kind, m.title, m.season, m.episode, m.episode_title) == ("episode", title, season, episode, ep_title), m


def test_multi_episode_and_group_keys():
    m = parse_path("/lib/Game.of.Thrones.S08E01E02.1080p.mkv", "/lib")
    assert m.episode_end == 2
    assert parse_path("/lib/Game of Thrones - 8x03.mkv", "/lib").group_key == m.group_key == "game of thrones"
    a = parse_path("/lib/Tiburón (1975).mkv", "/lib")
    assert a.group_key == "tiburon 1975"


@pytest.mark.parametrize(("path", "title", "season", "episode", "ep_title"), [
    ("/lib/Series/Breaking Bad/Season 1/Breaking.Bad.S01E01.720p.BluRay.x264.mkv", "Breaking Bad", 1, 1, ""),
    ("/lib/The Office (US)/Temporada 2/01 - The Dundies.mkv", "The Office US", 2, 1, "The Dundies"),
    ("/lib/La Casa de Papel/Temporada 3/La.Casa.de.Papel.S03E01.WEBRip.mkv", "La Casa de Papel", 3, 1, ""),
    ("/lib/Stranger Things Season 2/Chapter 1.mkv", "Stranger Things", 2, 1, ""),
    ("/lib/Serie Temporada 2/03.mkv", "Serie", 2, 3, ""),
    ("/lib/Mi Serie/Temporada 1/Piloto.mkv", "Mi Serie", 1, None, "Piloto"),
    ("/lib/Mi Serie/S02/E05.mkv", "Mi Serie", 2, 5, ""),
    ("/lib/Doctor Who (2005)/Specials/S00E01.mkv", "Doctor Who", 0, 1, ""),
    ("/lib/Fringe/Season 1/1x03.mkv", "Fringe", 1, 3, ""),
    ("/lib/El Padre Brown 1x03/video.mp4", "El Padre Brown", 1, 3, ""),
    ("/lib/Mr. Robot/S01/eps1.0_hellofriend.mov.mkv", "Mr. Robot", 1, None, "Eps1 0 Hellofriend Mov"),
])
def test_folder_layouts(path, title, season, episode, ep_title):
    m = parse_path(path, "/lib")
    assert (m.kind, m.title, m.season, m.episode, m.episode_title) == ("episode", title, season, episode, ep_title), m


def test_movie_named_by_its_folder_and_root_never_used():
    m = parse_path("/lib/The Godfather (1972)/movie.mkv", "/lib")
    assert (m.kind, m.title, m.year) == ("movie", "The Godfather", 1972)
    m = parse_path("/lib/Pelis/Casablanca.mkv", "/lib/Pelis")
    assert (m.kind, m.title) == ("movie", "Casablanca")
    # a file with a marker but no title, straight in the library folder: its own name, not the folder's
    m = parse_path("/lib/Series/S01E02.mkv", "/lib/Series")
    assert (m.kind, m.season, m.episode) == ("episode", 1, 2) and m.title


def test_helpers():
    assert split_year("Blade Runner 2049 ") == ("Blade Runner 2049 ", None)   # 2049 is in the future
    assert split_year("1984 (1984)")[1] == 1984
    assert clean_title("Tiburón.(1975).[1080p]") == ("Tiburón", 1975)
    assert find_marker("Video 1920x1080 sample") is None       # a resolution is not 20x108
    assert find_marker("Serie 1x02").season == 1
    assert folder_season("Temporada 03") == 3 and folder_season("S1") == 1 and folder_season("Specials") == 0
    assert folder_season("Serie Season 4") == 4 and folder_season("Películas") is None
    assert is_sample("movie-sample") and is_sample("Sample") and not is_sample("Samples of Life")
    assert is_video("a.MKV") and not is_video(".hidden.mkv") and not is_video("poster.jpg")
    assert norm("Cuéntame cómo pasó!") == "cuentame como paso"
