"""Minimal Telegram Bot API client. The token never appears in logs or error messages.

Settings, from the environment or the git-ignored <data>/secrets/telegram.json:
    LIFEOS_TELEGRAM_TOKEN    / "token"
    LIFEOS_TELEGRAM_CHAT_ID  / "chat_id"   (the only chat the bot serves)
"""
import json
import os
import urllib.parse
import urllib.request

from lifeos.lib import logs, paths

API = "https://api.telegram.org"
LIMIT = 4000  # Telegram's limit is 4096 characters


class TelegramError(Exception):
    pass


def config():
    """(token, chat_id). Either may be None."""
    saved = {}
    if not paths.is_sandbox():
        try:
            with open(paths.data_path("secrets", "telegram.json", mkdir=False), encoding="utf-8") as f:
                saved = json.load(f)
        except (OSError, ValueError):
            saved = {}
    token = os.environ.get("LIFEOS_TELEGRAM_TOKEN") or saved.get("token") or None
    chat = os.environ.get("LIFEOS_TELEGRAM_CHAT_ID") or saved.get("chat_id") or None
    return token, chat


def split_text(text, limit=LIMIT):
    return [text[i:i + limit] for i in range(0, len(text), limit)] or [""]


class Client:
    def __init__(self, token):
        self._token = token

    def call(self, method, timeout=30, **params):
        data = urllib.parse.urlencode(params).encode("utf-8")
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
        return self.call("getUpdates", timeout=timeout + 10, **params)

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


def notify(text):
    """Sends a message to the owner. True if sent.

    In a sandbox, without settings, or on failure the text goes to logs/outbox.jsonl instead.
    """
    token, chat = config()
    if token and chat and not paths.is_sandbox():
        try:
            Client(token).send_message(chat, text)
            return True
        except TelegramError:
            pass
    logs.append("outbox", {"text": text})
    return False
