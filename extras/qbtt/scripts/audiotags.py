"""
audiotags.py - minimal reader for albumartist / artist / album, used by qbt-sort.py.

Standard library only (the qBittorrent image has no mutagen).
Formats: MP3 (ID3v2.2/2.3/2.4 + ID3v1), FLAC, Ogg Vorbis, Opus, M4A/MP4;
anything else returns empty tags.
read_tags(path) -> {"albumartist": str|None, "artist": str|None, "album": str|None}
"""
import struct

KEYS = ("albumartist", "artist", "album")


def _empty():
    return dict.fromkeys(KEYS)


def _clean(s):
    s = (s or "").replace("\x00", "/").strip(" /")
    return s or None


# ---------------------------------------------------------------- ID3
ID3_MAP = {
    "TPE2": "albumartist", "TPE1": "artist", "TALB": "album",   # v2.3 / v2.4
    "TP2": "albumartist", "TP1": "artist", "TAL": "album",      # v2.2
}


def _syncsafe(b):
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def _id3_text(data):
    if not data:
        return None
    enc, raw = data[0], data[1:]
    codec = {0: "latin-1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}.get(enc, "latin-1")
    if enc in (1, 2) and len(raw) % 2:
        raw = raw[:-1]
    text = raw.decode(codec, "replace")
    # v2.4 multi-value: separated by NUL -> keep the first value
    return _clean(text.split("\x00")[0])


def read_id3v2(f):
    tags = _empty()
    f.seek(0)
    head = f.read(10)
    if len(head) < 10 or head[:3] != b"ID3":
        return tags, 0
    ver, flags, size = head[3], head[5], _syncsafe(head[6:10])
    body = f.read(size)
    if flags & 0x80 and ver < 4:            # whole-tag unsynchronisation (v2.2/2.3)
        body = body.replace(b"\xff\x00", b"\xff")
    pos = 0
    if flags & 0x40 and ver >= 3:           # extended header
        ext = body[:4]
        pos = _syncsafe(ext) if ver == 4 else struct.unpack(">I", ext)[0] + 4
    idlen, hlen = (3, 6) if ver == 2 else (4, 10)
    while pos + hlen <= len(body):
        fid = body[pos:pos + idlen]
        if not fid.strip(b"\x00"):
            break                           # padding
        if ver == 2:
            fsize = int.from_bytes(body[pos + 3:pos + 6], "big")
        elif ver == 4:
            fsize = _syncsafe(body[pos + 4:pos + 8])
        else:
            fsize = struct.unpack(">I", body[pos + 4:pos + 8])[0]
        data = body[pos + hlen:pos + hlen + fsize]
        key = ID3_MAP.get(fid.decode("latin-1", "replace"))
        if key and not tags[key]:
            if ver == 4 and body[pos + 9] & 0x02:   # per-frame unsynchronisation
                data = data.replace(b"\xff\x00", b"\xff")
            tags[key] = _id3_text(data)
        pos += hlen + fsize
    return tags, 10 + size


def read_id3v1(f):
    tags = _empty()
    try:
        f.seek(-128, 2)
    except OSError:
        return tags
    b = f.read(128)
    if b[:3] == b"TAG":
        dec = lambda x: _clean(x.split(b"\x00")[0].decode("latin-1"))
        tags["artist"], tags["album"] = dec(b[33:63]), dec(b[63:93])
    return tags


# ---------------------------------------------------------------- Vorbis comments
VORBIS_MAP = {"ALBUMARTIST": "albumartist", "ALBUM ARTIST": "albumartist",
              "ARTIST": "artist", "ALBUM": "album"}


def _vorbis_comments(b):
    tags = _empty()
    try:
        vlen = struct.unpack("<I", b[:4])[0]
        pos = 4 + vlen
        n = struct.unpack("<I", b[pos:pos + 4])[0]
        pos += 4
        for _ in range(n):
            ln = struct.unpack("<I", b[pos:pos + 4])[0]
            kv = b[pos + 4:pos + 4 + ln].decode("utf-8", "replace")
            pos += 4 + ln
            k, _, v = kv.partition("=")
            key = VORBIS_MAP.get(k.upper())
            if key and not tags[key]:
                tags[key] = _clean(v)
    except (struct.error, IndexError):
        pass
    return tags


def read_flac(f, start):
    f.seek(start)
    if f.read(4) != b"fLaC":
        return _empty()
    while True:
        h = f.read(4)
        if len(h) < 4:
            return _empty()
        last, btype, size = h[0] & 0x80, h[0] & 0x7F, int.from_bytes(h[1:4], "big")
        if btype == 4:
            return _vorbis_comments(f.read(size))
        f.seek(size, 1)
        if last:
            return _empty()


def read_ogg(f, max_bytes=16 << 20):
    """Rebuild the 2nd logical packet (comment header) from Ogg pages.
    The packet can span many pages when it embeds cover art."""
    f.seek(0)
    packets, cur = [], b""
    while f.tell() < max_bytes:
        h = f.read(27)
        if len(h) < 27 or h[:4] != b"OggS":
            break
        segs = f.read(h[26])
        for s in segs:
            cur += f.read(s)
            if s < 255:
                packets.append(cur)
                cur = b""
        if len(packets) >= 2:
            break
    for p in packets[1:2]:
        if p[:7] == b"\x03vorbis":
            return _vorbis_comments(p[7:])
        if p[:8] == b"OpusTags":
            return _vorbis_comments(p[8:])
    return _empty()


# ---------------------------------------------------------------- MP4 / M4A
MP4_MAP = {b"aART": "albumartist", b"\xa9ART": "artist", b"\xa9alb": "album"}
MP4_CONTAINERS = {b"moov", b"udta", b"meta", b"ilst"}


def read_mp4(f):
    tags = _empty()
    f.seek(0, 2)
    end = f.tell()

    def walk(start, stop, depth):
        pos = start
        while pos + 8 <= stop and depth < 6:
            f.seek(pos)
            h = f.read(8)
            size, typ = struct.unpack(">I", h[:4])[0], h[4:8]
            hdr = 8
            if size == 1:
                size, hdr = struct.unpack(">Q", f.read(8))[0], 16
            elif size == 0:
                size = stop - pos
            if size < hdr:
                return
            if typ in MP4_CONTAINERS:
                skip = 4 if typ == b"meta" else 0      # meta has version+flags
                walk(pos + hdr + skip, pos + size, depth + 1)
            elif typ in MP4_MAP:
                b = f.read(min(size - hdr, 4096))
                # child 'data' atom: size(4) 'data'(4) type(4) locale(4) value
                if b[4:8] == b"data":
                    dlen = struct.unpack(">I", b[:4])[0]
                    key = MP4_MAP[typ]
                    if not tags[key]:
                        tags[key] = _clean(b[16:dlen].decode("utf-8", "replace"))
            pos += size

    walk(0, end, 0)
    return tags


# ---------------------------------------------------------------- entry point
def read_tags(path):
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    try:
        with open(path, "rb") as f:
            if ext in ("m4a", "mp4", "aac", "alac", "m4b"):
                return read_mp4(f)
            if ext in ("ogg", "opus", "oga"):
                return read_ogg(f)
            tags, end = read_id3v2(f)           # MP3, but FLAC can also start with ID3
            if ext == "flac":
                return read_flac(f, end)
            if not (tags["artist"] or tags["albumartist"]):
                v1 = read_id3v1(f)
                for k in KEYS:
                    tags[k] = tags[k] or v1[k]
            return tags
    except (OSError, struct.error, ValueError):
        return _empty()


if __name__ == "__main__":
    import sys
    for p in sys.argv[1:]:
        print(p.rsplit("/", 1)[-1], read_tags(p))
