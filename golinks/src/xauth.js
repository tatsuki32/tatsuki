// X (Twitter) requests signed with OAuth 1.0a, using the four keys of the person's own X developer app
// (Worker secrets X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET). Only the person's own account posts.

const pct = (s) => encodeURIComponent(s).replace(/[!'()*]/g, (c) => "%" + c.charCodeAt(0).toString(16).toUpperCase());

async function hmac(key, text) {
  const k = await crypto.subtle.importKey("raw", new TextEncoder().encode(key), { name: "HMAC", hash: "SHA-1" }, false, ["sign"]);
  const sig = new Uint8Array(await crypto.subtle.sign("HMAC", k, new TextEncoder().encode(text)));
  return btoa(String.fromCharCode(...sig));
}

// the Authorization header for one request; JSON and multipart bodies are not part of the signature, query params are
export async function authHeader(env, method, url, fixed = {}) {
  const u = new URL(url);
  const oauth = {
    oauth_consumer_key: env.X_API_KEY.trim(),
    oauth_nonce: [...crypto.getRandomValues(new Uint8Array(16))].map((b) => b.toString(16).padStart(2, "0")).join(""),
    oauth_signature_method: "HMAC-SHA1",
    oauth_timestamp: String(Math.floor(Date.now() / 1000)),
    oauth_token: env.X_ACCESS_TOKEN.trim(),
    oauth_version: "1.0",
    ...fixed, // only the test sets the nonce and time
  };
  const params = [...Object.entries(oauth), ...u.searchParams.entries()].map(([k, v]) => [pct(k), pct(v)]).sort((a, b) => (a[0] === b[0] ? (a[1] < b[1] ? -1 : 1) : a[0] < b[0] ? -1 : 1));
  const base = [method.toUpperCase(), pct(u.origin + u.pathname), pct(params.map(([k, v]) => `${k}=${v}`).join("&"))].join("&");
  oauth.oauth_signature = await hmac(`${pct(env.X_API_SECRET.trim())}&${pct(env.X_ACCESS_SECRET.trim())}`, base);
  return "OAuth " + Object.entries(oauth).map(([k, v]) => `${pct(k)}="${pct(v)}"`).join(", ");
}

export async function xCall(env, method, url, body) {
  const headers = { authorization: await authHeader(env, method, url) };
  let payload;
  if (body instanceof FormData) payload = body;
  else if (body !== undefined) { headers["content-type"] = "application/json"; payload = JSON.stringify(body); }
  const r = await fetch(url, { method, headers, body: payload });
  return r.json().catch(() => ({ title: `X answered ${r.status}` }));
}
