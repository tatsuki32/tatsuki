// Read-only Google Calendar and Gmail. The scopes allow no change and no sending.
const TOKEN_URL = "https://oauth2.googleapis.com/token";
const CAL = "https://www.googleapis.com/calendar/v3";
const GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me";
const MAX_CALENDARS = 10;
const MAX_DAYS = 62;
const MAX_MAILS = 5;
const MAX_BODY = 8000;

export class GoogleError extends Error {}

export function googleClient(env) {
  let token = null;
  async function accessToken() {
    if (token) return token;
    if (!env.GOOGLE_REFRESH_TOKEN) throw new GoogleError("not_configured");
    const res = await fetch(TOKEN_URL, {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        client_id: env.GOOGLE_CLIENT_ID,
        client_secret: env.GOOGLE_CLIENT_SECRET,
        refresh_token: env.GOOGLE_REFRESH_TOKEN,
        grant_type: "refresh_token",
      }),
    });
    if (!res.ok) throw new GoogleError(res.status === 400 ? "permission_expired" : `token_${res.status}`);
    token = (await res.json()).access_token;
    return token;
  }
  return async function get(url) {
    const res = await fetch(url, { headers: { authorization: `Bearer ${await accessToken()}` } });
    if (!res.ok) throw new GoogleError(`http_${res.status}`);
    return res.json();
  };
}

export async function listEvents(get, { start_date, end_date }) {
  const from = new Date(`${start_date}T00:00:00+07:00`);
  const to = new Date(`${end_date}T23:59:59+07:00`);
  if (!(to >= from) || (to - from) / 864e5 > MAX_DAYS) throw new GoogleError("bad_range");
  const list = await get(`${CAL}/users/me/calendarList?minAccessRole=reader&maxResults=${MAX_CALENDARS}`);
  const calendars = (list.items || []).filter((c) => c.selected !== false);
  const events = [];
  for (const cal of calendars) {
    const q = new URLSearchParams({
      timeMin: from.toISOString(), timeMax: to.toISOString(), singleEvents: "true",
      orderBy: "startTime", maxResults: "50", timeZone: "Asia/Ho_Chi_Minh",
    });
    const data = await get(`${CAL}/calendars/${encodeURIComponent(cal.id)}/events?${q}`);
    for (const e of data.items || []) {
      if (e.status === "cancelled") continue;
      events.push({
        calendar: cal.summary,
        title: e.summary || "（題名なし）",
        start: e.start?.dateTime || e.start?.date,
        end: e.end?.dateTime || e.end?.date,
        all_day: Boolean(e.start?.date),
        blocks_time: e.transparency !== "transparent",
        location: e.location || undefined,
      });
    }
  }
  events.sort((a, b) => String(a.start).localeCompare(String(b.start)));
  return { events };
}

const header = (msg, name) =>
  msg.payload?.headers?.find((h) => h.name.toLowerCase() === name)?.value || "";

export async function searchMail(get, { query }) {
  const q = new URLSearchParams({ q: query, maxResults: String(MAX_MAILS) });
  const list = await get(`${GMAIL}/messages?${q}`);
  const mails = [];
  for (const { id } of list.messages || []) {
    const m = await get(`${GMAIL}/messages/${id}?format=metadata&metadataHeaders=From&metadataHeaders=Subject&metadataHeaders=Date`);
    mails.push({ id, from: header(m, "from"), subject: header(m, "subject"), date: header(m, "date"), snippet: m.snippet });
  }
  return { mails };
}

export function decodeBase64Url(data) {
  const bin = atob(data.replace(/-/g, "+").replace(/_/g, "/"));
  return new TextDecoder().decode(Uint8Array.from(bin, (c) => c.charCodeAt(0)));
}

function findPart(part, type) {
  if (part.mimeType === type && part.body?.data) return part.body.data;
  for (const child of part.parts || []) {
    const hit = findPart(child, type);
    if (hit) return hit;
  }
  return null;
}

export function stripHtml(html) {
  return html
    .replace(/<(style|script)[\s\S]*?<\/\1>/gi, " ")
    .replace(/<br\s*\/?>|<\/p>|<\/div>/gi, "\n")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/g, " ").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">")
    .replace(/[ \t]+/g, " ").replace(/\n\s*\n+/g, "\n\n").trim();
}

export async function readMail(get, { id }) {
  if (!/^[\w-]+$/.test(id)) throw new GoogleError("bad_id");
  const m = await get(`${GMAIL}/messages/${id}?format=full`);
  const plain = m.payload && findPart(m.payload, "text/plain");
  const html = !plain && m.payload && findPart(m.payload, "text/html");
  const body = plain ? decodeBase64Url(plain) : html ? stripHtml(decodeBase64Url(html)) : m.snippet || "";
  return {
    from: header(m, "from"), to: header(m, "to"), subject: header(m, "subject"), date: header(m, "date"),
    body: body.slice(0, MAX_BODY),
    cut_off: body.length > MAX_BODY,
  };
}
