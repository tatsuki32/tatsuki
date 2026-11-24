// Runs in the Instagram tab once per press of the owner, for the one person on screen. It presses Following, then Unfollow, and
// looks whether the page now says Follow. Anything unexpected stops it before a press: another person's page, another account
// signed in, words it does not know, or Instagram asking to slow down. It reads nothing else and sends nothing anywhere.
async function pressUnfollow(handle, own, wait = 10000) {
  const FOLLOWING = ["Following", "フォロー中"], FOLLOW = ["Follow", "Follow back", "フォローする", "フォローバックする"], UNFOLLOW = ["Unfollow", "フォローをやめる"];
  const STOP = /try again later|action blocked|we limit how often|we restrict certain activity|suspicious|しばらくしてから|ブロックされ|制限してい/i;
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const text = e => (e.innerText || "").trim();
  const buttons = root => [...root.querySelectorAll("button, [role=button]")];
  const named = (root, words) => buttons(root).find(b => words.includes(text(b)));
  const dialogs = () => [...document.querySelectorAll("[role=dialog], [role=alertdialog]")];
  const blocked = () => /^\/(challenge|accounts\/(login|suspended))/.test(location.pathname) || dialogs().some(d => STOP.test(text(d)));
  const until = async (look, ms) => { for (const end = Date.now() + ms; ;) { const v = look(); if (v || Date.now() > end) return v || null; await sleep(200); } };
  if (blocked()) return "blocked";
  if (location.pathname.replace(/\/$/, "") !== "/" + handle) return "other-page";
  const header = () => [...document.querySelectorAll("header")].find(h => text(h).split(/\s+/).includes(handle));
  const ready = await until(() => blocked() ? "blocked" : header() && (named(header(), FOLLOWING) || named(header(), FOLLOW)) && "ok", wait);
  if (ready === "blocked") return "blocked";
  if (!ready) return "no-button";
  if (![...document.querySelectorAll("a[href]")].some(a => a.getAttribute("href") === "/" + own + "/" && !a.closest("main"))) return "other-account";
  if (!named(header(), FOLLOWING)) return "already";
  named(header(), FOLLOWING).click();
  const menu = await until(() => dialogs().find(d => text(d).split(/\s+/).includes(handle) && named(d, UNFOLLOW)), 4000);
  if (!menu) return blocked() ? "blocked" : "no-menu";
  named(menu, UNFOLLOW).click();
  const end = await until(() => {
    if (blocked()) return "blocked";
    const h = header();
    if (h && !named(h, FOLLOWING) && named(h, FOLLOW)) return "ok";
    const again = dialogs().find(d => d !== menu && named(d, UNFOLLOW));  // a private account asks once more
    if (again && !again.dataset.deskPressed) { again.dataset.deskPressed = "1"; named(again, UNFOLLOW).click(); }
    return "";
  }, 6000);
  if (end !== "ok") return end || "not-sure";
  await sleep(800);
  return blocked() ? "blocked" : "ok";
}
