// Connecting social accounts for Post Studio's automatic posting.
// Desk asks for a sign-in link (/api/oauth/start), the person signs in and allows it on the platform's own page,
// the platform sends them back to go.tany.im/oauth/<platform>, and the token is kept here, encrypted with TOKEN_KEY.
// Tokens never go back to Desk; Desk only sees which accounts are connected and until when.

const STATE_TTL = 15 * 60 * 1000;

// each platform: where to sign in, how to swap the code for a token, how to make it last, and who it is
const GRAPH = "https://graph.facebook.com/v23.0";

export const PROVIDERS = {
  // Meta's unified login (Facebook Login for Business with the "Post Studio publishing" configuration):
  // one sign-in gives a token that does not expire, for the Instagram accounts the person picks
  meta: {
    name: "Instagram",
    id: (env) => env.META_APP_ID, secret: (env) => env.META_APP_SECRET,
    authorize: "https://www.facebook.com/v23.0/dialog/oauth",
    extra: (env) => ({ config_id: env.META_CONFIG_ID, override_default_response_type: "true" }),
    async token(env, code, redirect) {
      const r = await get(`${GRAPH}/oauth/access_token?client_id=${enc(env.META_APP_ID)}&client_secret=${enc(env.META_APP_SECRET)}&redirect_uri=${enc(redirect)}&code=${enc(code)}`);
      const token = r.access_token;
      const me = await get(`${GRAPH}/me?fields=id,name&access_token=${enc(token)}`);
      // the Instagram accounts this token may use; Meta has more than one way to list them, so try each
      const found = new Map();
      const tries = [
        `${GRAPH}/me/accounts?fields=instagram_business_account{id,username}&access_token=${enc(token)}`,
        `${GRAPH}/me/instagram_accounts?fields=id,username&access_token=${enc(token)}`,
        `${GRAPH}/me/assigned_instagram_accounts?fields=id,username&access_token=${enc(token)}`,
      ];
      const notes = [];
      for (const u of tries) {
        try {
          const v = await get(u);
          for (const x of v.data || []) {
            const ig = x.instagram_business_account || x;
            if (ig && ig.id && ig.username) found.set(ig.id, ig.username);
          }
          notes.push(`${u.split("?")[0].split("/").slice(-1)[0]}: ${(v.data || []).length}`);
        } catch (e) {
          notes.push(`${u.split("?")[0].split("/").slice(-1)[0]}: ${e.message.slice(0, 80)}`);
        }
      }
      return { many: [...found].map(([id, username]) => ({ provider: "instagram", account: id, handle: username })),
               account: me.id, handle: me.name || "", token, expires: 0, notes };
    },
  },
  instagram: {
    name: "Instagram",
    id: (env) => env.IG_APP_ID, secret: (env) => env.IG_APP_SECRET,
    scope: "instagram_business_basic,instagram_business_content_publish",
    authorize: "https://www.instagram.com/oauth/authorize",
    async token(env, code, redirect) {
      const r = await form("https://api.instagram.com/oauth/access_token", {
        client_id: env.IG_APP_ID, client_secret: env.IG_APP_SECRET, grant_type: "authorization_code", redirect_uri: redirect, code,
      });
      const long = await get(`https://graph.instagram.com/access_token?grant_type=ig_exchange_token&client_secret=${enc(env.IG_APP_SECRET)}&access_token=${enc(r.access_token)}`);
      const me = await get(`https://graph.instagram.com/me?fields=user_id,username&access_token=${enc(long.access_token)}`);
      return { account: String(me.user_id || r.user_id), handle: me.username || "", token: long.access_token, expires: Date.now() + (long.expires_in || 0) * 1000 };
    },
    async refresh(token) {
      const r = await get(`https://graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token&access_token=${enc(token)}`);
      return { token: r.access_token, expires: Date.now() + (r.expires_in || 0) * 1000 };
    },
  },
  threads: {
    name: "Threads",
    id: (env) => env.THREADS_APP_ID, secret: (env) => env.THREADS_APP_SECRET,
    scope: "threads_basic,threads_content_publish",
    authorize: "https://threads.net/oauth/authorize",
    async token(env, code, redirect) {
      const r = await form("https://graph.threads.net/oauth/access_token", {
        client_id: env.THREADS_APP_ID, client_secret: env.THREADS_APP_SECRET, grant_type: "authorization_code", redirect_uri: redirect, code,
      });
      const long = await get(`https://graph.threads.net/access_token?grant_type=th_exchange_token&client_secret=${enc(env.THREADS_APP_SECRET)}&access_token=${enc(r.access_token)}`);
      const me = await get(`https://graph.threads.net/v1.0/me?fields=id,username&access_token=${enc(long.access_token)}`);
      return { account: String(me.id || r.user_id), handle: me.username || "", token: long.access_token, expires: Date.now() + (long.expires_in || 0) * 1000 };
    },
    async refresh(token) {
      const r = await get(`https://graph.threads.net/refresh_access_token?grant_type=th_refresh_token&access_token=${enc(token)}`);
      return { token: r.access_token, expires: Date.now() + (r.expires_in || 0) * 1000 };
    },
  },
};

const enc = encodeURIComponent;
// secrets pasted by hand can carry a stray space or line break
const clean = (env) => { for (const k of ["META_APP_SECRET", "THREADS_APP_SECRET", "IG_APP_SECRET"]) if (typeof env[k] === "string") env[k] = env[k].trim(); };

async function get(url) {
  const r = await fetch(url);
  const v = await r.json().catch(() => ({}));
  if (!r.ok || v.error) throw new Error((v.error && (v.error.message || v.error)) || v.error_message || `answered ${r.status}`);
  return v;
}

async function form(url, fields) {
  const r = await fetch(url, { method: "POST", body: new URLSearchParams(fields) });
  const v = await r.json().catch(() => ({}));
  if (!r.ok || v.error || v.error_type) throw new Error((v.error && (v.error.message || v.error)) || v.error_message || `answered ${r.status}`);
  return v;
}

// --- tokens are kept encrypted (AES-GCM, key from the TOKEN_KEY secret) ---

async function aes(env) {
  const raw = Uint8Array.from(atob(env.TOKEN_KEY), (c) => c.charCodeAt(0));
  return crypto.subtle.importKey("raw", raw, "AES-GCM", false, ["encrypt", "decrypt"]);
}

export async function seal(env, text) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const out = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv }, await aes(env), new TextEncoder().encode(text)));
  return btoa(String.fromCharCode(...iv, ...out));
}

export async function open(env, sealed) {
  const all = Uint8Array.from(atob(sealed), (c) => c.charCodeAt(0));
  const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: all.slice(0, 12) }, await aes(env), all.slice(12));
  return new TextDecoder().decode(plain);
}

// --- the steps ---

export async function start(env, provider) {
  clean(env);
  const p = PROVIDERS[provider];
  if (!p) return { error: "Unknown platform" };
  if (!p.id(env) || !p.secret(env)) return { error: `${p.name} is not set up yet: the app id and secret are missing on go.tany.im` };
  const state = [...crypto.getRandomValues(new Uint8Array(18))].map((b) => b.toString(16).padStart(2, "0")).join("");
  await env.DB.prepare("INSERT INTO oauth_states (state, provider, created) VALUES (?, ?, ?)").bind(state, provider, Date.now()).run();
  const u = new URL(p.authorize);
  u.searchParams.set("client_id", p.id(env));
  u.searchParams.set("redirect_uri", `https://go.tany.im/oauth/${provider}`);
  u.searchParams.set("response_type", "code");
  if (p.scope) u.searchParams.set("scope", p.scope);
  for (const [k, v] of Object.entries(p.extra ? p.extra(env) : {})) u.searchParams.set(k, v);
  u.searchParams.set("state", state);
  return { url: u.toString() };
}

export async function callback(env, provider, url) {
  clean(env);
  const p = PROVIDERS[provider];
  const state = url.searchParams.get("state") || "";
  const row = p && (await env.DB.prepare("SELECT provider, created FROM oauth_states WHERE state = ?").bind(state).first());
  if (row) await env.DB.prepare("DELETE FROM oauth_states WHERE state = ?").bind(state).run();
  if (!row || row.provider !== provider || Date.now() - row.created > STATE_TTL) {
    return { ok: false, text: "This sign-in link has expired or was already used. Start again from Desk." };
  }
  if (url.searchParams.get("error")) return { ok: false, text: `${p.name} said no: ${url.searchParams.get("error_description") || url.searchParams.get("error")}` };
  const code = (url.searchParams.get("code") || "").replace(/#_$/, "");
  try {
    const t = await p.token(env, code, `https://go.tany.im/oauth/${provider}`);
    if (t.many) {
      // one sign-in, several accounts: each Instagram account gets its own row with the same token
      const sealed = await seal(env, t.token);
      for (const a of t.many) {
        await env.DB.prepare(
          "INSERT INTO accounts (id, provider, account, handle, token, expires, updated, problem) VALUES (?, ?, ?, ?, ?, 0, ?, '') " +
          "ON CONFLICT(id) DO UPDATE SET handle = excluded.handle, token = excluded.token, expires = 0, updated = excluded.updated, problem = ''"
        ).bind(`${a.provider}:${a.account}`, a.provider, a.account, a.handle, sealed, Date.now()).run();
      }
      if (!t.many.length) return { ok: false, text: `Signed in, but no Instagram account came with it (${t.notes.join("; ")}). Pick the accounts on Meta's page and try again.` };
      return { ok: true, text: `Connected: ${t.many.map((a) => "@" + a.handle).join(", ")}. You can close this page and go back to Desk.` };
    }
    await env.DB.prepare(
      "INSERT INTO accounts (id, provider, account, handle, token, expires, updated, problem) VALUES (?, ?, ?, ?, ?, ?, ?, '') " +
      "ON CONFLICT(id) DO UPDATE SET handle = excluded.handle, token = excluded.token, expires = excluded.expires, updated = excluded.updated, problem = ''"
    ).bind(`${provider}:${t.account}`, provider, t.account, t.handle, await seal(env, t.token), t.expires, Date.now()).run();
    return { ok: true, text: `${p.name} @${t.handle} is connected. You can close this page and go back to Desk.` };
  } catch (e) {
    return { ok: false, text: `Could not finish connecting ${p.name}: ${e.message}` };
  }
}

export async function forget(env, provider) {
  await env.DB.prepare("DELETE FROM accounts WHERE provider = ?").bind(provider).run();
  const code = [...crypto.getRandomValues(new Uint8Array(8))].map((b) => b.toString(16).padStart(2, "0")).join("");
  return { url: "https://go.tany.im/", confirmation_code: code };
}

export async function list(env) {
  const rows = (await env.DB.prepare("SELECT id, provider, account, handle, expires, updated, problem FROM accounts ORDER BY provider, handle").all()).results;
  return { accounts: rows, ready: Object.fromEntries(Object.entries(PROVIDERS).map(([k, p]) => [k, !!(p.id(env) && p.secret(env))])) };
}

export async function remove(env, id) {
  await env.DB.prepare("DELETE FROM accounts WHERE id = ?").bind(id).run();
  return { ok: true };
}

// tokens last about 60 days; this keeps each one fresh when it has less than 10 days left
export async function refreshAll(env) {
  const soon = Date.now() + 10 * 86400000;
  const rows = (await env.DB.prepare("SELECT id, provider, token FROM accounts WHERE expires < ? AND expires > ?").bind(soon, Date.now()).all()).results;
  for (const r of rows) {
    try {
      const t = await PROVIDERS[r.provider].refresh(await open(env, r.token));
      await env.DB.prepare("UPDATE accounts SET token = ?, expires = ?, updated = ?, problem = '' WHERE id = ?").bind(await seal(env, t.token), t.expires, Date.now(), r.id).run();
    } catch (e) {
      await env.DB.prepare("UPDATE accounts SET problem = ? WHERE id = ?").bind(`Could not renew: ${e.message}`.slice(0, 300), r.id).run();
    }
  }
}
