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

話者(2026-11-21):
  - mode 'meeting': マイク(audio.pcm)=Me、Mac の音(them.pcm、TatsukiAudioTap が録る)=Them。トラックごとに文字にして時刻で並べる。
  - mode 'inperson': マイク1本を、文字にしたあと sherpa-onnx で2人(A / B)に分ける。取り込んだ録音にも後からかけられる。
  - 名前は meta の speakers、行ごとの直しは speakers.json。
"""
import array
import difflib
import json
import os
import re
import shlex
import shutil
import signal
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
DIAR = os.path.join(ENGINE, 'diar')
DIAR_BIN = os.path.join(DIAR, 'bin', 'sherpa-onnx-offline-speaker-diarization')
DIAR_SEG = os.path.join(DIAR, 'segmentation.onnx')
DIAR_EMB = os.path.join(DIAR, 'embedding.onnx')
# 会議の相手の声を録るアプリ。試験では TRANSCRIBE_TAP_CMD に偽物のコマンドを入れる
TAP_APP = os.environ.get('TRANSCRIBE_TAP_APP') or os.path.expanduser('~/Applications/TatsukiAudioTap.app')
TAP_CMD = os.environ.get('TRANSCRIBE_TAP_CMD')
MODES = ('mic', 'meeting', 'inperson')
DEFAULT_SPEAKERS = {'meeting': {'me': 'Me', 'them': 'Them'}, 'inperson': {'A': 'Speaker A', 'B': 'Speaker B'}}

RATE = 16000
BYTES_PER_SEC = RATE * 2
CHUNK_SEC = 300          # 1区間の長さの目安
SEARCH_SEC = 60          # 区間の終わり何秒の中から最も静かな位置を探すか
RECORDING_STALE_SEC = 30 # これだけ音声が届かなければ録音は中断扱い
LANGUAGES = {'ja', 'en', 'vi', 'zh', 'ko', 'auto'}
ACTIVE = ('queued', 'converting', 'transcribing', 'splitting')
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


def file_size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def pcm_size(sid):
    return file_size(pcm_path(sid))


def mix_path(sid):
    return os.path.join(sdir(sid), 'mix.pcm')


def play_path(sid):
    """再生する音。会議は2本を重ねた mix.pcm ができていればそれ、なければマイク。"""
    return mix_path(sid) if os.path.exists(mix_path(sid)) else pcm_path(sid)


def tracks(meta):
    return ['me', 'them'] if meta.get('mode') == 'meeting' else ['me']


def track_pcm(sid, track):
    return pcm_path(sid) if track == 'me' else os.path.join(sdir(sid), 'them.pcm')


def track_suffix(track):
    return '' if track == 'me' else '-them'


def track_offset(meta, track):
    """Them の音がマイクより何秒遅れて始まったか。行の時刻をマイクの時間に合わせる。"""
    if track == 'me':
        return 0.0
    return float(meta.get('them_offset') or 0.0)


def speakers_of(meta):
    names = dict(DEFAULT_SPEAKERS.get(meta.get('mode'), {}))
    names.update({k: v for k, v in (meta.get('speakers') or {}).items() if k in names})
    return names


def public_meta(meta):
    """録音が途絶えているものは「中断」として見せる。保存値は変えない。"""
    meta = dict(meta)
    sid = meta['id']
    meta['duration'] = file_size(play_path(sid)) / BYTES_PER_SEC
    meta['mode'] = meta.get('mode') or 'mic'
    meta['speakers'] = speakers_of(meta)
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


def create_session(title, language, source, status, mode='mic'):
    sid = uuid.uuid4().hex
    os.makedirs(sdir(sid))
    now = time.time()
    meta = {'id': sid, 'title': title, 'language': language, 'source': source, 'status': status,
            'created': now, 'last_chunk': now, 'done_chunks': 0, 'total_chunks': 0, 'failed_chunks': [],
            'error': None, 'mode': mode if mode in MODES else 'mic'}
    write_json(os.path.join(sdir(sid), 'meta.json'), meta)
    open(pcm_path(sid), 'ab').close()
    if meta['mode'] == 'meeting':
        start_tap(sid)
    return meta


# ---------- Mac の音(会議の相手の声) ----------
# TatsukiAudioTap は open で起動する(画面収録の許可を Python ではなくそのアプリ自身に付けるため)。
# alive を5秒ごとに触り続け、録音画面が消えたらアプリは60秒で自分で止まる。

def touch(path):
    with open(path, 'a'):
        os.utime(path, None)


def start_tap(sid):
    folder = sdir(sid)
    touch(os.path.join(folder, 'alive'))
    try:
        os.remove(os.path.join(folder, 'stop'))
    except FileNotFoundError:
        pass
    try:
        if TAP_CMD:
            subprocess.Popen(shlex.split(TAP_CMD) + ['record', folder], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        elif os.path.exists(TAP_APP):
            subprocess.run(['/usr/bin/open', '-n', '-g', '-a', TAP_APP, '--args', 'record', folder],
                           capture_output=True, timeout=15)
        else:
            update_meta(sid, tap_error='missing')
            return
        update_meta(sid, tap_started_at=time.time(), tap_error=None)
    except (OSError, subprocess.SubprocessError) as e:
        update_meta(sid, tap_error=str(e)[:200])


def tap_state(sid):
    """アプリが書く tap.json。5秒以上更新がなければ止まったものとして見せる。"""
    tap = read_json(os.path.join(sdir(sid), 'tap.json'), None)
    meta = load_meta(sid) or {}
    if tap is None:
        if meta.get('tap_error') == 'missing':
            return {'state': 'error', 'error': 'missing'}
        return {'state': 'starting'}
    if tap.get('state') in ('starting', 'recording') and time.time() - tap.get('updated', 0) > 5:
        tap = {**tap, 'state': 'lost'}
    return tap


def stop_tap(sid, wait=True):
    folder = sdir(sid)
    if not os.path.isdir(folder):
        return
    touch(os.path.join(folder, 'stop'))
    deadline = time.time() + (8 if wait else 0)
    while time.time() < deadline:
        tap = read_json(os.path.join(folder, 'tap.json'), None)
        if tap is None or tap.get('state') in ('stopped', 'error') or time.time() - tap.get('updated', 0) > 5:
            return
        time.sleep(0.3)
    tap = read_json(os.path.join(folder, 'tap.json'), {}) or {}
    pid = tap.get('pid')
    if wait and pid:  # 応えないときだけ、名前を確かめてから止める
        name = subprocess.run(['ps', '-p', str(pid), '-o', 'comm='], capture_output=True, text=True).stdout
        if 'TatsukiAudioTap' in name or (TAP_CMD and name.strip()):
            try:
                os.kill(int(pid), signal.SIGTERM)
            except OSError:
                pass


def tend_taps():
    """録音中の会議のアプリを生かし続け、落ちていたら立ち上げ直す。keep_awake から5秒ごとに呼ぶ。"""
    now = time.time()
    for meta in list_sessions():
        if meta.get('mode') != 'meeting' or meta['status'] != 'recording':
            continue
        sid = meta['id']
        touch(os.path.join(sdir(sid), 'alive'))
        tap = tap_state(sid)
        if tap.get('state') == 'lost' and now - meta.get('tap_started_at', 0) > 30:
            print(f'[{sid[:8]}] meeting audio stopped; starting it again', flush=True)
            start_tap(sid)


def mix_tracks(sid, meta):
    """マイクと Mac の音を重ねて1本にする(再生と、Them が無いときの確認用)。文字起こしはトラックごとに行う。"""
    them = track_pcm(sid, 'them')
    if not file_size(them):
        return
    try:
        import audioop  # Python 3.12 まで。無ければ遅いが同じ計算をする
    except ImportError:
        audioop = None
    off = int(round(track_offset(meta, 'them') * RATE)) * 2
    tmp = mix_path(sid) + '.part'
    step = 1 << 20
    with open(pcm_path(sid), 'rb') as me_f, open(them, 'rb') as th_f, open(tmp, 'wb') as out:
        if off < 0:
            th_f.seek(-off)
            off = 0
        pos = 0
        while True:
            a = me_f.read(step)
            b = b''
            if pos + step > off:
                if pos < off:  # Them が始まる前の部分
                    gap = off - pos
                    b = b'\0' * gap + th_f.read(step - gap)
                else:
                    b = th_f.read(step)
            if not a and not b:
                break
            n = max(len(a), len(b)) & ~1
            a, b = a.ljust(n, b'\0')[:n], b.ljust(n, b'\0')[:n]
            if audioop:
                out.write(audioop.add(a, b, 2))
            else:
                x, y = array.array('h'), array.array('h')
                x.frombytes(a)
                y.frombytes(b)
                out.write(array.array('h', (max(-32768, min(32767, p + q)) for p, q in zip(x, y))).tobytes())
            pos += n
    os.replace(tmp, mix_path(sid))


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
            for track in ('me', 'them'):
                shutil.rmtree(os.path.join(sdir(sid), 'chunks' + track_suffix(track)), ignore_errors=True)
            for name in ('plan.json', 'plan-them.json', 'edits.json', 'speakers.json', 'diar.json'):
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


def plan_chunks(sid, track='me'):
    path = os.path.join(sdir(sid), f'plan{track_suffix(track)}.json')
    plan = read_json(path)
    if plan is not None:
        return plan
    src = track_pcm(sid, track)
    size = file_size(src) & ~1
    target = CHUNK_SEC * BYTES_PER_SEC
    search = SEARCH_SEC * BYTES_PER_SEC
    plan, pos = [], 0
    if not size:
        write_json(path, plan)
        return plan
    with open(src, 'rb') as f:
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


def transcribe_chunk(sid, index, start, end, language, track='me', offset=0.0):
    """1区間を処理する。GPU2回→CPU1回の順に試す。全部失敗したら例外を投げる。"""
    work = os.path.join(sdir(sid), 'tmp')
    os.makedirs(work, exist_ok=True)
    name = f'{track}-{index:04d}'
    wav = os.path.join(work, f'{name}.wav')
    stem = os.path.join(work, name)
    write_wav_slice(track_pcm(sid, track), start, end, wav)
    duration = (end - start) / BYTES_PER_SEC
    base = start / BYTES_PER_SEC + offset
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


def prepare_meeting(sid, meta):
    """録音が終わった会議：Mac の音のアプリを止め、2本の時刻のずれを決め、再生用に重ねる。"""
    stop_tap(sid)
    tap = read_json(os.path.join(sdir(sid), 'tap.json'), {}) or {}
    fields = {}
    if 'them_offset' not in meta:
        mic, them = meta.get('mic_start'), tap.get('started')
        fields['them_offset'] = round(them - mic, 3) if mic and them else 0.0
    if tap.get('apps'):
        fields['tap_apps'] = tap['apps']
    if fields:
        meta = update_meta(sid, **fields)
    if not os.path.exists(mix_path(sid)):
        mix_tracks(sid, meta)
    return meta


def process(sid):
    meta = load_meta(sid)
    current['sid'] = sid
    if meta.get('source_file'):
        update_meta(sid, status='converting')
        convert_source(sid, meta)
    if meta.get('mode') == 'meeting':
        meta = prepare_meeting(sid, meta)
    update_meta(sid, status='transcribing', started_at=time.time())
    plans = {t: plan_chunks(sid, t) for t in tracks(meta)}
    failed = []
    done = 0
    for t, plan in plans.items():
        cdir = os.path.join(sdir(sid), 'chunks' + track_suffix(t))
        os.makedirs(cdir, exist_ok=True)
        done += sum(os.path.exists(os.path.join(cdir, f'{i:04d}.json')) for i in range(len(plan)))
    update_meta(sid, total_chunks=sum(len(p) for p in plans.values()), done_chunks=done, resumed_chunks=done)
    for t, plan in plans.items():
        cdir = os.path.join(sdir(sid), 'chunks' + track_suffix(t))
        for i, (start, end) in enumerate(plan):
            path = os.path.join(cdir, f'{i:04d}.json')
            if os.path.exists(path):
                continue
            if load_meta(sid) is None or load_meta(sid)['status'] != 'transcribing':
                return
            off = track_offset(meta, t)
            try:
                segments = transcribe_chunk(sid, i, start, end, load_meta(sid)['language'], t, off)
            except RuntimeError as e:
                failed.append({'index': i, 'track': t, 'start': start / BYTES_PER_SEC + off,
                               'end': end / BYTES_PER_SEC + off, 'error': str(e)})
                update_meta(sid, failed_chunks=failed)
                continue
            if segments is None:
                return
            write_json(path, segments)
            done += 1
            update_meta(sid, done_chunks=done)
    shutil.rmtree(os.path.join(sdir(sid), 'tmp'), ignore_errors=True)
    if load_meta(sid).get('mode') == 'inperson' and not os.path.exists(os.path.join(sdir(sid), 'diar.json')):
        update_meta(sid, status='splitting')
        try:
            write_json(os.path.join(sdir(sid), 'diar.json'), diarize(sid))
            update_meta(sid, diar_error=None)
        except RuntimeError as e:
            print(f'[{sid[:8]}] speaker split failed: {e}', flush=True)
            update_meta(sid, diar_error=str(e)[:300])
        if load_meta(sid) is None:
            return
    update_meta(sid, status='partial' if failed else 'done', finished_at=time.time())


def diar_ready():
    return all(os.path.exists(p) for p in (DIAR_BIN, DIAR_SEG, DIAR_EMB))


DIAR_LINE = re.compile(r'^\s*([\d.]+)\s*--\s*([\d.]+)\s+speaker_(\d+)\s*$')


def diarize(sid):
    """マイク1本の録音を2人に分ける。[[start, end, 'A'|'B'], ...] を返す。先に話した人が A。"""
    if not diar_ready():
        raise RuntimeError("The speaker engine isn't installed. Run setup.sh once.")
    work = os.path.join(sdir(sid), 'tmp')
    os.makedirs(work, exist_ok=True)
    wav = os.path.join(work, 'diar.wav')
    size = pcm_size(sid) & ~1
    write_wav_slice(pcm_path(sid), 0, size, wav)
    duration = size / BYTES_PER_SEC
    cmd = [DIAR_BIN, '--clustering.num-clusters=2', f'--segmentation.pyannote-model={DIAR_SEG}',
           f'--embedding.model={DIAR_EMB}', '--segmentation.num-threads=4', '--embedding.num-threads=4', wav]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    current['proc'] = proc
    try:
        out, _ = proc.communicate(timeout=max(600, duration))
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise RuntimeError('The speaker split took too long')
    finally:
        current['proc'] = None
        shutil.rmtree(work, ignore_errors=True)
    if proc.returncode != 0:
        raise RuntimeError(f'Exit code {proc.returncode}: {out.decode("utf-8", "replace").strip()[-300:]}')
    turns, names = [], {}
    for line in out.decode('utf-8', 'replace').splitlines():
        m = DIAR_LINE.match(line)
        if m:
            label = names.setdefault(m.group(3), 'AB'[min(len(names), 1)])
            turns.append([float(m.group(1)), float(m.group(2)), label])
    return turns


def speaker_for(seg, turns):
    """その行と最も長く重なる話者。重ならなければ最も近い話者。"""
    best, best_overlap, nearest, nearest_gap = None, 0.0, None, None
    for start, end, label in turns:
        overlap = min(seg['end'], end) - max(seg['start'], start)
        if overlap > best_overlap:
            best, best_overlap = label, overlap
        gap = max(start - seg['end'], seg['start'] - end, 0)
        if nearest_gap is None or gap < nearest_gap:
            nearest, nearest_gap = label, gap
    return best or nearest


def norm_text(text):
    return re.sub(r'[\s、。,.!?！？「」"\'-]+', '', text).lower()


def drop_echo(me, them):
    """スピーカーで聞くと相手の声がマイクにも入る。Them とほぼ同じ時刻・同じ文の Me の行は消す。"""
    out = []
    for seg in me:
        near = ''.join(t['text'] for t in them if t['start'] < seg['end'] + 1.5 and t['end'] > seg['start'] - 1.5)
        a, b = norm_text(seg['text']), norm_text(near)
        if a and b and (a in b or difflib.SequenceMatcher(None, a, b).ratio() > 0.6):
            continue
        out.append(seg)
    return out


def assemble(sid):
    meta = load_meta(sid) or {}
    edits = read_json(os.path.join(sdir(sid), 'edits.json'), {})
    by_track = {}
    for t in tracks(meta):
        plan = read_json(os.path.join(sdir(sid), f'plan{track_suffix(t)}.json'), [])
        prefix = '' if t == 'me' else 't'
        rows = []
        for i in range(len(plan)):
            for j, seg in enumerate(read_json(os.path.join(sdir(sid), 'chunks' + track_suffix(t), f'{i:04d}.json'), [])):
                key = f'{prefix}{i}:{j}'
                rows.append({**seg, 'key': key, 'text': edits.get(key, seg['text'])})
        by_track[t] = rows
    mode = meta.get('mode')
    if mode == 'meeting':
        me = [{**s, 'spk': 'me'} for s in drop_echo(by_track['me'], by_track.get('them', []))]
        out = sorted(me + [{**s, 'spk': 'them'} for s in by_track.get('them', [])], key=lambda s: (s['start'], s['end']))
    else:
        out = by_track['me']
        turns = read_json(os.path.join(sdir(sid), 'diar.json'), None) if mode == 'inperson' else None
        if turns:
            out = [{**s, 'spk': speaker_for(s, turns) or 'A'} for s in out]
    fixes = read_json(os.path.join(sdir(sid), 'speakers.json'), {}) if mode in DEFAULT_SPEAKERS else {}
    for s in out:
        if s['key'] in fixes:
            s['spk'] = fixes[s['key']]
    return out


def transcript_lines(sid, with_time):
    """書き出しの行。話者があるときは「名前: 発言」で、同じ人が続く行は1つにまとめる。"""
    meta = load_meta(sid) or {}
    names = speakers_of(meta)
    rows = []
    for s in assemble(sid):
        spk = s.get('spk')
        if spk and rows and rows[-1][1] == spk:
            rows[-1][2].append(s['text'])
        else:
            rows.append([s['start'], spk, [s['text']]])
    sep = '' if meta.get('language') in ('ja', 'zh') else ' '
    out = []
    for start, spk, texts in rows:
        head = f'[{format_time(start)}] ' if with_time else ''
        who = f'{names.get(spk, spk)}: ' if spk else ''
        out.append(head + who + sep.join(texts))
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
        try:
            tend_taps()
        except OSError as e:
            print(f'meeting audio check failed: {e}', flush=True)
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
            return self.send_obj({'engine_ready': ready, 'free_bytes': free, 'diar_ready': diar_ready(),
                                  'tap_ready': bool(TAP_CMD) or os.path.exists(TAP_APP)})
        if parts[1:] == ['upload'] and method == 'POST':
            return self.handle_upload(query)
        if parts[1:] == ['sessions']:
            if method == 'GET':
                return self.send_obj(list_sessions())
            if method == 'POST':
                data = self.body_json()
                language = data.get('language') if data.get('language') in LANGUAGES else 'ja'
                mode = data.get('mode') if data.get('mode') in MODES else 'mic'
                default = {'meeting': 'Meeting %Y-%m-%d %H:%M', 'inperson': 'Conversation %Y-%m-%d %H:%M'}
                title = str(data.get('title') or '')[:200] or time.strftime(default.get(mode, 'Recording %Y-%m-%d %H:%M'))
                return self.send_obj(public_meta(create_session(title, language, 'recording', 'recording', mode)))
        if len(parts) >= 3 and parts[1] == 'sessions':
            return self.handle_session(method, parts[2], parts[3:], query)
        return self.fail(404, 'Not found')

    def handle_session(self, method, sid, rest, query):
        meta = load_meta(sid)
        if meta is None:
            return self.fail(404, 'Recording not found')
        if not rest:
            if method == 'GET':
                extra = {'tap': tap_state(sid)} if meta.get('mode') == 'meeting' else {}
                return self.send_obj({**public_meta(meta), **extra, 'segments': assemble(sid)})
            if method == 'PATCH':
                data = self.body_json()
                fields = {}
                if 'title' in data:
                    fields['title'] = str(data.get('title') or '').strip()[:200] or meta['title']
                if isinstance(data.get('speakers'), dict):
                    allowed = DEFAULT_SPEAKERS.get(meta.get('mode'), {})
                    names = {k: str(v).strip()[:40] or allowed[k] for k, v in data['speakers'].items() if k in allowed}
                    fields['speakers'] = {**(meta.get('speakers') or {}), **names}
                return self.send_obj(public_meta(update_meta(sid, **fields) if fields else meta))
            if method == 'DELETE':
                stop_tap(sid, wait=False)
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
        if action == 'clock' and method == 'POST':
            # 録音画面が、マイクの最初の音の時刻を知らせる(Them とのずれの計算に使う)
            try:
                mic_start = float(self.body_json().get('mic_start'))
            except (TypeError, ValueError):
                return self.fail(400, 'Bad time')
            if not meta.get('mic_start'):
                update_meta(sid, mic_start=mic_start)
            return self.send_obj({'ok': True})
        if action == 'tap' and method == 'GET':
            return self.send_obj(tap_state(sid))
        if action == 'finish' and method == 'POST':
            if meta.get('mode') == 'meeting':
                stop_tap(sid, wait=False)
            return self.send_obj(public_meta(queue_transcription(sid)))
        if action == 'split' and method == 'POST':
            # 文字にした録音を、あとから2人に分ける
            if meta['status'] in ACTIVE or meta['status'] == 'recording' or meta.get('mode') == 'meeting':
                return self.fail(409, "This recording can't be split now")
            with lock:
                for name in ('diar.json', 'speakers.json'):
                    try:
                        os.remove(os.path.join(sdir(sid), name))
                    except FileNotFoundError:
                        pass
                update_meta(sid, mode='inperson', status='queued', diar_error=None)
            wake.set()
            return self.send_obj(public_meta(load_meta(sid)))
        if action == 'transcribe' and method == 'POST':
            data = self.body_json()
            if meta['status'] in ACTIVE and not data.get('restart'):
                return self.send_obj(public_meta(meta))
            return self.send_obj(public_meta(queue_transcription(sid, data.get('language'), bool(data.get('restart')))))
        if action == 'segments' and method == 'PATCH':
            data = self.body_json()
            with lock:
                path = os.path.join(sdir(sid), 'edits.json')
                if 'spk' in data:  # この行の話者だけ直す
                    if data['spk'] not in DEFAULT_SPEAKERS.get(meta.get('mode'), {}):
                        return self.fail(400, 'Unknown speaker')
                    path = os.path.join(sdir(sid), 'speakers.json')
                    fixes = read_json(path, {})
                    fixes[str(data.get('key'))] = data['spk']
                    write_json(path, fixes)
                    return self.send_obj({'ok': True})
                edits = read_json(path, {})
                edits[str(data.get('key'))] = str(data.get('text', ''))
                write_json(path, edits)
            return self.send_obj({'ok': True})
        if action == 'audio.wav' and method == 'GET':
            return self.send_audio(sid)
        if action == 'transcript.txt' and method == 'GET':
            with_time = query.get('ts', ['0'])[0] == '1'
            lines = transcript_lines(sid, with_time)
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
        mode = 'inperson' if query.get('mode', [''])[0] == 'inperson' else 'mic'
        meta = create_session(title, language if language in LANGUAGES else 'ja', 'file', 'uploading', mode)
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
        src = play_path(sid)
        size = file_size(src) & ~1
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
        with open(src, 'rb') as f:
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
