#!/usr/bin/env python3
"""The real bot: long polling against Telegram.  python3 lifeos/bot/run.py

Without a token it prints what to do and exits; it never starts half-configured.
Without a chat id it does no work and only tells whoever writes their chat id, so that the
owner can set it. Once set, messages from every other chat are ignored.
"""
import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "desk"))
from lifeos.bot import router, telegram, voice  # noqa: E402
from lifeos.lib import logs, paths  # noqa: E402

NO_TOKEN = """Telegram のトークンがないので起動しません。
1. Telegram の BotFather でボットを作り、トークンを受け取る
2. キーチェーンに入れる：security add-generic-password -a telegram -s tatsuki-desk-telegram -w
3. もう一度起動し、ボットに何か送ると、チャット ID を返します
4. 登録する：python3 lifeos/bot/run.py set-chat <チャット ID>
トークンなしで試すには：python3 lifeos/bot/cli.py "テキスト\""""
NOT_TEXT = "文字と音声と写真を受け取れます。"
PHOTO_TYPES = ("image/", "application/pdf")


def ask_for_chat_id(chat_id):
    return f"このチャットの ID は {chat_id} です。Mac で python3 lifeos/bot/run.py set-chat {chat_id} を実行すると使えるようになります。"


def photo_file(msg):
    """The file id of a photo or image file in a message, or None. Telegram lists a photo's sizes from small to large."""
    photos = msg.get("photo")
    if photos:
        return photos[-1].get("file_id")
    doc = msg.get("document") or {}
    if str(doc.get("mime_type") or "").startswith(PHOTO_TYPES):
        return doc.get("file_id")
    return None


def handle_photo(file_id, caption, client, chat):
    """A photo goes to the same queue as the Drive folder, and is read right away when it can be."""
    import lifelog  # desk/lifelog.py
    with tempfile.TemporaryDirectory() as d:
        path = client.download(file_id, os.path.join(d, "photo"))
        try:
            _, new = lifelog.add(path, "telegram", note=caption or "")
        except lifelog.Refused as e:
            client.send_message(chat, str(e))
            return
    if not new:
        client.send_message(chat, "この写真は取り込み済みです")
        return
    client.send_message(chat, lifelog.reply_text(lifelog.process()))


def handle_update(update, client, owner, routes=None):
    """Handles one update from Telegram. client needs send_message and download."""
    msg = update.get("message") or {}
    chat = (msg.get("chat") or {}).get("id")
    if chat is None:
        return
    if owner is None:
        client.send_message(chat, ask_for_chat_id(chat))
        return
    if str(chat) != str(owner):
        return
    photo = photo_file(msg)
    if photo:
        handle_photo(photo, msg.get("caption"), client, chat)
        return
    ctx = router.Ctx(source="telegram", chat_id=chat, send=lambda t: client.send_message(chat, t))
    audio = msg.get("voice") or msg.get("audio")
    if audio:
        if not voice.connected():
            client.send_message(chat, voice.NOT_CONNECTED)
            return
        with tempfile.TemporaryDirectory() as d:
            client.download(audio["file_id"], os.path.join(d, "voice"))
            try:
                text = voice.transcribe(os.path.join(d, "voice"))
            except Exception as e:
                logs.append("bot-errors", {"where": "transcribe", "error": type(e).__name__})
                shutil.copy(os.path.join(d, "voice"), paths.data_path("inbox", "audio", str(update.get("update_id"))))
                client.send_message(chat, "文字起こしに失敗しました。音声は inbox/audio/ に残しました。")
                return
        ctx.is_voice = True
    else:
        text = msg.get("text") or msg.get("caption")
        if not text:
            client.send_message(chat, NOT_TEXT)
            return
    reply = router.dispatch(text, ctx, routes)
    if reply:
        client.send_message(chat, reply)


def read_offset():
    try:
        with open(paths.data_path("bot", "offset.json"), encoding="utf-8") as f:
            return json.load(f).get("offset")
    except (OSError, ValueError):
        return None


def save_offset(offset):
    with open(paths.data_path("bot", "offset.json"), "w", encoding="utf-8") as f:
        json.dump({"offset": offset}, f)


def poll_once(client, owner, offset, routes=None):
    """One getUpdates round. Returns the next offset."""
    for u in client.get_updates(offset):
        try:
            handle_update(u, client, owner, routes)
        except Exception as e:  # one bad message must not stop the bot or repeat forever
            logs.append("bot-errors", {"where": "update", "error": type(e).__name__})
        offset = u["update_id"] + 1
        save_offset(offset)
    return offset


def main():
    if sys.argv[1:2] == ["set-chat"]:
        if len(sys.argv) != 3 or not sys.argv[2].lstrip("-").isdigit():
            print("使い方：python3 lifeos/bot/run.py set-chat <チャット ID>")
            return 2
        telegram.set_chat_id(sys.argv[2])
        try:  # the running bot reads the id at start. launchd may hold the restart for its throttle, so do not wait for it
            subprocess.run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/com.tatsuki.desk.telegram"], capture_output=True, timeout=5)
        except subprocess.TimeoutExpired:
            pass
        print("チャット ID を登録しました。")
        return 0
    token, owner = telegram.config()
    if not token:
        print(NO_TOKEN.format(path=paths.data_path("secrets", "telegram.json", mkdir=False)))
        return 2
    lock = open(paths.data_path("bot", "run.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("ボットはすでに動いています。")
        return 1
    client = telegram.Client(token)
    found = router.discover()
    for slug, err in found.errors:
        print(f"読み込めません  {slug}  {err}", file=sys.stderr)
    print(f"ボットを起動しました（ツール {len(found.routes)} 個）" + ("" if owner else "。chat_id が未設定です"), flush=True)
    offset, wait = read_offset(), 5
    while True:
        try:
            offset = poll_once(client, owner, offset, found.routes)
            wait = 5
        except telegram.TelegramError as e:
            print(f"Telegram エラー: {e}。{wait}秒後にやり直します", file=sys.stderr, flush=True)
            time.sleep(wait)
            wait = min(wait * 2, 300)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        pass
