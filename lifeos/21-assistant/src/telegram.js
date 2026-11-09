// Telegram Bot API. Replies are plain text, and link previews stay off so that a link in a
// reply never makes Telegram's servers fetch a URL by itself.
const API = "https://api.telegram.org";
const LIMIT = 4000; // Telegram's limit is 4096 characters

export class TelegramError extends Error {}

async function call(env, method, body) {
  const res = await fetch(`${API}/bot${env.TELEGRAM_TOKEN}/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new TelegramError(`${method} ${res.status}`); // never include the URL: it holds the token
  return (await res.json()).result;
}

export function splitText(text, limit = LIMIT) {
  const parts = [];
  let rest = text;
  while (rest.length > limit) {
    const cut = rest.lastIndexOf("\n", limit);
    const at = cut > limit / 2 ? cut : limit;
    parts.push(rest.slice(0, at));
    rest = rest.slice(at).replace(/^\n/, "");
  }
  if (rest) parts.push(rest);
  return parts;
}

export async function send(env, text) {
  for (const part of splitText(text)) {
    await call(env, "sendMessage", {
      chat_id: env.OWNER_CHAT_ID,
      text: part,
      link_preview_options: { is_disabled: true },
    });
  }
}

export async function typing(env) {
  await call(env, "sendChatAction", { chat_id: env.OWNER_CHAT_ID, action: "typing" });
}

export async function downloadFile(env, fileId) {
  const file = await call(env, "getFile", { file_id: fileId });
  const res = await fetch(`${API}/file/bot${env.TELEGRAM_TOKEN}/${file.file_path}`);
  if (!res.ok) throw new TelegramError(`file ${res.status}`);
  return new Uint8Array(await res.arrayBuffer());
}

export function toBase64(bytes) {
  let out = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    out += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(out);
}
