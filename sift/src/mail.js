// The daily email: built here as a plain MIME message, sent with Cloudflare's send_email binding.
// Cloudflare only delivers to addresses verified in Email Routing, so it can only ever reach the owner's own inbox.

const enc = new TextEncoder();

export function b64(str) {
  const bytes = enc.encode(str);
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

const wrap = (s) => s.replace(/.{1,76}/g, "$&\r\n");
const header = (s) => (/^[\x20-\x7e]*$/.test(s) ? s : `=?UTF-8?B?${b64(s)}?=`);

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

export function mime({ from, fromName, to, subject, text, html, id }) {
  const boundary = "sift-" + id;
  return [
    `From: ${header(fromName)} <${from}>`,
    `To: <${to}>`,
    `Subject: ${header(subject)}`,
    `Date: ${new Date().toUTCString()}`,
    `Message-ID: <${id}@${from.split("@")[1]}>`,
    "MIME-Version: 1.0",
    `Content-Type: multipart/alternative; boundary="${boundary}"`,
    "",
    `--${boundary}`,
    "Content-Type: text/plain; charset=UTF-8",
    "Content-Transfer-Encoding: base64",
    "",
    wrap(b64(text)),
    `--${boundary}`,
    "Content-Type: text/html; charset=UTF-8",
    "Content-Transfer-Encoding: base64",
    "",
    wrap(b64(html)),
    `--${boundary}--`,
    "",
  ].join("\r\n");
}

export function dayLabel(day) {
  const [y, m, d] = day.split("-").map(Number);
  return `${["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"].at(m - 1)} ${d}`;
}

// groups: [{ folder, items: [{ title, url, site, line, later, task }] }]  (later / task = signed links)
export function digestEmail({ day, brief, groups, total, appUrl }) {
  const subject = `Sift Daily · ${dayLabel(day)} · ${total} new`;
  const t = [];
  t.push(subject, "");
  if (brief) t.push("TODAY IN BRIEF", brief, "");
  for (const g of groups) {
    t.push(g.folder.toUpperCase());
    for (const it of g.items) {
      t.push(`- ${it.title} (${it.site})`);
      if (it.line) t.push(`  ${it.line}`);
      t.push(`  ${it.url}`);
    }
    t.push("");
  }
  t.push(`Open Sift Daily: ${appUrl}`);

  const gold = "#c9a54a", ink = "#1c1d21", muted = "#5f6068", rule = "#e4e4e8";
  const h = [];
  h.push(`<!doctype html><html><body style="margin:0;padding:0;background:#f5f5f7;">`);
  h.push(`<div style="max-width:600px;margin:0 auto;padding:24px 16px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;color:${ink};">`);
  h.push(`<div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:${muted};">Sift Daily · ${esc(dayLabel(day))}</div>`);
  h.push(`<div style="font-size:20px;font-weight:700;margin:4px 0 16px;">${total} new from your sources</div>`);
  if (brief) h.push(`<div style="background:#fff;border-left:3px solid ${gold};padding:16px;margin-bottom:24px;font-size:14px;line-height:1.6;">${esc(brief)}</div>`);
  for (const g of groups) {
    h.push(`<div style="font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:${muted};margin:24px 0 8px;">${esc(g.folder)}</div>`);
    for (const it of g.items) {
      h.push(`<div style="background:#fff;padding:12px 16px;border-bottom:1px solid ${rule};">`);
      h.push(`<a href="${esc(it.url)}" style="font-size:16px;font-weight:700;color:${ink};text-decoration:none;line-height:1.4;">${esc(it.title)}</a>`);
      h.push(`<div style="font-size:12px;color:${muted};margin-top:4px;">${esc(it.site)}</div>`);
      if (it.line) h.push(`<div style="font-size:14px;line-height:1.5;margin-top:8px;">${esc(it.line)}</div>`);
      h.push(`<div style="font-size:12px;margin-top:8px;"><a href="${esc(it.later)}" style="color:${muted};">Later</a> &nbsp;·&nbsp; <a href="${esc(it.task)}" style="color:${muted};">Make it a task</a></div>`);
      h.push(`</div>`);
    }
  }
  h.push(`<div style="font-size:12px;color:${muted};margin-top:24px;">You get this because you added these sources to <a href="${esc(appUrl)}" style="color:${muted};">Sift Daily</a>.</div>`);
  h.push(`</div></body></html>`);
  return { subject, text: t.join("\n"), html: h.join("") };
}
