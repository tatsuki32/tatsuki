// The privacy policy and data-deletion pages Meta (and LinkedIn, X, Google) ask for. They describe what this app,
// Tatsuki Post Studio, does: it posts and reads messages only for the accounts of its one owner.
const shell = (title, body) => new Response(
  `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${title}</title>` +
  `<body style="font-family:system-ui,sans-serif;max-width:40em;margin:6vh auto;padding:0 16px;line-height:1.6;color:#222">${body}</body></html>`,
  { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "public, max-age=3600" } });

export const privacy = () => shell("Privacy Policy — Tatsuki Post Studio", `
<h1>Privacy Policy</h1>
<p><b>Tatsuki Post Studio</b> is a personal publishing tool run by Tatsuki Mitsuhashi (TANY). It is used only by its owner, for the owner's own accounts on Instagram, Facebook Pages, Threads, LinkedIn, X and YouTube. It is not offered to other people.</p>
<h2>What it does with platform data</h2>
<ul>
<li><b>Publishing:</b> it sends posts, pictures and videos that the owner wrote and approved to the owner's own accounts.</li>
<li><b>Messages:</b> it reads direct messages sent to the owner's own Instagram professional accounts, so the owner can see them in a private dashboard and reply. A reply is sent only when the owner presses Send.</li>
<li><b>Account details:</b> it reads the name, id and username of the owner's connected accounts to know where to post.</li>
</ul>
<h2>What it keeps, and where</h2>
<ul>
<li>Access tokens are stored encrypted (AES-GCM) on the app's server (Cloudflare) and never leave it.</li>
<li>Message text and draft replies are kept on the owner's own computer only while a reply is pending, and removed once the conversation is answered.</li>
<li>Pictures and videos waiting to be posted are kept for at most two days, then deleted automatically.</li>
<li>No data is sold, shared with third parties, or used for advertising or profiling. Message text may be sent to Anthropic's Claude API only to draft a reply for the owner; it is not used to train models.</li>
</ul>
<h2>Deleting data</h2>
<p>See <a href="/data-deletion">data deletion</a>. Contact: <a href="https://tany.im">tany.im</a>.</p>
<p>Last updated: 2026-11-15.</p>`);

export const deletion = () => shell("Data deletion — Tatsuki Post Studio", `
<h1>Data deletion</h1>
<p>Tatsuki Post Studio keeps no data about anyone other than its owner beyond the short times described in the <a href="/privacy">privacy policy</a>.</p>
<p>To remove everything the app holds for a connected account:</p>
<ol>
<li>Remove the app in the platform's settings (for Facebook and Instagram: Settings → Apps and websites → Tatsuki Post Studio → Remove). The stored token stops working at once.</li>
<li>Or ask through the contact on <a href="https://tany.im">tany.im</a>; tokens, queued posts and any stored message text for the account are deleted within 7 days.</li>
</ol>
<p>Messages from people who wrote to the owner are kept only on the owner's computer until the conversation is answered, and media waiting to be posted is deleted after two days.</p>`);
