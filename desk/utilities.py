#!/usr/bin/env python3
"""Tatsuki's Desk — small tools: QR codes, URL shortening and PDF compression (the character count lives in the page).

Nothing is kept: a QR image stays in memory, a PDF stays in a private temp folder for an hour at most, and saving goes
through the browser's own download so this server never writes outside that folder. Only shorten() talks to the outside,
and only when the button is pressed. The PDF work is done by macOS itself (app/pdfshrink.swift, built on first use).
"""
import json
import os
import re
import secrets
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

ROOT = os.path.dirname(os.path.abspath(__file__))
TOOL_SOURCE = os.path.join(ROOT, "app", "pdfshrink.swift")
TOOL = os.environ.get("DESK_UTIL_TOOL") or os.path.expanduser("~/Library/Application Support/TatsukiDesk/pdfshrink")
PDF_MAX = 500 * 1024 * 1024
KEEP_SECONDS = 3600
LEVELS = ("high", "standard", "small")
SHORTENER = "https://is.gd/create.php"

_lock = threading.Lock()
_build_lock = threading.Lock()
_jobs = {}  # job id -> {dir, name, before, level, after, pages, at}
_files = {}  # token -> {path or data, name, ctype, at}
_qr_tokens = []


# --- QR code (ISO/IEC 18004, byte mode, error correction M: survives about 15% damage) ---

ECC_PER_BLOCK = (0, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28)
BLOCKS = (0, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49)
FORMAT_M = 0  # the two format bits that mean level M
QR_MAX_BYTES = 2331


def _raw_modules(ver):
    """Modules left for data and error correction once the fixed patterns are placed."""
    n = (16 * ver + 128) * ver + 64
    if ver >= 2:
        align = ver // 7 + 2
        n -= (25 * align - 10) * align - 55
        if ver >= 7:
            n -= 36
    return n


def _data_capacity(ver):
    return _raw_modules(ver) // 8 - ECC_PER_BLOCK[ver] * BLOCKS[ver]


def _gf_mul(x, y):
    z = 0
    for i in range(7, -1, -1):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree):
    out = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            out[j] = _gf_mul(out[j], root)
            if j + 1 < degree:
                out[j] ^= out[j + 1]
        root = _gf_mul(root, 2)
    return out


def _rs_remainder(data, divisor):
    out = [0] * len(divisor)
    for b in data:
        factor = b ^ out.pop(0)
        out.append(0)
        for i, coef in enumerate(divisor):
            out[i] ^= _gf_mul(coef, factor)
    return out


def _codewords(data, ver):
    """Payload bytes to the interleaved codewords, with padding and error correction."""
    count_bits = 8 if ver <= 9 else 16
    bits = [0, 1, 0, 0]
    bits += [(len(data) >> i) & 1 for i in range(count_bits - 1, -1, -1)]
    for b in data:
        bits += [(b >> i) & 1 for i in range(7, -1, -1)]
    capacity = _data_capacity(ver)
    bits += [0] * min(4, capacity * 8 - len(bits))
    bits += [0] * (-len(bits) % 8)
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(words) < capacity:
        words.append(pad)
        pad ^= 0xEC ^ 0x11

    blocks_n, ecc_n = BLOCKS[ver], ECC_PER_BLOCK[ver]
    raw = _raw_modules(ver) // 8
    short_n = blocks_n - raw % blocks_n
    short_len = raw // blocks_n
    divisor = _rs_divisor(ecc_n)
    blocks, k = [], 0
    for i in range(blocks_n):
        size = short_len - ecc_n + (0 if i < short_n else 1)
        part = words[k:k + size]
        k += size
        ecc = _rs_remainder(part, divisor)
        if i < short_n:
            part = part + [0]  # a hole, skipped when reading the columns
        blocks.append(part + ecc)
    out = []
    for i in range(len(blocks[0])):
        for j, block in enumerate(blocks):
            if i != short_len - ecc_n or j >= short_n:
                out.append(block[i])
    return out


def _penalty(m):
    n = len(m)
    score = 0
    finder = ([1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0], [0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1])
    lines = [list(r) for r in m] + [[m[y][x] for y in range(n)] for x in range(n)]
    for line in lines:
        run, prev = 0, None
        for v in line:
            if v == prev:
                run += 1
            else:
                if run >= 5:
                    score += run - 2
                run, prev = 1, v
        if run >= 5:
            score += run - 2
        for i in range(n - 10):
            if line[i:i + 11] in finder:
                score += 40
    for y in range(n - 1):
        for x in range(n - 1):
            if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                score += 3
    dark = sum(sum(r) for r in m)
    total = n * n  # always odd, so the difference below is never zero
    score += ((abs(dark * 20 - total * 10) + total - 1) // total - 1) * 10
    return score


_MASKS = (
    lambda x, y: (x + y) % 2 == 0, lambda x, y: y % 2 == 0, lambda x, y: x % 3 == 0, lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (x // 3 + y // 2) % 2 == 0, lambda x, y: x * y % 2 + x * y % 3 == 0,
    lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0, lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
)


def qr_matrix(text):
    """Text to rows of 0/1. Raises ValueError when it does not fit in a QR code."""
    data = text.encode("utf-8")
    if not data:
        raise ValueError("empty")
    for ver in range(1, 41):
        head = 4 + (8 if ver <= 9 else 16)
        if (head + len(data) * 8 + 7) // 8 <= _data_capacity(ver):
            break
    else:
        raise ValueError("too long")

    n = ver * 4 + 17
    m = [[0] * n for _ in range(n)]
    fixed = [[False] * n for _ in range(n)]

    def put(x, y, v):
        m[y][x] = 1 if v else 0
        fixed[y][x] = True

    for i in range(n):
        put(6, i, i % 2 == 0)
        put(i, 6, i % 2 == 0)
    for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                x, y = cx + dx, cy + dy
                if 0 <= x < n and 0 <= y < n:
                    d = max(abs(dx), abs(dy))
                    put(x, y, d != 2 and d != 4)
    if ver >= 2:
        count = ver // 7 + 2
        step = (ver * 8 + count * 3 + 5) // (count * 4 - 4) * 2
        pos = [6] + sorted(n - 7 - i * step for i in range(count - 1))
        last = len(pos) - 1
        for i, ax in enumerate(pos):
            for j, ay in enumerate(pos):
                if (i, j) in ((0, 0), (0, last), (last, 0)):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        put(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)

    def put_format(mask):
        value = FORMAT_M << 3 | mask
        rem = value
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (value << 10 | rem) ^ 0x5412
        bit = lambda i: (bits >> i) & 1
        for i in range(6):
            put(8, i, bit(i))
        put(8, 7, bit(6))
        put(8, 8, bit(7))
        put(7, 8, bit(8))
        for i in range(9, 15):
            put(14 - i, 8, bit(i))
        for i in range(8):
            put(n - 1 - i, 8, bit(i))
        for i in range(8, 15):
            put(8, n - 15 + i, bit(i))
        put(8, n - 8, 1)

    put_format(0)
    if ver >= 7:
        rem = ver
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        bits = ver << 12 | rem
        for i in range(18):
            a, b = n - 11 + i % 3, i // 3
            put(a, b, (bits >> i) & 1)
            put(b, a, (bits >> i) & 1)

    words = _codewords(data, ver)
    i = 0
    total = len(words) * 8
    x = n - 1
    while x >= 1:
        if x == 6:
            x = 5
        for vert in range(n):
            for j in (0, 1):
                cx = x - j
                upward = (x + 1) & 2 == 0
                y = n - 1 - vert if upward else vert
                if not fixed[y][cx] and i < total:
                    m[y][cx] = (words[i >> 3] >> (7 - (i & 7))) & 1
                    i += 1
        x -= 2

    best, best_score = None, None
    for mask, rule in enumerate(_MASKS):
        trial = [r[:] for r in m]
        for y in range(n):
            for x in range(n):
                if not fixed[y][x] and rule(x, y):
                    trial[y][x] ^= 1
        saved, m[:] = [r[:] for r in m], trial
        put_format(mask)
        score = _penalty(m)
        if best_score is None or score < best_score:
            best, best_score = [r[:] for r in m], score
        m[:] = saved
    return best


def _png(rows, scale=12, quiet=4):
    """Rows of 0/1 to a black-on-white one-bit PNG, with the quiet zone readers need."""
    size = (len(rows) + quiet * 2) * scale
    blank = b"\0" + b"\xff" * ((size + 7) // 8)
    raw = bytearray(blank * (quiet * scale))
    for r in rows:
        line = [1] * (quiet * scale)
        for v in r:
            line += [0 if v else 1] * scale
        line += [1] * (quiet * scale)
        packed = bytearray(b"\0")
        for i in range(0, size, 8):
            chunk = line[i:i + 8] + [1] * (8 - len(line[i:i + 8]))
            packed.append(int("".join(map(str, chunk)), 2))
        raw += bytes(packed) * scale
    raw += blank * (quiet * scale)

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 1, 0, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b"")


def _token(entry):
    token = secrets.token_urlsafe(16)
    entry["at"] = time.time()
    with _lock:
        _files[token] = entry
    return token


def qr(text):
    """Returns (status, body) for the page: the rows to draw and a link to the PNG."""
    if not isinstance(text, str) or not text.strip():
        return 400, {"error": "Enter a link"}
    if len(text.encode("utf-8")) > QR_MAX_BYTES:
        return 413, {"error": f"Too long. A QR code holds up to {QR_MAX_BYTES} bytes"}
    rows = qr_matrix(text)
    token = _token({"data": _png(rows), "name": "QR code.png", "ctype": "image/png"})
    with _lock:
        _qr_tokens.append(token)
        while len(_qr_tokens) > 8:  # each keystroke makes one; only the latest few can still be saved
            _files.pop(_qr_tokens.pop(0), None)
    return 200, {"rows": ["".join(map(str, r)) for r in rows], "file": token}


# --- URL shortening: the one thing here that leaves this Mac ---

LOCAL_HOST = re.compile(r"^(localhost|.*\.local|.*\.localhost|127\.\d+\.\d+\.\d+|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|\[?::1\]?)$", re.I)
SHORTENER_ERRORS = {
    1: "This address could not be shortened. Check its format",
    2: "This address could not be shortened",
    3: "The service is busy. Wait a moment, then try again",
    4: "is.gd seems to be down for now. Wait a moment",
}


def shorten(url, opener=urllib.request.urlopen):
    if not isinstance(url, str) or not url.strip():
        return 400, {"error": "Enter an address to shorten"}
    url = url.strip()
    if len(url) > 5000 or re.search(r"\s", url):
        return 400, {"error": "Check the address format"}
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname
    except ValueError:
        return 400, {"error": "Check the address format"}
    if parts.scheme.lower() not in ("http", "https") or not host:
        return 400, {"error": "Only addresses starting with http or https can be shortened"}
    if LOCAL_HOST.match(host):
        return 400, {"error": "Addresses for this Mac or the home network are not shortened"}
    request = urllib.request.Request(SHORTENER + "?" + urllib.parse.urlencode({"format": "json", "url": url}), headers={"User-Agent": "TatsukiDesk"})
    try:
        with opener(request, timeout=15) as r:
            answer = json.loads(r.read(100000).decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            answer = json.loads(e.read(100000).decode("utf-8"))
        except ValueError:
            return 502, {"error": "No answer from is.gd"}
    except (OSError, ValueError):
        return 502, {"error": "Could not reach is.gd. Check the connection"}
    short = answer.get("shorturl") if isinstance(answer, dict) else None
    if isinstance(short, str) and short.startswith("https://is.gd/"):
        return 200, {"short": short}
    code = answer.get("errorcode") if isinstance(answer, dict) else None
    return 502, {"error": SHORTENER_ERRORS.get(code, "Could not shorten")}


# --- PDF compression ---

def tool():
    """The helper, built the first time (or again when its source changes). None when this Mac cannot build it."""
    with _build_lock:
        try:
            if os.path.exists(TOOL) and os.path.getmtime(TOOL) >= os.path.getmtime(TOOL_SOURCE):
                return TOOL
            os.makedirs(os.path.dirname(TOOL), exist_ok=True)
            subprocess.run(["swiftc", "-O", "-framework", "Quartz", "-framework", "PDFKit", TOOL_SOURCE, "-o", TOOL], check=True, capture_output=True, timeout=240)
            return TOOL
        except (OSError, subprocess.SubprocessError):
            return None


def clean_name(name):
    """A safe file name from what the page sent; only ever used as the suggested download name."""
    base = os.path.basename(str(name or "").replace("\\", "/"))
    base = re.sub(r"[\x00-\x1f/:]", "", base).strip()
    if base.lower().endswith(".pdf"):
        base = base[:-4]
    return base[:120] or "PDF"


def purge(now=None):
    now = now or time.time()
    with _lock:
        for job_id in [k for k, v in _jobs.items() if now - v["at"] > KEEP_SECONDS]:
            shutil.rmtree(_jobs.pop(job_id)["dir"], ignore_errors=True)
        live = {v.get("file") for v in _jobs.values()}
        for token in [k for k, v in _files.items() if "path" in v and k not in live]:
            del _files[token]
        for token in [k for k, v in _files.items() if "data" in v and now - v["at"] > KEEP_SECONDS]:
            _files.pop(token, None)


def start_pdf(stream, length, name, level):
    """Takes the upload and compresses it. Returns (status, body)."""
    if level not in LEVELS:
        return 400, {"error": "Choose a quality"}
    if length <= 0:
        return 400, {"error": "The file is empty"}
    purge()
    folder = tempfile.mkdtemp(prefix="desk-pdf-")
    source = os.path.join(folder, "source.pdf")
    head, left = b"", length
    try:
        with open(source, "wb") as f:
            while left > 0:
                block = stream.read(min(1024 * 1024, left))
                if not block:
                    break
                if not head:
                    head = block[:1024]
                f.write(block)
                left -= len(block)
    except OSError:
        shutil.rmtree(folder, ignore_errors=True)
        return 500, {"error": "Could not receive the file. Check free space on the Mac"}
    if left > 0:
        shutil.rmtree(folder, ignore_errors=True)
        return 400, {"error": "The upload was cut off. Choose the file again"}
    if b"%PDF-" not in head:
        shutil.rmtree(folder, ignore_errors=True)
        return 415, {"error": "This does not look like a PDF"}
    job_id = secrets.token_urlsafe(16)
    with _lock:
        _jobs[job_id] = {"dir": folder, "name": clean_name(name), "before": length, "at": time.time()}
    return run_pdf(job_id, level)


EXIT_WORDS = {
    3: "PDFs with a password or protection cannot be compressed",
    4: "Could not read this PDF",
    5: "Compressing would change the content, so it stopped. The original file is unchanged",
    6: "Could not write the compressed file",
}


def run_pdf(job_id, level):
    with _lock:
        job = _jobs.get(job_id)
    if not job:
        return 404, {"error": "This expired. Choose the file again"}
    if level not in LEVELS:
        return 400, {"error": "Choose a quality"}
    helper = tool()
    if not helper:
        return 500, {"error": "Could not set up compression. Run xcode-select --install, then try again"}
    out = os.path.join(job["dir"], f"{level}.pdf")
    try:
        done = subprocess.run([helper, os.path.join(job["dir"], "source.pdf"), out, level], capture_output=True, timeout=600)
    except subprocess.TimeoutExpired:
        return 504, {"error": "It took too long, so it stopped"}
    except OSError:
        return 500, {"error": "Could not start compressing"}
    if done.returncode != 0 or not os.path.exists(out):
        return 422, {"error": EXIT_WORDS.get(done.returncode, "This PDF could not be compressed")}
    try:
        pages = json.loads(done.stdout.decode("utf-8"))["pages"]
    except (ValueError, KeyError):
        pages = None
    after = os.path.getsize(out)
    body = {"id": job_id, "name": job["name"], "level": level, "before": job["before"], "after": after, "pages": pages, "smaller": after < job["before"]}
    with _lock:
        if job.get("file"):
            _files.pop(job["file"], None)
        job["at"] = time.time()
        token = None
        if body["smaller"]:
            token = secrets.token_urlsafe(16)
            _files[token] = {"path": out, "name": job["name"] + "-compressed.pdf", "ctype": "application/pdf", "at": time.time()}
        job["file"] = token
    body["file"] = token
    return 200, body


def clear_pdf(job_id):
    with _lock:
        job = _jobs.pop(job_id, None)
        if job and job.get("file"):
            _files.pop(job["file"], None)
    if job:
        shutil.rmtree(job["dir"], ignore_errors=True)
    return 200, {"ok": True}


def get_file(token):
    with _lock:
        return _files.get(token)


def disposition(name):
    """Content-Disposition with the Japanese name kept (RFC 5987) and a plain fallback."""
    ascii_name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{urllib.parse.quote(name)}"
