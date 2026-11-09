// The second Telegram bot, on Cloudflare Workers. Telegram calls fetch(), which only checks the
// sender and queues the update. queue() does the work, because a webhook must answer quickly and
// a Claude turn with web search can run for minutes. scheduled() sends the 5:00 weather.
import { runTurn, failureWord } from "./assistant.js";
import { addTurn, firstTime, forget, getHistory, getLocation, getUsage, putLocation, putUsage } from "./store.js";
import { downloadFile, send, toBase64, typing } from "./telegram.js";
import { limitOf } from "./usage.js";
import { forecast, placeName, roundCoord, weatherText } from "./weather.js";

const HELP = `使い方
・質問をそのまま送る（来週の空きは？ など）
・外国語の文や写真を送ると訳します
・「丁寧に断りたい」で相手の言語の文面を作ります（送信はしません）
・位置情報を送ると、朝5時の天気の場所が変わります
・「天気」「使用量」「忘れて」も使えます`;
const IMAGE_TYPES = ["image/jpeg", "image/png", "image/webp", "image/gif"];
const MAX_IMAGE = 5 * 1024 * 1024;

const sameSecret = (a, b) => {
  if (!a || !b || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
};

export function isOwner(env, message) {
  return Boolean(message) && String(message.chat?.id) === String(env.OWNER_CHAT_ID) && String(message.from?.id) === String(env.OWNER_CHAT_ID);
}

async function replyWeather(env, loc) {
  await send(env, weatherText(loc.place, await forecast(loc.lat, loc.lon)));
}

async function handleLocation(env, location) {
  const lat = roundCoord(location.latitude);
  const lon = roundCoord(location.longitude);
  const place = await placeName(lat, lon);
  await putLocation(env, { lat, lon, place });
  await send(env, `朝の天気の場所を ${place} にしました（場所：OpenStreetMap）。`);
  await replyWeather(env, { lat, lon, place });
}

async function usageLine(env, now) {
  const u = await getUsage(env, now);
  return `今月の Claude API：約 ${u.usd.toFixed(2)} ドル（上限 ${limitOf(env)} ドル）。計算上の目安です。`;
}

async function imageBlock(env, fileId, mediaType) {
  const bytes = await downloadFile(env, fileId);
  if (bytes.length > MAX_IMAGE) return null;
  return { type: "image", source: { type: "base64", media_type: mediaType, data: toBase64(bytes) } };
}

async function askClaude(env, message, now, client) {
  const usage = await getUsage(env, now);
  const limit = limitOf(env);
  if (usage.usd >= limit) {
    await send(env, `今月の上限 ${limit} ドルに届いたので、秘書と翻訳は来月まで止めています。朝の天気は続きます。`);
    return;
  }

  const text = message.text || message.caption || "";
  const content = [];
  let label = text;
  const doc = message.document;
  if (message.photo?.length) {
    const block = await imageBlock(env, message.photo[message.photo.length - 1].file_id, "image/jpeg");
    if (!block) return send(env, "写真が大きすぎます（5 MB まで）。");
    content.push(block);
    label = `[写真] ${text}`.trim();
  } else if (doc) {
    if (!IMAGE_TYPES.includes(doc.mime_type)) return send(env, "ファイルは画像だけ読めます。");
    const block = await imageBlock(env, doc.file_id, doc.mime_type);
    if (!block) return send(env, "画像が大きすぎます（5 MB まで）。");
    content.push(block);
    label = `[写真] ${text}`.trim();
  }
  if (text) content.push({ type: "text", text });
  if (!content.length) return send(env, HELP);

  await typing(env);
  const result = await runTurn(env, content, await getHistory(env), now, client);

  const spent = usage.usd + result.usd;
  const justReached = spent >= limit && !usage.notified;
  await putUsage(env, { usd: spent, notified: usage.notified || justReached }, now);

  if (result.refused) {
    await send(env, "この内容にはお答えできませんでした。");
  } else {
    const parts = [result.text || (result.drafts.length ? "" : "答えを作れませんでした。")];
    if (result.unfinished) parts.push("（途中までです。続きは送り直してください）");
    if (result.sources.length) parts.push(`出典\n${result.sources.join("\n")}`);
    const body = parts.filter(Boolean).join("\n\n");
    if (body) await send(env, body);
    for (const d of result.drafts) {
      await send(env, `意味：${d.meaning_ja}`);
      await send(env, d.draft); // alone, so that it is easy to copy
    }
    const remembered = [result.text, ...result.drafts.map((d) => `（渡した文面）${d.draft}`)].filter(Boolean).join("\n");
    await addTurn(env, label || "（内容なし）", remembered || "（返事なし）");
  }
  if (justReached) {
    await send(env, `今月の上限 ${limit} ドルに届きました。秘書と翻訳は来月まで止まります。朝の天気は続きます。`);
  }
}

export async function handleMessage(env, message, now = new Date(), client = null) {
  if (message.location) return handleLocation(env, message.location);
  const text = (message.text || "").trim();
  if (text === "/start" || text === "/help" || text === "使い方") return send(env, HELP);
  if (text === "使用量") return send(env, await usageLine(env, now));
  if (text === "忘れて") {
    await forget(env);
    return send(env, "今の会話の文脈を消しました。");
  }
  if (text === "天気") {
    const loc = await getLocation(env);
    return loc ? replyWeather(env, loc) : send(env, "位置情報を1回送ってください。");
  }
  return askClaude(env, message, now, client);
}

export default {
  async fetch(request, env) {
    if (request.method !== "POST") return new Response("not found", { status: 404 });
    if (!sameSecret(request.headers.get("x-telegram-bot-api-secret-token"), env.WEBHOOK_SECRET)) {
      return new Response("forbidden", { status: 403 });
    }
    const update = await request.json().catch(() => null);
    const message = update?.message; // edited messages are ignored, so live location does not move the place
    if (isOwner(env, message) && (await firstTime(env, update.update_id))) {
      await env.MESSAGES.send(message);
    }
    return new Response("ok"); // always ok: anyone else learns nothing
  },

  async queue(batch, env) {
    for (const item of batch.messages) {
      try {
        await handleMessage(env, item.body);
      } catch (e) {
        console.error("message failed", e?.name, e?.status); // no message text in logs
        await send(env, failureWord(e)).catch(() => {});
      }
    }
  },

  async scheduled(_event, env) {
    const loc = await getLocation(env);
    if (!loc) return; // nothing to go on until a location has been sent
    try {
      await replyWeather(env, loc);
    } catch (e) {
      console.error("weather failed", e?.name);
      await send(env, `${loc.place}の天気を取得できませんでした。`).catch(() => {});
    }
  },
};
