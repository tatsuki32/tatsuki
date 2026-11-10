#!/usr/bin/env python3
"""長時間録音・文字起こしツール — ローカルサーバー(Python標準ライブラリのみ)

  python3 server.py            → http://localhost:8791/
  PORT=9000 python3 server.py  → ポート変更

安定動作のための設計:
  - 録音は数秒ごとにディスクへ追記する。ブラウザやサーバーが落ちても、そこまでの音声は残る。
  - 文字起こしは約5分ごとに無音の位置で分割し、1区間ずつ別プロセスで処理して結果を保存する。
    落ちても再起動すれば、終わっていない区間から自動で再開する。
  - 区間が失敗したら再試行し、最後はCPU処理に切り替える。それでも駄目ならその区間だけ失敗として残す。
保存先は desk/data/transcribe/sessions/ 。音声も文字もこのMacの外には出ない。
Desk のサーバー(4747)が起動時に立ち上げる(desk/transcribe_desk.py)。
"""
import array
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(ROOT, 'public')
# 録音と文字は Desk の個人データ置き場(desk/data/transcribe)。リポジトリにも非公開の保管にも入らない
DATA = os.environ.get('TRANSCRIBE_DIR') or os.path.join(os.path.dirname(ROOT), 'data', 'transcribe')
SESSIONS = os.path.join(DATA, 'sessions')
ENGINE = os.environ.get('TRANSCRIBE_ENGINE') or os.path.join(ROOT, 'engine')
WHISPER = os.path.join(ENGINE, 'whisper-cli')
MODEL = os.path.join(ENGINE, 'models', 'ggml-large-v3-turbo.bin')
VAD_MODEL = os.path.join(ENGINE, 'models', 'ggml-silero-v5.1.2.bin')
PID_FILE = os.path.join(DATA, 'whisper.pid')
PORT = int(os.environ.get('PORT', '8791'))

RATE = 16000
BYTES_PER_SEC = RATE * 2
CHUNK_SEC = 300          # 1区間の長さの目安
SEARCH_SEC = 60          # 区間の終わり何秒の中から最も静かな位置を探すか
RECORDING_STALE_SEC = 30 # これだけ音声が届かなければ録音は中断扱い
LANGUAGES = {'ja', 'en', 'vi', 'zh', 'ko', 'auto'}
ACTIVE = ('queued', 'converting', 'transcribing')
ID_RE = re.compile(r'^[0-9a-f]{32}$')

lock = threading.RLock()
wake = threading.Event()
current = {'sid': None, 'proc': None}


# ---------- 保存 ----------

def sdir(sid):
    return os.path.join(SESSIONS, sid)


def write_json(path, obj):
    tmp = f'{path}.{uuid.uuid4().hex}.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def load_meta(sid):
    return read_json(os.path.join(sdir(sid), 'meta.json')) if ID_RE.match(sid or '') else None


def update_meta(sid, **fields):
    with lock:
        meta = load_meta(sid)
        if meta is None:
            return None
        meta.update(fields)
        write_json(os.path.join(sdir(sid), 'meta.json'), meta)
        return meta


def pcm_path(sid):
    return os.path.join(sdir(sid), 'audio.pcm')


def pcm_size(sid):
    try:
        return os.path.getsize(pcm_path(sid))
    except OSError:
        return 0


def public_meta(meta):
    """録音が途絶えているものは「中断」として見せる。保存値は変えない。"""
    meta = dict(meta)
    sid = meta['id']
    meta['duration'] = pcm_size(sid) / BYTES_PER_SEC
    if meta['status'] == 'recording' and time.time() - meta.get('last_chunk', 0) > RECORDING_STALE_SEC:
        meta['status'] = 'interrupted'
    return meta


def list_sessions():
    os.makedirs(SESSIONS, exist_ok=True)
    out = []
    for sid in os.listdir(SESSIONS):
        meta = load_meta(sid)
        if meta:
            out.append(public_meta(meta))
    out.sort(key=lambda m: m['created'], reverse=True)
    return out


def create_session(title, language, source, status):
    sid = uuid.uuid4().hex
    os.makedirs(sdir(sid))
    now = time.time()
    meta = {'id': sid, 'title': title, 'language': language, 'source': source, 'status': status,
            'created': now, 'last_chunk': now, 'done_chunks': 0, 'total_chunks': 0, 'failed_chunks': [],
            'error': None}
    write_json(os.path.join(sdir(sid), 'meta.json'), meta)
    open(pcm_path(sid), 'ab').close()
    return meta


def append_chunk(sid, offset, body):
    """同じ位置を再送されても音声が重複しないよう、バイト位置で管理する。"""
    with lock:
        meta = load_meta(sid)
        if meta is None:
            return None
        size = pcm_size(sid)
        if size % 2:  # 書き込み途中で落ちた場合の端数
            with open(pcm_path(sid), 'r+b') as f:
                f.truncate(size - 1)
            size -= 1
        if offset > size:
            return size  # 抜けがある。ブラウザ側は返されたsizeから送り直す
        skip = size - offset
        if skip < len(body):
            with open(pcm_path(sid), 'ab') as f:
                f.write(body[skip:])
                f.flush()
                os.fsync(f.fileno())
            size += len(body) - skip
        if meta['status'] == 'recording':
            update_meta(sid, last_chunk=time.time())
        return size


def queue_transcription(sid, language=None, restart=False):
    with lock:
        meta = load_meta(sid)
        if meta is None:
            return None
        if restart:
            shutil.rmtree(os.path.join(sdir(sid), 'chunks'), ignore_errors=True)
            for name in ('plan.json', 'edits.json'):
                try:
                    os.remove(os.path.join(sdir(sid), name))
                except FileNotFoundError:
                    pass
        fields = {'status': 'queued', 'error': None, 'failed_chunks': []}
        if language in LANGUAGES:
            fields['language'] = language
        if restart:
            fields.update(done_chunks=0, total_chunks=0)
        meta = update_meta(sid, **fields)
    wake.set()
    return meta


# ---------- 音声の分割 ----------

def quietest_offset(f, start, end):
    """[start, end) の中で最も静かな0.2秒の中央のバイト位置を返す。"""
    f.seek(start)
    raw = f.read(end - start)
    raw = raw[:len(raw) - len(raw) % 2]
    samples = array.array('h')
    samples.frombytes(raw)
    frame = RATE // 5
    best_i, best_e = 0, None
    for i in range(0, max(len(samples) - frame, 1), frame):
        energy = sum(abs(s) for s in samples[i:i + frame:4])
        if best_e is None or energy < best_e:
            best_i, best_e = i, energy
    return start + (best_i + frame // 2) * 2


def plan_chunks(sid):
    path = os.path.join(sdir(sid), 'plan.json')
    plan = read_json(path)
    if plan is not None:
        return plan
    size = pcm_size(sid) & ~1
    target = CHUNK_SEC * BYTES_PER_SEC
    search = SEARCH_SEC * BYTES_PER_SEC
    plan, pos = [], 0
    with open(pcm_path(sid), 'rb') as f:
        while pos < size:
            if size - pos <= target + search:
                plan.append([pos, size])
                break
            cut = quietest_offset(f, pos + target - search, pos + target)
            plan.append([pos, cut])
            pos = cut
    write_json(path, plan)
    return plan


def write_wav_slice(src, start, end, dest):
    with open(src, 'rb') as f, wave.open(dest, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        f.seek(start)
        left = end - start
        while left > 0:
            buf = f.read(min(left, 1 << 20))
            if not buf:
                break
            w.writeframes(buf)
            left -= len(buf)


# ---------- 文字起こし ----------

def collapse_repeats(segments):
    """音声認識特有の同じ文の繰り返し(4回以上連続)を1つにまとめる。"""
    out, i = [], 0
    while i < len(segments):
        j = i
        while j + 1 < len(segments) and segments[j + 1]['text'] == segments[i]['text']:
            j += 1
        if j - i + 1 >= 4:
            out.append({**segments[i], 'end': segments[j]['end']})
        else:
            out.extend(segments[i:j + 1])
        i = j + 1
    return out


def run_whisper(sid, wav, stem, language, duration, use_gpu):
    cmd = [WHISPER, '-m', MODEL, '-f', wav, '-l', language, '-t', '4', '-mc', '0',
           '-oj', '-of', stem, '-np', '--vad', '--vad-model', VAD_MODEL]
    if not use_gpu:
        cmd.append('-ng')
    timeout = max(600, duration * (8 if use_gpu else 12))
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    current['proc'] = proc
    with open(PID_FILE, 'w') as f:
        f.write(str(proc.pid))
    try:
        _, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise RuntimeError(f'Stopped after {int(timeout)} seconds without finishing')
    finally:
        current['proc'] = None
    if proc.returncode != 0:
        tail = err.decode('utf-8', 'replace').strip()[-300:]
        raise RuntimeError(f'Exit code {proc.returncode}: {tail}')
    with open(stem + '.json', encoding='utf-8', errors='replace') as f:
        return json.load(f).get('transcription', [])


def transcribe_chunk(sid, index, start, end, language):
    """1区間を処理する。GPU2回→CPU1回の順に試す。全部失敗したら例外を投げる。"""
    work = os.path.join(sdir(sid), 'tmp')
    os.makedirs(work, exist_ok=True)
    wav = os.path.join(work, f'{index:04d}.wav')
    stem = os.path.join(work, f'{index:04d}')
    write_wav_slice(pcm_path(sid), start, end, wav)
    duration = (end - start) / BYTES_PER_SEC
    base = start / BYTES_PER_SEC
    last_error = None
    for use_gpu in (True, True, False):
        if current['sid'] != sid or load_meta(sid) is None or load_meta(sid)['status'] != 'transcribing':
            return None
        try:
            items = run_whisper(sid, wav, stem, language, duration, use_gpu)
            segments = []
            for item in items:
                text = item.get('text', '').strip()
                if text:
                    segments.append({'start': round(base + item['offsets']['from'] / 1000, 2),
                                     'end': round(base + item['offsets']['to'] / 1000, 2), 'text': text})
            return collapse_repeats(segments)
        except (RuntimeError, OSError, ValueError, KeyError) as e:
            last_error = str(e)
            print(f'[{sid[:8]}] part {index} failed (gpu={use_gpu}): {last_error}', flush=True)
    raise RuntimeError(last_error)


def copy_wav_data(src, dest):
    """WAVの音声部分だけを取り出す。afconvertは標準のwaveモジュールが読めない拡張形式を出すことがある。"""
    with open(src, 'rb') as f, open(dest, 'wb') as out:
        if f.read(4) != b'RIFF' or f.read(4) is None or f.read(4) != b'WAVE':
            raise RuntimeError('Could not read the converted audio')
        while True:
            head = f.read(8)
            if len(head) < 8:
                raise RuntimeError('The converted audio was empty')
            size = int.from_bytes(head[4:], 'little')
            if head[:4] == b'data':
                left = size
                while left > 0:
                    buf = f.read(min(left, 1 << 20))
                    if not buf:
                        break
                    out.write(buf)
                    left -= len(buf)
                return
            f.seek(size + size % 2, 1)


def convert_source(sid, meta):
    """取り込んだファイルをMac標準のafconvertで16kHzモノラルに変換する。"""
    src = os.path.join(sdir(sid), meta['source_file'])
    tmp = os.path.join(sdir(sid), 'converted.wav')
    result = subprocess.run(['/usr/bin/afconvert', '-f', 'WAVE', '-d', f'LEI16@{RATE}', '-c', '1', src, tmp],
                            capture_output=True, timeout=3600)
    if result.returncode != 0:
        raise RuntimeError("This file type can't be read. Use m4a, mp3, wav, aiff, aac or caf.")
    copy_wav_data(tmp, pcm_path(sid) + '.part')
    os.replace(pcm_path(sid) + '.part', pcm_path(sid))
    os.remove(tmp)
    os.remove(src)
    update_meta(sid, source_file=None)


def process(sid):
    meta = load_meta(sid)
    current['sid'] = sid
    if meta.get('source_file'):
        update_meta(sid, status='converting')
        convert_source(sid, meta)
    update_meta(sid, status='transcribing', started_at=time.time())
    plan = plan_chunks(sid)
    cdir = os.path.join(sdir(sid), 'chunks')
    os.makedirs(cdir, exist_ok=True)
    failed = []
    done = sum(os.path.exists(os.path.join(cdir, f'{i:04d}.json')) for i in range(len(plan)))
    resumed = done
    update_meta(sid, total_chunks=len(plan), done_chunks=done, resumed_chunks=resumed)
    for i, (start, end) in enumerate(plan):
        path = os.path.join(cdir, f'{i:04d}.json')
        if os.path.exists(path):
            continue
        if load_meta(sid) is None or load_meta(sid)['status'] != 'transcribing':
            return
        try:
            segments = transcribe_chunk(sid, i, start, end, load_meta(sid)['language'])
        except RuntimeError as e:
            failed.append({'index': i, 'start': start / BYTES_PER_SEC, 'end': end / BYTES_PER_SEC, 'error': str(e)})
            update_meta(sid, failed_chunks=failed)
            continue
        if segments is None:
            return
        write_json(path, segments)
        done += 1
        update_meta(sid, done_chunks=done)
    shutil.rmtree(os.path.join(sdir(sid), 'tmp'), ignore_errors=True)
    update_meta(sid, status='partial' if failed else 'done', finished_at=time.time())


def assemble(sid):
    plan = read_json(os.path.join(sdir(sid), 'plan.json'), [])
    edits = read_json(os.path.join(sdir(sid), 'edits.json'), {})
    out = []
    for i in range(len(plan)):
        for j, seg in enumerate(read_json(os.path.join(sdir(sid), 'chunks', f'{i:04d}.json'), [])):
            key = f'{i}:{j}'
            out.append({**seg, 'key': key, 'text': edits.get(key, seg['text'])})
    return out


def format_time(sec):
    sec = int(sec)
    return f'{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}'


def worker():
    # 前回の異常終了で残ったエンジンのプロセスを止める
    try:
        with open(PID_FILE) as f:
            pid = int(f.read())
        name = subprocess.run(['ps', '-p', str(pid), '-o', 'comm='], capture_output=True, text=True).stdout
        if 'whisper-cli' in name:
            os.kill(pid, 9)
    except (OSError, ValueError):
        pass
    while True:
        waiting = [m for m in reversed(list_sessions()) if m['status'] in ACTIVE]
        if not waiting:
            current['sid'] = None
            wake.wait(5)
            wake.clear()
            continue
        sid = waiting[0]['id']
        try:
            if not (os.path.exists(WHISPER) and os.path.exists(MODEL) and os.path.exists(VAD_MODEL)):
                raise RuntimeError("The speech engine isn't installed. Run setup.sh once.")
            process(sid)
        except Exception as e:  # 1件の失敗で全体を止めない
            print(f'[{sid[:8]}] error: {e}', flush=True)
            update_meta(sid, status='error', error=str(e))
        current['sid'] = None


def keep_awake():
    """録音中と文字起こし中だけ、Macのスリープを止める。"""
    proc = None
    while True:
        busy = any(m['status'] in ACTIVE or m['status'] == 'recording' for m in list_sessions())
        if busy and proc is None:
            proc = subprocess.Popen(['/usr/bin/caffeinate', '-i', '-s', '-w', str(os.getpid())])
        elif not busy and proc is not None:
            proc.terminate()
            proc.wait()
            proc = None
        time.sleep(5)


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def send_body(self, code, body, ctype='application/json; charset=utf-8', extra=None):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def send_obj(self, obj, code=200):
        self.send_body(code, json.dumps(obj, ensure_ascii=False).encode('utf-8'))

    def fail(self, code, message):
        self.close_connection = True
        self.send_obj({'error': message}, code)

    def body(self):
        self.body_read = True
        return self.rfile.read(int(self.headers.get('Content-Length') or 0))

    def body_json(self):
        try:
            return json.loads(self.body() or b'{}')
        except ValueError:
            return {}

    def allowed(self):
        """他のWebサイトからこのサーバーを操作されないよう、自分の画面からの要求だけ受ける。"""
        host = (self.headers.get('Host') or '').split(':')[0]
        origin = self.headers.get('Origin')
        if host not in ('localhost', '127.0.0.1'):
            return False
        return origin is None or re.match(r'^http://(localhost|127\.0\.0\.1):\d+$', origin) is not None

    def route(self):
        url = urlparse(self.path)
        return url.path.strip('/').split('/'), parse_qs(url.query)

    def dispatch(self, method):
        if not self.allowed():
            return self.fail(403, 'Forbidden')
        parts, query = self.route()
        self.body_read = False
        try:
            self.handle_route(method, parts, query)
            # 読まなかった本文が残ると、同じ接続の次の要求が壊れる
            if not self.body_read and not self.close_connection:
                self.body()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def do_GET(self):
        self.dispatch('GET')

    def do_HEAD(self):
        self.dispatch('GET')

    def do_POST(self):
        self.dispatch('POST')

    def do_PATCH(self):
        self.dispatch('PATCH')

    def do_DELETE(self):
        self.dispatch('DELETE')

    def handle_route(self, method, parts, query):
        if method == 'GET' and parts in ([''], ['index.html']):
            return self.send_file('index.html', 'text/html; charset=utf-8')
        if method == 'GET' and parts == ['worklet.js']:
            return self.send_file('worklet.js', 'text/javascript; charset=utf-8')
        if parts[0] != 'api':
            return self.fail(404, 'Not found')
        if parts[1:] == ['status'] and method == 'GET':
            ready = all(os.path.exists(p) for p in (WHISPER, MODEL, VAD_MODEL))
            free = shutil.disk_usage(ROOT).free
            return self.send_obj({'engine_ready': ready, 'free_bytes': free})
        if parts[1:] == ['upload'] and method == 'POST':
            return self.handle_upload(query)
        if parts[1:] == ['sessions']:
            if method == 'GET':
                return self.send_obj(list_sessions())
            if method == 'POST':
                data = self.body_json()
                language = data.get('language') if data.get('language') in LANGUAGES else 'ja'
                title = str(data.get('title') or '')[:200] or time.strftime('Recording %Y-%m-%d %H:%M')
                return self.send_obj(create_session(title, language, 'recording', 'recording'))
        if len(parts) >= 3 and parts[1] == 'sessions':
            return self.handle_session(method, parts[2], parts[3:], query)
        return self.fail(404, 'Not found')

    def handle_session(self, method, sid, rest, query):
        meta = load_meta(sid)
        if meta is None:
            return self.fail(404, 'Recording not found')
        if not rest:
            if method == 'GET':
                return self.send_obj({**public_meta(meta), 'segments': assemble(sid)})
            if method == 'PATCH':
                title = str(self.body_json().get('title') or '').strip()[:200]
                return self.send_obj(update_meta(sid, title=title or meta['title']))
            if method == 'DELETE':
                with lock:
                    update_meta(sid, status='deleted')
                    proc = current.get('proc')
                    if current.get('sid') == sid and proc:
                        proc.kill()
                    shutil.rmtree(sdir(sid), ignore_errors=True)
                return self.send_obj({'ok': True})
        action = rest[0] if rest else ''
        if action == 'chunk' and method == 'POST':
            try:
                offset = int(query.get('offset', [''])[0])
            except ValueError:
                return self.fail(400, 'Bad offset')
            return self.send_obj({'size': append_chunk(sid, offset, self.body())})
        if action == 'finish' and method == 'POST':
            return self.send_obj(public_meta(queue_transcription(sid)))
        if action == 'transcribe' and method == 'POST':
            data = self.body_json()
            if meta['status'] in ACTIVE and not data.get('restart'):
                return self.send_obj(public_meta(meta))
            return self.send_obj(public_meta(queue_transcription(sid, data.get('language'), bool(data.get('restart')))))
        if action == 'segments' and method == 'PATCH':
            data = self.body_json()
            with lock:
                path = os.path.join(sdir(sid), 'edits.json')
                edits = read_json(path, {})
                edits[str(data.get('key'))] = str(data.get('text', ''))
                write_json(path, edits)
            return self.send_obj({'ok': True})
        if action == 'audio.wav' and method == 'GET':
            return self.send_audio(sid)
        if action == 'transcript.txt' and method == 'GET':
            with_time = query.get('ts', ['0'])[0] == '1'
            lines = [(f'[{format_time(s["start"])}] ' if with_time else '') + s['text'] for s in assemble(sid)]
            body = ('\n'.join(lines) + '\n').encode('utf-8')
            return self.send_body(200, body, 'text/plain; charset=utf-8',
                                  {'Content-Disposition': 'attachment; filename="transcript.txt"'})
        return self.fail(404, 'Not found')

    def send_file(self, name, ctype):
        with open(os.path.join(PUBLIC, name), 'rb') as f:
            self.send_body(200, f.read(), ctype)

    def handle_upload(self, query):
        self.body_read = True
        name = os.path.basename(query.get('name', ['audio'])[0])
        language = query.get('language', ['ja'])[0]
        length = int(self.headers.get('Content-Length') or 0)
        if not length:
            return self.fail(400, 'The file is empty')
        title = os.path.splitext(name)[0][:200] or 'Imported audio'
        meta = create_session(title, language if language in LANGUAGES else 'ja', 'file', 'uploading')
        sid = meta['id']
        ext = re.sub(r'[^a-zA-Z0-9.]', '', os.path.splitext(name)[1])[:8]
        path = os.path.join(sdir(sid), 'source' + ext)
        left = length
        try:
            with open(path, 'wb') as f:
                while left > 0:
                    buf = self.rfile.read(min(left, 1 << 20))
                    if not buf:
                        raise ConnectionError
                    f.write(buf)
                    left -= len(buf)
        except (ConnectionError, OSError):
            shutil.rmtree(sdir(sid), ignore_errors=True)
            self.close_connection = True
            return None
        update_meta(sid, source_file=os.path.basename(path), status='queued')
        wake.set()
        return self.send_obj(public_meta(load_meta(sid)))

    def send_audio(self, sid):
        size = pcm_size(sid) & ~1
        header = wav_header(size)
        total = size + len(header)
        start, end, code = 0, total - 1, 200
        match = re.match(r'bytes=(\d*)-(\d*)$', self.headers.get('Range') or '')
        if match and (match.group(1) or match.group(2)):
            if match.group(1):
                start = int(match.group(1))
                end = min(int(match.group(2) or end), end)
            else:
                start = max(total - int(match.group(2)), 0)
            code = 206
            if start > end:
                self.send_response(416)
                self.send_header('Content-Range', f'bytes */{total}')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
        self.send_response(code)
        self.send_header('Content-Type', 'audio/wav')
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(end - start + 1))
        if code == 206:
            self.send_header('Content-Range', f'bytes {start}-{end}/{total}')
        self.end_headers()
        if self.command == 'HEAD':
            return
        if start < len(header):
            self.wfile.write(header[start:end + 1])
        pos = max(start - len(header), 0)
        left = end + 1 - max(start, len(header))
        with open(pcm_path(sid), 'rb') as f:
            f.seek(pos)
            while left > 0:
                buf = f.read(min(left, 1 << 18))
                if not buf:
                    break
                self.wfile.write(buf)
                left -= len(buf)


def wav_header(size):
    le32 = lambda n: n.to_bytes(4, 'little')
    le16 = lambda n: n.to_bytes(2, 'little')
    return (b'RIFF' + le32(36 + size) + b'WAVEfmt ' + le32(16) + le16(1) + le16(1) + le32(RATE)
            + le32(BYTES_PER_SEC) + le16(2) + le16(16) + b'data' + le32(size))


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64


def main():
    os.makedirs(SESSIONS, exist_ok=True)
    for meta in list_sessions():  # 取り込みの途中で落ちたものは、続きを受け取れないのでエラーにする
        if meta['status'] == 'uploading':
            update_meta(meta['id'], status='error', error="The import didn't finish. Choose the file again.")
    server = Server(('127.0.0.1', PORT), Handler)  # このMacの中からだけ接続できる
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=keep_awake, daemon=True).start()
    print(f'Transcribe: http://localhost:{PORT}/', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
