#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
flip_exe_to_pdf.py - convert a Flip PDF / FlipBuilder interactive book (.exe) to a PDF.

* The .exe is NEVER executed: it is only read (mmap) and its page files are extracted.
* Always "Vector lite": vector pages, selectable text taken from the book (no OCR),
  big images shrunk to 150 dpi / JPEG q72.
* Pure Python. Only dependency: Pillow  (pip install pillow)

Use from code:
    from flip_exe_to_pdf import convert_exe_to_pdf, BookError, Cancelled
    info = convert_exe_to_pdf("book.exe", "book.pdf")        # -> {"pages": 120, "bytes": 5123456, "notes": [...]}

    pages = peek_book("book.exe")                              # cheap check: is this really a book? (raises BookError)

Use from the command line (handy for running it in a separate process from the bot):
    python3 flip_exe_to_pdf.py book.exe [book.pdf]
    python3 flip_exe_to_pdf.py --json book.exe book.pdf       # one JSON object per line (progress / done / error)
"""
import hashlib
import io
import json
import math
import mmap
import os
import re
import struct
import sys
import time
import traceback
import zlib
from pathlib import Path

# ---- settings -------------------------------------------------------------
LITE = {"bal": (150, 72), "strong": (120, 60), "max": (96, 50)}   # (max image dpi, JPEG quality)
DEFAULT_LEVEL = "bal"
MAX_PAGES = 2000                    # refuse absurd books
MAX_SWF_BYTES = 256 * 1024 * 1024   # max size of one decompressed page (zip-bomb guard)
MAX_BITMAP_PIXELS = 80_000_000      # max pixels of one embedded bitmap

PT = 0.012            # one SWF twip (1/20 px at 300 px/inch) in PDF points
EM = 20480.0          # DefineFont3 em square (1024 * 20)


class BookError(Exception):
    pass


class Cancelled(Exception):
    pass


# ---------------------------------------------------------------- extractor

def _jpeg_end(d, p):
    q = p + 2
    while True:
        q = d.find(b"\xff", q)
        if q < 0 or q + 1 >= len(d):
            raise BookError("Damaged JPEG inside the book")
        while d[q] == 0xff:
            q += 1
        m = d[q]
        q += 1
        if m == 0xd9:
            return q
        if m == 0x01 or 0xd0 <= m <= 0xd7 or m == 0:
            continue
        q += struct.unpack(">H", d[q:q + 2])[0]
        if m == 0xda:                      # scan data: skip until a real marker
            while True:
                q = d.find(b"\xff", q)
                if q < 0:
                    raise BookError("Damaged JPEG inside the book")
                nx = d[q + 1]
                if nx == 0 or 0xd0 <= nx <= 0xd7:
                    q += 2
                    continue
                break



def _inflate_at(d, pos, keep=True):
    """Inflate the zlib stream at `pos`. Returns (data, end offset). keep=False only finds the end."""
    dec = zlib.decompressobj()
    out, p, total = [], pos, 0
    while not dec.eof:
        chunk = d[p:p + 65536]
        if not chunk:
            raise BookError("The book file is truncated")
        piece = dec.decompress(chunk)
        total += len(piece)
        if total > MAX_SWF_BYTES:
            raise BookError("A page inside the book is unreasonably large")
        if keep:
            out.append(piece)
        p += len(chunk)
    return b"".join(out), p - len(dec.unused_data)


_MAGIC = {"xml": (b"<?xml", b"\xef\xbb\xbf<?xml"), "swf": (b"CWS", b"FWS"), "jpg": (b"\xff\xd8\xff",),
          "png": (b"\x89PNG",), "ocx": (b"MZ\x90\x00",)}


_TABLE_RE = re.compile(rb"(?:[A-Za-z0-9_./ \-]+=\d+\r\n){5,}")


def _find_table(d):
    """-> (match, names). The file table of a Flip PDF / FlipBuilder book."""
    m = None
    for mt in _TABLE_RE.finditer(d):
        if b"files/" in mt.group(0):
            m = mt
            break
    if not m:
        raise BookError("This .exe is not a Flip PDF / FlipBuilder book (no file table found)")
    names = [ln.split("=")[0] for ln in m.group(0).decode("latin1").split("\r\n") if "=" in ln]
    return m, names


class BookSniffer:
    """Decides EARLY, while a .exe is still being downloaded, whether it can be a Flip PDF / FlipBuilder book,
    so a Windows installer (or any other program) is dropped after the first few MB instead of being fully
    downloaded. Feed it the chunks in order:
        sniff = BookSniffer(limit_bytes)
        for chunk in chunks:
            sniff.feed(chunk)        # raises BookError as soon as it is clear that this is not a book
        sniff.end()                  # raises BookError if the file table was never seen
    `found_at` is the byte offset where the book's file table starts (log it to tune the limit).
    """
    _PAT = re.compile(rb"files/large/[A-Za-z0-9_.\-]+=\d+\r\n")

    def __init__(self, limit_bytes):
        self.limit, self.seen, self.found_at, self._tail = int(limit_bytes), 0, None, b""

    def feed(self, chunk):
        if self.found_at is not None:
            self.seen += len(chunk)
            return True
        if self.seen == 0 and chunk[:2] != b"MZ":
            raise BookError("This is not a Windows .exe file")
        buf = self._tail + chunk
        m = self._PAT.search(buf)
        if m:
            self.found_at = self.seen - len(self._tail) + m.start()
            self.seen += len(chunk)
            return True
        self.seen += len(chunk)
        self._tail = buf[-256:]
        if self.seen >= self.limit:
            raise BookError("This .exe is not a Flip PDF / FlipBuilder book (no file table in the first %d MB)"
                            % (self.limit // 1048576))
        return False

    def end(self):
        if self.found_at is None:
            raise BookError("This .exe is not a Flip PDF / FlipBuilder book (no file table found)")


def sniff_file(path, limit_bytes):
    """Same check on a file that is already on disk. Returns the table offset or raises BookError."""
    sn = BookSniffer(limit_bytes)
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            sn.feed(chunk)
            if sn.found_at is not None:
                return sn.found_at
    sn.end()
    return sn.found_at


def peek_book(src):
    """Cheap validation of a downloaded .exe: finds the file table and counts the pages, without decompressing
    anything. Returns the page count; raises BookError (safe to show to the user) when it is not a supported book."""
    with open(src, "rb") as f:
        if os.fstat(f.fileno()).st_size < 1024:
            raise BookError("This file is too small to be a book")
        d = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            if d[:2] != b"MZ":
                raise BookError("This is not a Windows .exe file")
            _m, names = _find_table(d)
            n = sum(1 for x in names if x.startswith("files/large/"))
            if not n:
                raise BookError("No page files (files/large/...) in this book: layout not supported")
            return n
        finally:
            try:
                d.close()
            except BufferError:
                pass


def _scan_book(d):
    """Walk the file table and return the page files as [(kind, start, end)] in page order.
    Nothing is kept in memory: pages are read again one at a time by _read_page."""
    m, names = _find_table(d)
    last = max((i for i, n in enumerate(names) if n.startswith("files/large/")), default=-1)
    if last < 0:
        raise BookError("No page files (files/large/...) in this book: layout not supported")
    pos, pages = m.end(), {}
    for k, n in enumerate(names[:last + 1]):
        ext = n.rsplit(".", 1)[-1].lower()
        start = pos
        if ext == "swf":
            sig = bytes(d[pos:pos + 3])
            if sig == b"CWS":
                pos = _inflate_at(d, pos + 8, keep=False)[1]
            elif sig == b"FWS":
                pos += struct.unpack("<I", d[pos + 4:pos + 8])[0]
            else:
                raise BookError("Unsupported SWF format in %s (%r)" % (n, sig))
        elif ext == "png":
            e = d.find(b"IEND", pos)
            if e < 0:
                raise BookError("Damaged PNG inside the book (%s)" % n)
            pos = e + 8
        elif ext in ("jpg", "jpeg"):
            pos = _jpeg_end(d, pos)
        elif ext == "xml":
            nxt = names[k + 1].rsplit(".", 1)[-1].lower() if k + 1 < len(names) else "ocx"
            nxt = "jpg" if nxt == "jpeg" else nxt
            if nxt not in _MAGIC:
                raise BookError("Unsupported file type in the book: " + names[k + 1])
            c = [x for x in (d.find(mg, pos + 6) for mg in _MAGIC[nxt]) if x >= 0]
            if not c:
                raise BookError("Could not find the end of " + n)
            pos = min(c)
        else:
            raise BookError("Unsupported file type in the book: " + n)
        if n.startswith("files/large/"):
            pages[n] = ("swf" if ext == "swf" else "png" if ext == "png" else "jpg", start, pos)
    order = sorted(pages, key=lambda s: int(re.sub(r"\D", "", s.rsplit("/", 1)[-1]) or 0))
    return [pages[k] for k in order]


def _png_to_jpeg(b):
    """Pages stored as PNG are turned into JPEG (transparency flattened on white)."""
    try:
        from PIL import Image
    except ImportError:
        raise BookError("Pillow is required for this book: pip install pillow")
    im = Image.open(io.BytesIO(b))
    im.load()
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[3])
        im = bg
    elif im.mode != "RGB":
        im = im.convert("RGB")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def _read_page(d, kind, start, end):
    """-> ('swf', bytes) or ('jpg', bytes)"""
    if kind == "swf":
        if bytes(d[start:start + 3]) == b"CWS":
            body, _ = _inflate_at(d, start + 8)
            return "swf", b"FWS" + bytes(d[start + 3:start + 8]) + body
        return "swf", bytes(d[start:end])
    if kind == "png":
        return "jpg", _png_to_jpeg(bytes(d[start:end]))
    return "jpg", bytes(d[start:end])


def _swf_size(data):
    br = _BR(data, 8)
    r = br.rect()
    return r[1] * PT, r[3] * PT


# ---------------------------------------------------------------- SWF parsing
class _BR:
    def __init__(s, d, p=0):
        s.d, s.p, s.b = d, p, 0

    def al(s):
        if s.b:
            s.b = 0
            s.p += 1

    def u8(s):
        s.al(); v = s.d[s.p]; s.p += 1; return v

    def u16(s):
        s.al(); v = struct.unpack_from("<H", s.d, s.p)[0]; s.p += 2; return v

    def i16(s):
        s.al(); v = struct.unpack_from("<h", s.d, s.p)[0]; s.p += 2; return v

    def u32(s):
        s.al(); v = struct.unpack_from("<I", s.d, s.p)[0]; s.p += 4; return v

    def bits(s, n, signed=False):
        v = 0
        for _ in range(n):
            v = (v << 1) | ((s.d[s.p] >> (7 - s.b)) & 1)
            s.b += 1
            if s.b == 8:
                s.b = 0; s.p += 1
        if signed and n and v & (1 << (n - 1)):
            v -= 1 << n
        return v

    def rect(s):
        n = s.bits(5); r = [s.bits(n, True) for _ in range(4)]; s.al(); return r

    def matrix(s):
        sx = sy = 1.0; r0 = r1 = 0.0
        if s.bits(1):
            n = s.bits(5); sx = s.bits(n, True) / 65536; sy = s.bits(n, True) / 65536
        if s.bits(1):
            n = s.bits(5); r0 = s.bits(n, True) / 65536; r1 = s.bits(n, True) / 65536
        n = s.bits(5); tx = s.bits(n, True); ty = s.bits(n, True); s.al()
        return (sx, r0, r1, sy, tx, ty)          # PDF order: x' = a x + c y + e ; y' = b x + d y + f


def _color(br, rgba):
    r, g, b = br.u8(), br.u8(), br.u8()
    return (r, g, b, br.u8() if rgba else 255)


def _styles(br, rgba):
    n = br.u8()
    if n == 255: n = br.u16()
    fills = []
    for _ in range(n):
        t = br.u8()
        if t == 0:
            fills.append(("solid", _color(br, rgba)))
        elif t in (0x10, 0x12, 0x13):
            br.matrix(); cnt = br.u8() & 15
            stops = [(br.u8(), _color(br, rgba)) for _ in range(cnt)]
            if t == 0x13: br.i16()
            fills.append(("grad", stops))
        elif 0x40 <= t <= 0x43:
            bid = br.u16(); fills.append(("bitmap", bid, br.matrix()))
        else:
            raise BookError("Unsupported fill style 0x%x" % t)
    n = br.u8()
    if n == 255: n = br.u16()
    lines = []
    for _ in range(n):
        w = br.u16(); lines.append((w, _color(br, rgba)))
    return fills, lines


def _records(br, nf, nl, rgba, fills, lines):
    """Shape records -> list of (fill0, fill1, line, (p0, ctrl|None, p1))."""
    x = y = 0; f0 = f1 = ln = 0; fb = lb = 0
    fills, lines = list(fills), list(lines)
    out = []
    while True:
        if br.bits(1) == 0:
            fl = br.bits(5)
            if fl == 0: break
            if fl & 1:
                n = br.bits(5); x = br.bits(n, True); y = br.bits(n, True)
            if fl & 2: f0 = br.bits(nf)
            if fl & 4: f1 = br.bits(nf)
            if fl & 8: ln = br.bits(nl)
            if fl & 16:
                br.al(); nfs, nls = _styles(br, rgba)
                fb, lb = len(fills), len(lines); fills += nfs; lines += nls
                nf = br.bits(4); nl = br.bits(4)
        else:
            if br.bits(1):
                n = br.bits(4) + 2
                if br.bits(1):
                    dx = br.bits(n, True); dy = br.bits(n, True)
                elif br.bits(1):
                    dx = 0; dy = br.bits(n, True)
                else:
                    dx = br.bits(n, True); dy = 0
                seg = ((x, y), None, (x + dx, y + dy)); x += dx; y += dy
            else:
                n = br.bits(4) + 2
                cx = x + br.bits(n, True); cy = y + br.bits(n, True)
                ax = cx + br.bits(n, True); ay = cy + br.bits(n, True)
                seg = ((x, y), (cx, cy), (ax, ay)); x, y = ax, ay
            out.append((f0 + fb if f0 else 0, f1 + fb if f1 else 0, ln + lb if ln else 0, seg))
    br.al()
    return out, fills, lines


def _chain(segs):
    start = {}
    for i, s in enumerate(segs):
        start.setdefault(s[0], []).append(i)
    used = [False] * len(segs); out = []
    for i in range(len(segs)):
        if used[i]: continue
        used[i] = True; sp = [segs[i]]
        while sp[-1][2] != sp[0][0]:
            nx = next((j for j in start.get(sp[-1][2], ()) if not used[j]), None)
            if nx is None: break
            used[nx] = True; sp.append(segs[nx])
        out.append(sp)
    return out


def _fill_paths(recs):
    per = {}
    for f0, f1, ln, (a, c, b) in recs:
        if f1: per.setdefault(f1, []).append((a, c, b))
        if f0: per.setdefault(f0, []).append((b, c, a))
    return {k: _chain(v) for k, v in per.items()}


def _n(v):
    s = ("%.1f" % v).rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def _path(subs):
    o = []
    for sp in subs:
        p0 = sp[0][0]
        o.append("%d %d m" % p0)
        for a, c, b in sp:
            if c is None:
                o.append("%d %d l" % b)
            else:
                o.append("%s %s %s %s %d %d c" % (_n(a[0] + 2 * (c[0] - a[0]) / 3), _n(a[1] + 2 * (c[1] - a[1]) / 3),
                                                   _n(b[0] + 2 * (c[0] - b[0]) / 3), _n(b[1] + 2 * (c[1] - b[1]) / 3), b[0], b[1]))
        if sp[-1][2] == p0: o.append("h")
    return "\n".join(o)


def _mm(A, B):          # A after B (PDF matrices)
    return (A[0] * B[0] + A[2] * B[1], A[1] * B[0] + A[3] * B[1], A[0] * B[2] + A[2] * B[3],
            A[1] * B[2] + A[3] * B[3], A[0] * B[4] + A[2] * B[5] + A[4], A[1] * B[4] + A[3] * B[5] + A[5])


def _n8(v):
    s = ("%.8f" % v).rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def _mat(M):
    return " ".join(_n8(v) for v in M)


def _jpeg_info(b):
    p = 2
    while p + 9 < len(b):
        if b[p] != 0xff: p += 1; continue
        m = b[p + 1]
        if m in (0xd8, 0x01) or 0xd0 <= m <= 0xd7 or m == 0xff: p += 2 if m != 0xff else 1; continue
        if 0xc0 <= m <= 0xcf and m not in (0xc4, 0xc8, 0xcc):
            h, w = struct.unpack(">HH", b[p + 5:p + 9]); return w, h, b[p + 9]
        p += 2 + struct.unpack(">H", b[p + 2:p + 4])[0]
    raise BookError("Unreadable JPEG header")


class _Font:
    def __init__(s, name, glyphs, codes, adv): s.name, s.glyphs, s.codes, s.adv = name, glyphs, codes, adv


def _font3(body):
    br = _BR(body); fid = br.u16(); flags = br.u8(); br.u8()
    nl = br.u8(); name = bytes(body[br.p:br.p + nl]).decode("latin1"); br.p += nl
    ng = br.u16(); wide = flags & 8; start = br.p
    offs = [br.u32() if wide else br.u16() for _ in range(ng)]
    code_off = br.u32() if wide else br.u16()
    glyphs = []
    for i in range(ng):
        gb = _BR(body, start + offs[i]); nf = gb.bits(4); nl2 = gb.bits(4)
        glyphs.append(_records(gb, nf, nl2, False, [], [])[0])
    cb = _BR(body, start + code_off)
    codes = [cb.u16() for _ in range(ng)]
    adv = None
    if flags & 0x80:
        try:
            cb.i16(); cb.i16(); cb.i16(); adv = [cb.i16() for _ in range(ng)]
        except Exception:
            adv = None
    return fid, _Font(name, glyphs, codes, adv)


def _text2(body, fonts):
    br = _BR(body); cid = br.u16(); br.rect(); m = br.matrix()
    gb = br.u8(); ab = br.u8()
    font = None; color = (0, 0, 0, 255); x = y = h = 0; gl = []
    while body[br.p] != 0:
        b = body[br.p]; br.p += 1; br.b = 0
        if b & 0x80:
            if b & 8: font = fonts.get(br.u16())
            if b & 4: color = _color(br, True)
            if b & 1: x = br.i16()
            if b & 2: y = br.i16()
            if b & 8: h = br.u16()
        else:
            for _ in range(b & 0x7f):
                gi = br.bits(gb); adv = br.bits(ab, True)
                if font is not None: gl.append((font, gi, x, y, h, color))
                x += adv
            br.al()
    return cid, m, gl


def _lossless(body, alpha_tag):
    cid, fmt, w, h = struct.unpack_from("<HBHH", body)
    if w * h > MAX_BITMAP_PIXELS:
        raise BookError("An image inside the book is unreasonably large")
    p = 7; ncol = 0
    if fmt == 3: ncol = body[p] + 1; p += 1
    raw = zlib.decompressobj().decompress(body[p:], w * h * 4 + 4096)
    if fmt == 5:
        rgb = bytearray(w * h * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = raw[1::4], raw[2::4], raw[3::4]
        return cid, {"w": w, "h": h, "raw": bytes(rgb), "alpha": (raw[0::4] if alpha_tag else None)}
    if fmt == 3:
        e = 4 if alpha_tag else 3
        pal = [raw[i * e:i * e + 3] for i in range(ncol)]
        alp = [raw[i * e + 3] for i in range(ncol)] if alpha_tag else None
        stride = (w + 3) & ~3; px = raw[ncol * e:]
        rgb = bytearray(); al = bytearray()
        for r in range(h):
            row = px[r * stride:r * stride + w]
            for i in row:
                rgb += pal[i] if i < ncol else b"\0\0\0"
                if alp: al.append(alp[i] if i < ncol else 0)
        return cid, {"w": w, "h": h, "raw": bytes(rgb), "alpha": bytes(al) if alp else None}
    raise BookError("Unsupported bitmap format %d" % fmt)



_TOLERATED = (BookError, IndexError, KeyError, ValueError, struct.error, zlib.error)


def _where(ex):
    """'IndexError in _text2 line 512' - where an unexpected error came from (shown to the user / logged)."""
    tb = traceback.extract_tb(ex.__traceback__) if ex is not None and ex.__traceback__ else []
    name = "struct.error" if isinstance(ex, struct.error) else type(ex).__name__
    return "%s%s" % (name, " in %s line %d" % (tb[-1].name, tb[-1].lineno) if tb else "")


def _parse_tag(pg, order, t, body):
    if t in (2, 22, 32):
        # DefineShape (2) and DefineShape2 (22) store colours as RGB; only DefineShape3 (32) has RGBA
        rgba = t == 32
        b = _BR(body); sid = b.u16(); b.rect()
        fills, lines = _styles(b, rgba)
        nf = b.bits(4); nl = b.bits(4)
        recs, fills, lines = _records(b, nf, nl, rgba, fills, lines)
        pg["shapes"][sid] = (recs, fills, lines)
    elif t == 75:
        fid, f = _font3(body); pg["fonts"][fid] = f
    elif t == 33:
        cid, m, gl = _text2(body, pg["fonts"]); pg["texts"][cid] = (m, gl)
    elif t in (21, 35):
        cid = struct.unpack_from("<H", body)[0]
        if t == 21:
            jp, al = body[2:], None
        else:
            jl = struct.unpack_from("<I", body, 2)[0]; jp, al = body[6:6 + jl], body[6 + jl:]
        i = jp.find(b"\xff\xd9\xff\xd8")          # SWF: tables-only JPEG + image -> one JPEG
        if i >= 0: jp = jp[:i] + jp[i + 4:]
        w, h2, nc = _jpeg_info(jp)
        pg["bitmaps"][cid] = {"w": w, "h": h2, "jpeg": jp, "nc": nc, "alpha_z": al}
    elif t in (20, 36):
        cid, bm = _lossless(body, t == 36); pg["bitmaps"][cid] = bm
    elif t == 26:
        b = _BR(body); fl = b.u8(); depth = b.u16()
        cid = b.u16() if fl & 2 else None
        m = b.matrix() if fl & 4 else None
        order.append(("p", depth, cid, m, fl))
    elif t == 70:                                   # PlaceObject3: two flag bytes, optional class name, then as PlaceObject2
        b = _BR(body); fl = b.u8(); fl2 = b.u8(); depth = b.u16()
        if fl2 & 8 or (fl2 & 0x10 and fl & 2):
            while body[b.p]: b.p += 1
            b.p += 1
        cid = b.u16() if fl & 2 else None
        m = b.matrix() if fl & 4 else None
        order.append(("p", depth, cid, m, fl))
    elif t == 4:                                    # old PlaceObject
        b = _BR(body); cid = b.u16(); depth = b.u16(); m = b.matrix()
        order.append(("p", depth, cid, m, 6))
    elif t == 28:
        order.append(("r", struct.unpack_from("<H", body)[0]))
    elif t in (10, 48, 11, 37, 39, 83, 46, 84):
        pg["warn"].add("unsupported tag %d" % t)


def parse_swf(data):
    """Parses one page. A damaged or unsupported tag is skipped (recorded in pg["issues"]) instead of failing the page."""
    if data[:3] != b"FWS":
        raise BookError("Not a SWF page")
    br = _BR(data, 8); rect = br.rect(); p = br.p + 4
    if not (0 < rect[1] <= 1_500_000 and 0 < rect[3] <= 1_500_000):
        raise BookError("Invalid page size")
    pg = {"W": rect[1], "H": rect[3], "shapes": {}, "fonts": {}, "texts": {}, "bitmaps": {}, "disp": {},
          "warn": set(), "issues": [], "first_exc": None}
    order = []
    while p + 2 <= len(data):
        h = struct.unpack_from("<H", data, p)[0]; t = h >> 6; ln = h & 63; p += 2
        if ln == 63:
            if p + 4 > len(data): break
            ln = struct.unpack_from("<I", data, p)[0]; p += 4
        body = data[p:p + ln]; p += ln
        if t == 1:
            break
        try:
            _parse_tag(pg, order, t, body)
        except _TOLERATED as ex:
            pg["issues"].append("tag %d skipped (%s)" % (t, _where(ex)))
            pg["first_exc"] = pg["first_exc"] or ex
    disp = pg["disp"]
    for o in order:
        if o[0] == "p":
            _, depth, cid, m, fl = o
            if fl & 1 and depth in disp:
                old = disp[depth]; disp[depth] = (cid if cid is not None else old[0], m or old[1])
            else:
                disp[depth] = (cid, m or (1, 0, 0, 1, 0, 0))
        else:
            disp.pop(o[1], None)
    return pg



# ---------------------------------------------------------------- PDF writer
def _cmap(uni):
    cm = ["/CIDInit /ProcSet findresource begin 12 dict begin begincmap",
          "/CIDSystemName << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
          "/CMapName /Adobe-Identity-UCS def /CMapType 2 def",
          "1 begincodespacerange <00> <FF> endcodespacerange"]
    for i in range(0, len(uni), 100):
        cm.append("%d beginbfchar" % len(uni[i:i + 100])); cm += uni[i:i + 100]; cm.append("endbfchar")
    cm.append("endcmap CMapName currentdict /CMap defineresource pop end end")
    return "\n".join(cm)


def _shrink_bitmap(bm, ppi, dpi, q):
    """Downscale (never upscale) and recompress a JPEG bitmap. Returns a new bitmap dict, or None to keep the original."""
    try:
        import io
        from PIL import Image
    except ImportError:
        return None
    try:
        w, h = bm["w"], bm["h"]
        f = min(1.0, dpi / ppi) if ppi else 1.0
        nw, nh = max(1, int(round(w * f))), max(1, int(round(h * f)))
        im = Image.open(io.BytesIO(bm["jpeg"]))
        im.load()
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        if (nw, nh) != (w, h):
            im = im.resize((nw, nh), Image.LANCZOS)
        if im.mode == "RGB":                               # colour JPEG that is really grey: store one channel
            from PIL import ImageChops
            r, g, b = im.resize((96, 96)).split()
            if max(ImageChops.difference(r, g).getextrema()[1], ImageChops.difference(g, b).getextrema()[1]) <= 8:
                im = im.convert("L")
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=q, optimize=True, progressive=True, **({"subsampling": 2} if im.mode == "RGB" else {}))
        new = buf.getvalue()
        if len(new) >= len(bm["jpeg"]):
            return None                                    # never make an image bigger
        out = {"w": nw, "h": nh, "jpeg": new, "nc": 1 if im.mode == "L" else 3, "alpha_z": None}
        if bm.get("alpha_z"):
            a = Image.frombytes("L", (w, h), zlib.decompress(bm["alpha_z"]))
            if (nw, nh) != (w, h):
                a = a.resize((nw, nh), Image.LANCZOS)
            out["alpha_z"] = zlib.compress(a.tobytes(), 6)
        return out
    except Exception:
        return None


class _PDF:
    def __init__(self, path, img=None):
        self.f = open(path, "wb"); self.f.write(b"%PDF-1.5\n%\xe2\xe3\xcf\xd3\n")
        self.offs = {}; self.n = 2; self.kids = []; self.imgs = {}
        self.img_opt = img
        self.stats = {"img_n": 0, "img_orig": 0, "img_out": 0, "ppi": []}

    def new(self):
        self.n += 1
        return self.n

    def put(self, num, body):
        self.offs[num] = self.f.tell()
        self.f.write(b"%d 0 obj\n" % num + body + b"\nendobj\n")

    def obj(self, text):
        num = self.new(); self.put(num, text.encode("latin1")); return num

    def stream(self, d, data, compress=True):
        num = self.new()
        if compress:
            data = zlib.compress(data, 9); d += " /Filter /FlateDecode"
        self.put(num, ("<< %s /Length %d >>\nstream\n" % (d, len(data))).encode("latin1") + data + b"\nendstream")
        return num

    def image(self, bm, ppi=None):
        shrink = bool(self.img_opt and "jpeg" in bm)
        f = min(1.0, self.img_opt[0] / ppi) if (shrink and ppi) else 1.0
        key = (hashlib.sha1(bm.get("jpeg") or bm["raw"]).hexdigest(), bm["w"], bm["h"], round(f, 2))
        if key in self.imgs: return self.imgs[key]
        orig = len(bm["jpeg"]) if "jpeg" in bm else len(bm["raw"])
        src = bm
        if shrink:
            src = _shrink_bitmap(bm, ppi, *self.img_opt) or bm
        start = self.f.tell()
        w, h = src["w"], src["h"]; sm = ""
        alpha = src.get("alpha_z") if "jpeg" in src else src.get("alpha")
        if alpha:
            if "jpeg" in src:
                sn = self.stream("/Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode" % (w, h), alpha, False)
            else:
                sn = self.stream("/Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceGray /BitsPerComponent 8" % (w, h), alpha)
            sm = " /SMask %d 0 R" % sn
        if "jpeg" in src:
            cs = "/DeviceGray" if src["nc"] == 1 else "/DeviceRGB"
            num = self.stream("/Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace %s /BitsPerComponent 8 /Filter /DCTDecode%s" % (w, h, cs, sm), src["jpeg"], False)
        else:
            num = self.stream("/Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceRGB /BitsPerComponent 8%s" % (w, h, sm), src["raw"])
        st = self.stats
        st["img_n"] += 1; st["img_orig"] += orig; st["img_out"] += self.f.tell() - start
        if ppi: st["ppi"].append(ppi)
        self.imgs[key] = num
        return num

    def type3(self, font, used):
        """Type3 font objects for the used glyphs. Returns {glyph index: (resource name, code)}, resources {name: obj}."""
        m, res = {}, {}
        used = sorted(used)
        for ci in range(0, len(used), 255):
            part = used[ci:ci + 255]; procs = []; widths = []; uni = []; names = []
            for k, gi in enumerate(part, 1):
                adv = font.adv[gi] if font.adv and gi < len(font.adv) else 10240
                subs = [[((a[0], -a[1]), None if c is None else (c[0], -c[1]), (b[0], -b[1])) for a, c, b in sp]
                        for sps in _fill_paths(font.glyphs[gi] if gi < len(font.glyphs) else []).values() for sp in sps]       # y-up glyph space
                if not subs:
                    body = "%d 0 d0" % adv
                else:
                    pts = [q for sp in subs for a, c, b in sp for q in ((a, b) if c is None else (a, c, b))]
                    xs = [q[0] for q in pts]; ys = [q[1] for q in pts]
                    body = "%d 0 %d %d %d %d d1\n%s\nf*" % (adv, min(xs) - 1, min(ys) - 1, max(xs) + 1, max(ys) + 1, _path(subs))
                procs.append(self.stream("", body.encode("latin1")))
                widths.append(adv); names.append("g%d" % k)
                u = font.codes[gi] if gi < len(font.codes) else 0x20
                uni.append("<%02X> <%04X>" % (k, u if u else 0x20))
            tu = self.stream("", _cmap(uni).encode("latin1"))
            fo = self.obj("<< /Type /Font /Subtype /Type3 /FontBBox [-2000 -8000 30000 24000] /FontMatrix [%s 0 0 %s 0 0] "
                          "/CharProcs << %s >> /Encoding << /Type /Encoding /Differences [1 %s] >> /FirstChar 1 /LastChar %d "
                          "/Widths [%s] /Resources << >> /ToUnicode %d 0 R >>"
                          % ("%.9f" % (1 / EM), "%.9f" % (1 / EM), " ".join("/%s %d 0 R" % (a, b) for a, b in zip(names, procs)),
                             " ".join("/" + a for a in names), len(part), " ".join(str(w) for w in widths), tu))
            rn = "F%d" % fo; res[rn] = fo
            for k, gi in enumerate(part, 1): m[gi] = (rn, k)
        return m, res

    def page(self, wpt, hpt, content, fonts=None, xobjs=None, gs=None):
        c = self.stream("", content.encode("latin1"))
        r = []
        if fonts: r.append("/Font << %s >>" % " ".join("/%s %d 0 R" % kv for kv in fonts.items()))
        if xobjs: r.append("/XObject << %s >>" % " ".join("/%s %d 0 R" % kv for kv in xobjs.items()))
        if gs: r.append("/ExtGState << %s >>" % " ".join("/%s << /ca %s >>" % kv for kv in gs.items()))
        pn = self.obj("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %s %s] /Resources << %s >> /Contents %d 0 R >>"
                      % (_n(wpt), _n(hpt), " ".join(r), c))
        self.kids.append(pn)

    def close(self):
        self.put(2, ("<< /Type /Pages /Kids [%s] /Count %d >>" % (" ".join("%d 0 R" % k for k in self.kids), len(self.kids))).encode())
        self.put(1, b"<< /Type /Catalog /Pages 2 0 R >>")
        xr = self.f.tell(); n = self.n
        self.f.write(b"xref\n0 %d\n0000000000 65535 f \n" % (n + 1))
        for i in range(1, n + 1):
            self.f.write(b"%010d 00000 n \n" % self.offs[i])
        self.f.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (n + 1, xr))
        self.f.close()



# ---------------------------------------------------------------- page building
def _build_page(pdf, pg):
    """Returns (content, fonts, xobjs, gs, W_pt, H_pt). Shapes are vector, text is real text (Type3 + ToUnicode).
    One object that cannot be drawn is left out (recorded in pg["issues"]); the rest of the page is kept."""
    W, H = pg["W"] * PT, pg["H"] * PT
    P = (PT, 0, 0, -PT, 0, H)
    ops, xobjs, gs, fres, fmap = [], {}, {}, {}, {}
    used = {}
    for cid, _ in pg["disp"].values():
        if cid in pg["texts"]:
            for font, gi, *_r in pg["texts"][cid][1]:
                used.setdefault(id(font), (font, set()))[1].add(gi)
    for fid_, (font, gis) in used.items():
        try:
            fm, fr = pdf.type3(font, gis)
        except MemoryError:
            raise
        except Exception as ex:
            pg["issues"].append("font skipped (%s)" % _where(ex)); pg["first_exc"] = pg["first_exc"] or ex
            continue
        fmap[fid_] = fm; fres.update(fr)
    for depth in sorted(pg["disp"]):
        cid, M = pg["disp"][depth]
        mark = len(ops)
        try:
            if cid in pg["shapes"]:
                recs, fills, lines = pg["shapes"][cid]
                ops.append("q %s cm" % _mat(_mm(P, M)))
                for fi, subs in _fill_paths(recs).items():
                    if not 0 < fi <= len(fills): continue      # fill style index outside the list
                    st = fills[fi - 1]; path = _path(subs)
                    if st[0] == "bitmap":
                        bm = pg["bitmaps"].get(st[1])
                        if bm is None: continue
                        T = _mm(_mm(P, M), st[2]); px = math.sqrt(abs(T[0] * T[3] - T[1] * T[2]))
                        name = "Im%d" % pdf.image(bm, 72.0 / px if px > 0 else None); xobjs[name] = int(name[2:])
                        im = _mm(st[2], (bm["w"], 0, 0, -bm["h"], 0, bm["h"]))
                        ops.append("q\n%s\nW* n\n%s cm /%s Do\nQ" % (path, _mat(im), name))
                        continue
                    if st[0] == "grad":
                        pg["warn"].add("gradient drawn as a flat colour")
                        r, g, b, a = st[1][len(st[1]) // 2][1]
                    else:
                        r, g, b, a = st[1]
                    if a < 255:
                        k = "A%d" % a; gs[k] = _n(a / 255); ops.append("/%s gs" % k)
                    ops.append("%s %s %s rg\n%s\nf*" % (_n(r / 255), _n(g / 255), _n(b / 255), path))
                    if a < 255: ops.append("/A255 gs"); gs["A255"] = "1"
                bystyle = {}
                for f0, f1, ln, seg in recs:
                    if ln: bystyle.setdefault(ln, []).append(seg)
                for ln, segs in bystyle.items():
                    if not 0 < ln <= len(lines): continue
                    w, (r, g, b, a) = lines[ln - 1]
                    ops.append("%s w %s %s %s RG 1 J 1 j" % (_n(max(w, 1)), _n(r / 255), _n(g / 255), _n(b / 255)))
                    ops.append(_path([[s] for s in segs]).replace(" h", "") + "\nS")
                ops.append("Q")
            elif cid in pg["texts"]:
                tm, gl = pg["texts"][cid]
                Tm0 = _mm(P, _mm(M, tm)); last = None
                for font, gi, gx, gy, h, color in gl:
                    fm = fmap.get(id(font))
                    if not fm or gi not in fm:
                        continue
                    rn, code = fm[gi]
                    if color != last:
                        ops.append("%s %s %s rg" % (_n(color[0] / 255), _n(color[1] / 255), _n(color[2] / 255))); last = color
                    T = _mm(Tm0, (h, 0, 0, -h, gx, gy))
                    ops.append("BT /%s 1 Tf %s Tm <%02X> Tj ET" % (rn, _mat(T), code))
        except MemoryError:
            raise
        except Exception as ex:
            del ops[mark:]                       # drop the half-written object so the content stream stays valid
            pg["issues"].append("object %s skipped (%s)" % (cid, _where(ex))); pg["first_exc"] = pg["first_exc"] or ex
    return "\n".join(ops), fres, xobjs, gs, W, H


def _jpeg_page(pdf, data, ref_w):
    """A page stored as a JPEG. Width follows the book's vector pages (when there are any) so sizes match."""
    w, h, nc = _jpeg_info(data)
    Wp = ref_w if ref_w else w * 72.0 / 150.0
    Hp = Wp * h / w
    ppi = w / (Wp / 72.0)
    bn = pdf.image({"w": w, "h": h, "jpeg": data, "nc": nc}, ppi)
    pdf.page(Wp, Hp, "q %s 0 0 %s 0 0 cm /Im%d Do Q" % (_n(Wp), _n(Hp), bn), None, {"Im%d" % bn: bn})


def convert_exe_to_pdf(src, dst, level=DEFAULT_LEVEL, progress=None, cancelled=None):
    """Convert a Flip PDF / FlipBuilder .exe book to a PDF ("Vector lite").

    progress(done, total) is called after every page; cancelled() -> True stops the job (raises Cancelled).
    Raises BookError (message is safe to show to the user). On any failure no output file is left behind.
    Returns {"pages": n, "bytes": size, "notes": [str, ...]}.
    """
    if level not in LITE:
        raise ValueError("level must be one of %s" % ", ".join(LITE))
    src, dst = Path(src), Path(dst)
    tmp = dst.with_name(dst.name + ".part")
    pdf, warn, issues = None, set(), []
    d = None
    try:
        f = open(src, "rb")
        try:
            if os.fstat(f.fileno()).st_size < 1024:
                raise BookError("This file is too small to be a book")
            d = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        finally:
            f.close()
        entries = _scan_book(d)
        n = len(entries)
        if not n:
            raise BookError("No pages found in this book")
        if n > MAX_PAGES:
            raise BookError("This book has too many pages (%d, limit %d)" % (n, MAX_PAGES))
        ref = None
        for kind, s_, e_ in entries:                    # size of the vector pages, for JPEG pages and for blank fallbacks
            if kind == "swf":
                ref = _swf_size(_read_page(d, kind, s_, e_)[1])
                break
        ref_w = ref[0] if ref else None
        pdf = _PDF(str(tmp), LITE[level])
        bad, first_exc = 0, None
        for i, (kind, s_, e_) in enumerate(entries, 1):
            if cancelled and cancelled():
                raise Cancelled()
            k, data = _read_page(d, kind, s_, e_)
            try:
                if k == "swf":
                    pg = parse_swf(data)
                    c, fonts, xo, gs, W, H = _build_page(pdf, pg)
                    pdf.page(W, H, c, fonts, xo, gs)
                    warn |= pg["warn"]
                    issues.extend("page %d: %s" % (i, m) for m in pg["issues"])
                    first_exc = first_exc or pg["first_exc"]
                else:
                    _jpeg_page(pdf, data, ref_w)
            except _TOLERATED as ex:                    # one unreadable page must not lose the whole book
                bad += 1
                first_exc = first_exc or ex
                issues.append("page %d could not be read (%s)" % (i, _where(ex)))
                pdf.page(ref[0] if ref else 595.0, ref[1] if ref else 842.0, "")
            del data
            if progress:
                progress(i, n)
        if bad * 2 > n:
            raise BookError("Most pages could not be read (%d of %d). First problem: %s" % (bad, n, _where(first_exc))) from first_exc
        pdf.close(); pdf = None
        os.replace(tmp, dst)
        return {"pages": n, "bytes": dst.stat().st_size, "notes": sorted(warn),
                "issues": issues[:25] + (["... and %d more" % (len(issues) - 25)] if len(issues) > 25 else [])}
    except (BookError, Cancelled):
        raise
    except MemoryError:
        raise BookError("This book needs more memory than this server allows")
    except (struct.error, IndexError, KeyError, ValueError, zlib.error) as ex:
        raise BookError("Could not read this book (%s). It may use a layout this tool does not support yet"
                        % _where(ex)) from ex
    finally:
        if pdf is not None:
            try:
                pdf.f.close()
            except Exception:
                pass
        tmp.unlink(missing_ok=True)
        if d is not None:
            try:
                d.close()
            except BufferError:
                pass


def main(argv):
    args = [a for a in argv[1:] if a != "--json"]
    as_json = len(args) != len(argv) - 1
    if not args:
        print(__doc__)
        return 2
    src = Path(args[0])
    dst = Path(args[1]) if len(args) > 1 else src.with_suffix(".pdf")
    if not as_json:
        try:
            os.nice(10)                      # be polite to the rest of the server (the bot sets this itself)
        except Exception:
            pass

    def out(**kw):
        print(json.dumps(kw, ensure_ascii=False), flush=True)

    last = [0.0]

    def prog(i, n):
        if as_json:
            now = time.time()
            if i == n or now - last[0] >= 1.0:          # at most one line per second
                last[0] = now
                out(p=i, n=n)
        else:
            print("\r%d/%d" % (i, n), end="", flush=True)

    try:
        info = convert_exe_to_pdf(src, dst, progress=prog)
    except (BookError, Cancelled, OSError) as ex:
        c = ex.__cause__ or ex
        trace = "".join(traceback.format_exception(type(c), c, c.__traceback__)[-5:])
        if as_json:
            out(error=str(ex), trace=trace)
        else:
            print("\nError:", ex)
            print(trace)
        return 1
    if as_json:
        out(done=True, pages=info["pages"], bytes=info["bytes"], notes=info["notes"], issues=info["issues"])
    else:
        print("\nOK: %d pages, %.1f MB -> %s" % (info["pages"], info["bytes"] / 1048576, dst))
        for w in info["notes"]:
            print("Note:", w)
        for w in info["issues"]:
            print("PROBLEM:", w)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
