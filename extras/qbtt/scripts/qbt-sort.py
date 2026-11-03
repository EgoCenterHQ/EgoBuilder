#!/usr/bin/env python3
"""
qbt-sort.py - sorts completed torrents into /Films, /Musics, /SerieTv.
Music goes to /Musics/<Artist>/<Album>/ (Jellyfin layout), artist and album
read from the audio tags (audiotags.py) or, failing that, from the torrent name.

Run by qBittorrent ("Run external program on torrent finished"):
    python3 /scripts/qbt-sort.py "%K"
(%K = torrent ID, also valid for v2 torrents; %I is "-" for v2 torrents)

Uses the Web API (localhost, no login: "bypass_local_auth"; port from QBT_WEBUI_PORT
or from qBittorrent.conf) and torrents/setLocation,
so qBittorrent moves the files itself and keeps seeding from the new location.

Test without moving anything:  python3 qbt-sort.py <hash> --dry-run
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime

from audiotags import read_tags

LOG = "/config/qbt-sort.log"
CONF = "/config/qBittorrent/config/qBittorrent.conf"


def webui_port():
    """QBT_WEBUI_PORT does not reach the script (the image's doas clears the environment):
    if missing, read it from qBittorrent.conf."""
    if os.getenv("QBT_WEBUI_PORT"):
        return os.environ["QBT_WEBUI_PORT"]
    try:
        with open(CONF) as f:
            for line in f:
                if line.startswith("WebUI\\Port="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return "8082"


API = "http://127.0.0.1:%s/api/v2/" % webui_port()

DEST = {"video_movie": "/Films", "video_series": "/SerieTv", "audio": "/Musics"}

EXT_VIDEO = {"mkv", "mp4", "avi", "m4v", "mov", "wmv", "mpg", "mpeg", "ts", "m2ts", "webm", "flv", "vob", "divx"}
EXT_AUDIO = {"mp3", "flac", "m4a", "aac", "ogg", "opus", "wav", "wma", "alac", "ape", "aiff", "dsf"}
# executables and scripts: never expected in a film/series/music torrent
EXT_RISKY = {"exe", "msi", "scr", "bat", "cmd", "com", "pif", "cpl", "dll", "sys", "lnk", "reg",
             "vbs", "vbe", "js", "jse", "wsf", "wsh", "hta", "ps1", "psm1", "jar", "apk",
             "app", "dmg", "pkg", "deb", "rpm", "sh", "run"}
WARNING_FILE = "POTENTIALLY HARMFUL"

# S01E02, S01, 1x02, Season 1/Stagione 2, "Complete Series", Ep 01
# (season/stagione only when followed by a number: "The Four Seasons" is a movie)
RE_SERIES = re.compile(
    r"(\bS\d{1,2}\s?E\d{1,3}\b|\bS\d{1,2}\b|\b\d{1,2}x\d{2,3}\b|"
    r"\bseasons?[ ._\-]?\d{1,2}\b|\bstagion[ei][ ._\-]?\d{1,2}\b|"
    r"complete.?series|serie\s?(tv|completa)|\bep(isode)?\s?\d{1,3}\b)",
    re.IGNORECASE,
)

# "Title.2018.1080p..." -> title + year (takes the last year: "Blade.Runner.2049.2017")
RE_YEAR = re.compile(r"^(.+)[ ._\-\(\[]((?:19|20)\d{2})(?=$|[ ._\-\)\]])")
# no year: cut at the first quality/rip tag
RE_TAG = re.compile(
    r"[ ._\-\(\[](2160p|1080p|720p|576p|480p|4k|uhd|bluray|blu-ray|bdrip|brrip|web-?dl|webrip|"
    r"hdtv|dvdrip|x264|x265|h\.?264|h\.?265|hevc|ita|eng|multi)\b",
    re.IGNORECASE,
)


def movie_folder(name):
    """'BlacKkKlansman.2018.1080p.BluRay' -> 'BlacKkKlansman (2018)'"""
    if "." in name and name.rsplit(".", 1)[1].lower() in EXT_VIDEO:
        name = name.rsplit(".", 1)[0]
    m = RE_YEAR.match(name)
    if m:
        title, year = m.group(1), m.group(2)
    else:
        title, year = RE_TAG.split(name, 1)[0], None
    title = re.sub(r"[._]+", " ", title)
    title = re.sub(r'[\\/:*?"<>|\[\]\(\)]', "", title).strip(" -")
    if not title:
        title = re.sub(r'[\\/:*?"<>|]', "", name).strip()
    return "%s (%s)" % (title, year) if year else title


UNKNOWN_ARTIST = "Unknown Artist"
VARIOUS_ARTISTS = "Various Artists"

# "Artist feat. X", "Artist (ft. X)", "Artist Featuring X" -> "Artist"
# ("&", "," and "x" are not split: "Simon & Garfunkel" is one artist)
RE_FEAT = re.compile(r"\s*[\(\[]?\s*\b(feat\.?|ft\.?|featuring)\s.*$", re.IGNORECASE)
# "Artist - Album (2019) [FLAC 24-96]" -> ("Artist", "Album (2019)")
RE_ARTIST_ALBUM = re.compile(r"^(.+?)\s+[-–—]\s+(.+)$")


def clean_name(name):
    """Make a tag usable as a folder name ("AC/DC" -> "AC-DC")."""
    name = re.sub(r"[\\/]", "-", name or "")
    name = re.sub(r'[:*?"<>|\x00-\x1f]', "", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:150].strip(" .")


def main_artist(artist):
    return RE_FEAT.sub("", artist).strip() or artist


def names_from_title(name):
    """Fallback: (artist, album) from "Artist - Album (Year) [FLAC]"."""
    name = re.sub(r"\s*\[[^\]]*\]", "", name).replace("_", " ").strip()
    m = RE_ARTIST_ALBUM.match(name)
    if m:
        return main_artist(m.group(1).strip()), m.group(2).strip()
    return None, name or None


def music_names(t, files):
    """(artist, album) folder names for an audio torrent: majority of the
    tags of its tracks, then the torrent name, then Unknown Artist."""
    albumartists, artists, albums = Counter(), Counter(), Counter()
    tracks = 0
    for f in files:
        ext = f["name"].rsplit(".", 1)[-1].lower() if "." in f["name"] else ""
        if ext not in EXT_AUDIO or f.get("progress", 1) < 1:
            continue
        tracks += 1
        tags = read_tags(os.path.join(t["save_path"], f["name"]))
        if tags["albumartist"]:
            albumartists[main_artist(tags["albumartist"])] += 1
        if tags["artist"]:
            artists[main_artist(tags["artist"])] += 1
        if tags["album"]:
            albums[tags["album"]] += 1

    artist = album = None
    if albumartists:
        artist = albumartists.most_common(1)[0][0]
    elif artists:
        top, n = artists.most_common(1)[0]
        # no album artist and no clear majority among many artists: a compilation
        artist = VARIOUS_ARTISTS if len(artists) >= 3 and n * 2 < tracks else top
    if albums:
        top, n = albums.most_common(1)[0]
        if n >= 0.8 * sum(albums.values()):
            album = top

    root = root_folder(files) or t["name"]
    title_artist, title_album = names_from_title(root)
    if not album:
        # no album tag, or several albums (e.g. a discography): use the torrent's name
        album = title_album
    artist = clean_name(artist or title_artist) or UNKNOWN_ARTIST
    album = clean_name(album) or clean_name(t["name"]) or "Unknown Album"
    return artist, album


def free_name(parent, name, own=None):
    """name, or "name (2)", "name (3)"... if parent/name already exists
    and is not where this torrent already is (own)."""
    candidate, i = name, 1
    while True:
        path = os.path.join(parent, candidate)
        if not os.path.exists(path) or path == own:
            return candidate
        i += 1
        candidate = "%s (%d)" % (name, i)


def risky_files(files):
    """Names of the torrent's executables/scripts ("track.mp3.exe" included)."""
    return [f["name"] for f in files
            if "." in f["name"] and f["name"].rsplit(".", 1)[1].lower() in EXT_RISKY]


def mark_risky(name, risky, folder, dry=False):
    """Log a warning and, if the torrent has its own folder, leave a
    WARNING_FILE in it listing the suspicious files."""
    if not risky:
        return
    log("WARN  '%s': %d potentially harmful file(s): %s"
        % (name, len(risky), ", ".join(risky[:5]) + (" ..." if len(risky) > 5 else "")))
    if dry or folder is None:
        return
    try:
        with open(os.path.join(folder, WARNING_FILE), "w") as f:
            f.write("qbt-sort: this torrent contains executable files or scripts.\n"
                    "Do not open them unless you know exactly what they are.\n\n")
            f.write("\n".join(risky) + "\n")
        log("WARN  '%s': created %s" % (name, os.path.join(folder, WARNING_FILE)))
    except OSError as e:
        log("ERROR '%s': cannot create %s in %s: %r" % (name, WARNING_FILE, folder, e))


def log(msg):
    line = "%s  %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def api(path, data=None):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    with urllib.request.urlopen(API + path, body, timeout=30) as r:
        return r.read().decode()


def torrent_info(hash_):
    info = json.loads(api("torrents/info?" + urllib.parse.urlencode({"hashes": hash_})))
    return info[0] if info else None


def wait_for_move(hash_, max_seconds=3600):
    """Wait until qBittorrent has finished moving the files (e.g. from /downloads/temp)."""
    t = torrent_info(hash_)
    for _ in range(max_seconds // 5):
        if t is None or t["state"] != "moving":
            break
        time.sleep(5)
        t = torrent_info(hash_)
    return t


def root_folder(files):
    """Name of the torrent's top folder, if all files are inside a single one."""
    roots = {f["name"].split("/", 1)[0] for f in files}
    if len(roots) == 1 and all("/" in f["name"] for f in files):
        return roots.pop()
    return None


def classify(name, files):
    size = {"video": 0, "audio": 0}
    series_videos = 0
    for f in files:
        ext = f["name"].rsplit(".", 1)[-1].lower() if "." in f["name"] else ""
        if ext in EXT_VIDEO:
            size["video"] += f["size"]
            if RE_SERIES.search(os.path.basename(f["name"])):
                series_videos += 1
        elif ext in EXT_AUDIO:
            size["audio"] += f["size"]

    if size["video"] == 0 and size["audio"] == 0:
        return None
    if size["audio"] > size["video"]:
        return "audio"
    if RE_SERIES.search(name) or series_videos >= 2:
        return "video_series"
    return "video_movie"


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    hash_ = sys.argv[1]
    dry = "--dry-run" in sys.argv

    # with the temp folder enabled the torrent may still be moving
    t = wait_for_move(hash_)
    if t is None:
        log("ERROR torrent %s not found" % hash_)
        return 1
    files = json.loads(api("torrents/files?" + urllib.parse.urlencode({"hash": hash_})))

    risky = risky_files(files)
    if risky:
        # never move a torrent with executables/scripts: it stays in /downloads
        root = root_folder(files)
        mark_risky(t["name"], risky, os.path.join(t["save_path"], root) if root else None, dry)
        log("HOLD  '%s': not moved because of the files above, stays in %s"
            % (t["name"], t["save_path"]))
        return 0

    kind = classify(t["name"], files)
    if kind is None:
        log("SKIP  '%s': no video/audio files, stays in %s" % (t["name"], t["save_path"]))
        return 0

    dest = DEST[kind]
    rename = None   # (old, new) top folder of the torrent
    artist_dir = None
    if kind == "video_movie":
        root = root_folder(files)
        if root is None and len(files) == 1:
            # single-file movie: give it a "Title (Year)" folder
            dest = os.path.join(dest, movie_folder(t["name"]))
        elif root is not None and movie_folder(root) != root:
            # movie already in a folder: rename it to "Title (Year)"
            rename = (root, movie_folder(root))
    elif kind == "audio":
        # /Musics/<Artist>/<Album>/...
        artist, album = music_names(t, files)
        dest = artist_dir = os.path.join(dest, artist)
        root = root_folder(files)
        if root is None:
            # loose files: the album folder is created by setLocation
            dest = os.path.join(dest, free_name(dest, album, t["save_path"].rstrip("/")))
        else:
            own = os.path.join(t["save_path"], root) if t["save_path"].rstrip("/") == dest else None
            album = free_name(dest, album, own)
            if album != root:
                rename = (root, album)
    if t["save_path"].rstrip("/") == dest and rename is None:
        log("SKIP  '%s': already in %s" % (t["name"], dest))
        return 0
    if not os.path.isdir(DEST[kind]):
        log("ERROR '%s': folder %s not mounted in the container" % (t["name"], DEST[kind]))
        return 1

    final = os.path.join(dest, rename[1]) if rename else dest
    if dry:
        log("DRY   '%s' -> %s" % (t["name"], final))
        return 0

    if artist_dir and not os.path.isdir(artist_dir):
        # artist folder: create it if missing
        os.makedirs(artist_dir, exist_ok=True)
        log("MKDIR %s" % artist_dir)
    if rename:
        api("torrents/renameFolder", {"hash": hash_, "oldPath": rename[0], "newPath": rename[1]})
    if t["save_path"].rstrip("/") != dest:
        api("torrents/setLocation", {"hashes": hash_, "location": dest})
    # wait for the move to finish, only to log it
    time.sleep(5)
    t2 = wait_for_move(hash_)
    if t2 is not None and t2["save_path"].rstrip("/") != dest:
        # setLocation answers ok even if the move then fails (e.g. permissions)
        log("ERROR '%s': move to %s failed, stays in %s (state: %s)"
            % (t["name"], dest, t2["save_path"], t2["state"]))
        return 1
    log("MOVE  '%s' -> %s (state: %s)" % (t["name"], final, t2["state"] if t2 else "removed"))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # never make qBittorrent fail because of this script
        log("ERROR %s: %r" % (sys.argv[1:], e))
        sys.exit(1)
