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
import threading
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "desk"))
from lifeos.bot import router, telegram, voice  # noqa: E402
from lifeos.lib import logs, paths  # noqa: E402

NO_TOKEN = """No Telegram token, so the bot is not starting.
1. Create a bot with BotFather in Telegram and get its token
2. Add it to the keychain: security add-generic-password -a telegram -s tatsuki-desk-telegram -w
3. Start again and send the bot anything; it replies with the chat ID
4. Register it: python3 lifeos/bot/run.py set-chat <chat ID>
To try it without a token: python3 lifeos/bot/cli.py "text\""""
TRANSCRIPT = "Transcript:\n"
NOT_TEXT = "This bot takes text, voice and photos."
PHOTO_ELSEWHERE = "Please send photos to the records bot."
TO_SECRETARY = "This was not taken as a record. It is kept in Unsorted. Please send plans and tasks to the assistant bot."
OFFSET_FILES = {telegram.RECORDS: "offset.json", telegram.SECRETARY: "offset-secretary.json"}
PHOTO_TYPES = ("image/", "application/pdf")


def ask_for_chat_id(chat_id):
    return f"This chat's ID is {chat_id}. Run python3 lifeos/bot/run.py set-chat {chat_id} on the Mac to start using it."


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
        client.send_message(chat, "This photo is already in")
        return
    client.send_message(chat, lifelog.reply_text(lifelog.process()))


def handle_update(update, client, owner, routes=None, photos=True, unrouted=None):
    """Handles one update from Telegram. client needs send_message and download.

    photos: whether this bot takes photos. unrouted: its reply, if any, when none of its tools wants the message.
    """
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
        if photos:
            handle_photo(photo, msg.get("caption"), client, chat)
        else:
            client.send_message(chat, PHOTO_ELSEWHERE)
        return
    ctx = router.Ctx(source="telegram", chat_id=chat, send=lambda t: client.send_message(chat, t), routes=routes)
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
                client.send_message(chat, "Could not transcribe. The audio is kept in inbox/audio/.")
                return
        ctx.is_voice = True
    else:
        text = msg.get("text") or msg.get("caption")
        if not text:
            client.send_message(chat, NOT_TEXT)
            return
    if ctx.is_voice and (text or "").strip():  # the words as heard come first, whichever tool takes them
        client.send_message(chat, TRANSCRIPT + text.strip())
    reply = router.dispatch(text, ctx, routes)
    if reply == router.UNROUTED and unrouted:
        reply = unrouted
    if reply:
        client.send_message(chat, reply)


def read_offset(bot=telegram.RECORDS):
    try:
        with open(paths.data_path("bot", OFFSET_FILES[bot]), encoding="utf-8") as f:
            return json.load(f).get("offset")
    except (OSError, ValueError):
        return None


def save_offset(offset, bot=telegram.RECORDS):
    with open(paths.data_path("bot", OFFSET_FILES[bot]), "w", encoding="utf-8") as f:
        json.dump({"offset": offset}, f)


def poll_once(client, owner, offset, routes=None, bot=telegram.RECORDS, photos=True, unrouted=None):
    """One getUpdates round. Returns the next offset."""
    for u in client.get_updates(offset):
        try:
            handle_update(u, client, owner, routes, photos, unrouted)
        except Exception as e:  # one bad message must not stop the bot or repeat forever
            logs.append("bot-errors", {"where": "update", "error": type(e).__name__})
        offset = u["update_id"] + 1
        save_offset(offset, bot)
    return offset


def serve(bot, token, owner, routes, photos=True, unrouted=None):
    """Polls one bot for good. One bot's trouble never stops the other."""
    client = telegram.Client(token)
    offset, wait = read_offset(bot), 5
    while True:
        try:
            offset = poll_once(client, owner, offset, routes, bot, photos, unrouted)
            wait = 5
        except telegram.TelegramError as e:
            print(f"Telegram error ({bot}): {e}. Retrying in {wait} seconds", file=sys.stderr, flush=True)
            time.sleep(wait)
            wait = min(wait * 2, 300)
        except Exception as e:
            logs.append("bot-errors", {"where": "serve", "bot": bot, "error": type(e).__name__})
            time.sleep(wait)
            wait = min(wait * 2, 300)


def plan_bots(token, secretary_token, routes):
    """[(bot, token, routes, photos, unrouted)]. Without a secretary token one bot serves every tool, as before."""
    if not secretary_token or secretary_token == token:
        return [(telegram.RECORDS, token, routes, True, None)]
    return [(telegram.RECORDS, token, router.for_bot(routes, telegram.RECORDS), True, TO_SECRETARY),
            (telegram.SECRETARY, secretary_token, router.for_bot(routes, telegram.SECRETARY), False, None)]


def main():
    if sys.argv[1:2] == ["set-chat"]:
        if len(sys.argv) != 3 or not sys.argv[2].lstrip("-").isdigit():
            print("Usage: python3 lifeos/bot/run.py set-chat <chat ID>")
            return 2
        telegram.set_chat_id(sys.argv[2])
        try:  # the running bot reads the id at start. launchd may hold the restart for its throttle, so do not wait for it
            subprocess.run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/com.tatsuki.desk.telegram"], capture_output=True, timeout=5)
        except subprocess.TimeoutExpired:
            pass
        print("Chat ID registered.")
        return 0
    token, owner = telegram.config()
    if not token:
        print(NO_TOKEN.format(path=paths.data_path("secrets", "telegram.json", mkdir=False)))
        return 2
    lock = open(paths.data_path("bot", "run.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("The bot is already running.")
        return 1
    found = router.discover()
    for slug, err in found.errors:
        print(f"Could not load  {slug}  {err}", file=sys.stderr)
    bots = plan_bots(token, telegram.config(telegram.SECRETARY)[0], found.routes)
    shown = ", ".join(f"{b[0]} {len(b[2])} tools" for b in bots)
    print(f"Bot started ({shown})" + ("" if owner else ". chat_id is not set"), flush=True)
    for bot, bot_token, routes, photos, unrouted in bots[1:]:
        threading.Thread(target=serve, args=(bot, bot_token, owner, routes, photos, unrouted), daemon=True).start()
    bot, bot_token, routes, photos, unrouted = bots[0]
    serve(bot, bot_token, owner, routes, photos, unrouted)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        pass
