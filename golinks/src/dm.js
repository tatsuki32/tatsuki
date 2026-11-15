// Instagram DMs for Desk's Replies: read the conversations of the person's own Instagram accounts and send a reply
// they pressed Send on. Meta's Facebook-login route reaches Instagram messages through the Facebook Page the Instagram
// account is linked to, so each call first finds that Page and its token. Tokens never leave go.tany.im.

import { open } from "./accounts.js";

const GRAPH = "https://graph.facebook.com/v23.0";
const enc = encodeURIComponent;

async function get(url) {
  const r = await fetch(url);
  const v = await r.json().catch(() => ({}));
  if (!r.ok || v.error) throw new Error((v.error && (v.error.error_user_msg || v.error.message)) || `Meta answered ${r.status}`);
  return v;
}

// the Page linked to this Instagram account, with its token
async function pageFor(env, igId) {
  const row = await env.DB.prepare("SELECT token FROM accounts WHERE id = ?").bind(`instagram:${igId}`).first();
  if (!row) throw new Error("That Instagram account is not connected");
  const token = await open(env, row.token);
  const v = await get(`${GRAPH}/me/accounts?fields=id,name,access_token,instagram_business_account&limit=100&access_token=${enc(token)}`);
  const page = (v.data || []).find((p) => p.instagram_business_account && p.instagram_business_account.id === igId);
  return { token, page };
}

export async function conversations(env, igId) {
  const { token, page } = await pageFor(env, igId);
  const fields = "participants,updated_time,messages.limit(10){id,message,from,created_time,attachments}";
  let v;
  if (page) {
    v = await get(`${GRAPH}/${page.id}/conversations?platform=instagram&fields=${enc(fields)}&limit=25&access_token=${enc(page.access_token)}`);
  } else {
    // no linked Page: try the Instagram account itself (works only for some account setups)
    v = await get(`${GRAPH}/${igId}/conversations?platform=instagram&fields=${enc(fields)}&limit=25&access_token=${enc(token)}`);
  }
  const out = [];
  for (const c of v.data || []) {
    const them = (c.participants && c.participants.data || []).find((p) => p.id !== igId) || {};
    const msgs = ((c.messages && c.messages.data) || []).map((m) => ({
      id: m.id, at: Date.parse(m.created_time) || 0, mine: m.from && m.from.id === igId, text: m.message || "",
      attachment: !!(m.attachments && m.attachments.data && m.attachments.data.length),
    })).sort((a, b) => a.at - b.at);
    out.push({ id: c.id, updated: Date.parse(c.updated_time) || 0, with: { id: them.id || "", username: them.username || them.name || "" }, messages: msgs });
  }
  return { account: igId, page: page ? page.name : "", conversations: out };
}

export async function send(env, igId, to, text) {
  if (!to || !String(text || "").trim()) return { error: "Nothing to send" };
  const { page } = await pageFor(env, igId);
  if (!page) return { error: "This Instagram account is not linked to a Facebook Page, so Meta does not let the app send" };
  const r = await fetch(`${GRAPH}/${page.id}/messages?access_token=${enc(page.access_token)}`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ recipient: { id: to }, message: { text: String(text).slice(0, 1000) }, messaging_type: "RESPONSE" }),
  });
  const v = await r.json().catch(() => ({}));
  if (!r.ok || v.error) return { error: (v.error && (v.error.error_user_msg || v.error.message)) || `Meta answered ${r.status}` };
  return { ok: true, id: v.message_id || "" };
}
