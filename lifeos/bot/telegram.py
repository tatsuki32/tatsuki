"""Minimal Telegram Bot API client. The token never appears in logs or error messages.

The token comes from the environment or the macOS keychain (service tatsuki-desk-telegram), never from a file that is
backed up. The chat id is not a secret: the environment or <data>/secrets/telegram.json.
    LIFEOS_TELEGRAM_TOKEN    / keychain
    LIFEOS_TELEGRAM_CHAT_ID  / "chat_id"   (the only chat the bot serves)
A second bot, the secretary, has its own token: LIFEOS_TELEGRAM_SECRETARY_TOKEN / keychain service tatsuki-desk-telegram-secretary.
Telegram gives the owner the same chat id with every bot, so it stays one setting.
"""
import json
import os
import subprocess
import urllib.parse
import urllib.request

from lifeos.lib import logs, paths

API = "https://api.telegram.org"
LIMIT = 4000  # Telegram's limit is 4096 characters


class TelegramError(Exception):
    pass


RECORDS, SECRETARY = "records", "secretary"
KEYCHAIN = {RECORDS: ("tatsuki-desk-telegram", "telegram"), SECRETARY: ("tatsuki-desk-telegram-secretary", "telegram")}
TOKEN_ENV = {RECORDS: "LIFEOS_TELEGRAM_TOKEN", SECRETARY: "LIFEOS_TELEGRAM_SECRETARY_TOKEN"}


def keychain_token(bot=RECORDS):
    try:
        out = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN[bot][0], "-a", KEYCHAIN[bot][1], "-w"],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def set_chat_id(chat_id):
    """Keeps the chat id. Anything else already in the file stays."""
    path = paths.data_path("secrets", "telegram.json")
    try:
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        saved = {}
    saved["chat_id"] = str(chat_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(saved, f)


def config(bot=RECORDS):
    """(token, chat_id) for that bot. Either may be None."""
    saved = {}
    if not paths.is_sandbox():
        try:
            with open(paths.data_path("secrets", "telegram.json", mkdir=False), encoding="utf-8") as f:
                saved = json.load(f)
        except (OSError, ValueError):
            saved = {}
    token = os.environ.get(TOKEN_ENV[bot]) or (saved.get("token") if bot == RECORDS else None) or (None if paths.is_sandbox() else keychain_token(bot))
    chat = os.environ.get("LIFEOS_TELEGRAM_CHAT_ID") or saved.get("chat_id") or None
    return token, chat


def split_text(text, limit=LIMIT):
    return [text[i:i + limit] for i in range(0, len(text), limit)] or [""]


class Client:
    def __init__(self, token):
        self._token = token

    def call(self, method, timeout=30, fields=None, **params):
        """timeout is how long this Mac waits. fields carries names that clash with it, such as Telegram's own timeout."""
        data = urllib.parse.urlencode({**(fields or {}), **params}).encode("utf-8")
        try:
            with urllib.request.urlopen(f"{API}/bot{self._token}/{method}", data=data, timeout=timeout) as r:
                v = json.loads(r.read().decode("utf-8"))
        except (OSError, ValueError) as e:
            raise TelegramError(f"{method}: {type(e).__name__}") from None  # the URL holds the token
        if not v.get("ok"):
            raise TelegramError(f"{method}: {v.get('description') or 'failed'}")
        return v.get("result")

    def get_updates(self, offset=None, timeout=50):
        params = {"timeout": timeout, "allowed_updates": json.dumps(["message"])}
        if offset is not None:
            params["offset"] = offset
        return self.call("getUpdates", timeout=timeout + 10, fields=params)

    def send_message(self, chat_id, text):
        for part in split_text(text):
            self.call("sendMessage", chat_id=chat_id, text=part)

    def download(self, file_id, dest):
        info = self.call("getFile", file_id=file_id)
        try:
            with urllib.request.urlopen(f"{API}/file/bot{self._token}/{info['file_path']}", timeout=60) as r, open(dest, "wb") as f:
                f.write(r.read())
        except (OSError, KeyError) as e:
            raise TelegramError(f"download: {type(e).__name__}") from None
        return dest


def notify(text, bot=RECORDS):
    """Sends a message to the owner. True if sent. Without that bot's token the records bot sends it.

    In a sandbox, without settings, or on failure the text goes to logs/outbox.jsonl instead.
    """
    token, chat = config(bot)
    token = token or config()[0]
    if token and chat and not paths.is_sandbox():
        try:
            Client(token).send_message(chat, text)
            return True
        except TelegramError:
            pass
    logs.append("outbox", {"text": text})
    return False
