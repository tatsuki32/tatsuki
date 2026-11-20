// Worker entry. The logic lives in app.js (tested in Node); this file adds what only exists on Cloudflare:
// sending the sign-in email with the send_email binding.
import { EmailMessage } from 'cloudflare:email';
import { handle } from './app.js';

const b64 = (s) => { const bytes = new TextEncoder().encode(s); let bin = ''; for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000)); return btoa(bin); };
const header = (s) => (/^[\x20-\x7e]*$/.test(s) ? s : `=?UTF-8?B?${b64(s)}?=`);

// Cloudflare only delivers to addresses verified in Email Routing for tany.im, so this can only reach the owner.
async function sendMail(env, { to, subject, text }) {
  const from = env.MAIL_FROM; // secret: the sender address at tany.im (kept out of this public repo)
  if (!from) throw new Error('MAIL_FROM is not set');
  const id = crypto.randomUUID();
  const raw = [
    `From: Rhizome Box <${from}>`, `To: <${to}>`, `Subject: ${header(subject)}`, `Date: ${new Date().toUTCString()}`,
    `Message-ID: <${id}@tany.im>`, 'MIME-Version: 1.0', 'Content-Type: text/plain; charset=UTF-8', 'Content-Transfer-Encoding: base64', '',
    b64(text).replace(/.{1,76}/g, '$&\r\n'), '',
  ].join('\r\n');
  await env.MAIL.send(new EmailMessage(from, to, raw));
}

export default {
  async fetch(request, env, ctx) {
    try {
      return await handle(request, env, ctx, { sendMail });
    } catch (e) {
      console.error(e && e.stack || e);
      return new Response('Something went wrong. Please try again.', { status: 500, headers: { 'Content-Type': 'text/plain; charset=utf-8' } });
    }
  },
};
