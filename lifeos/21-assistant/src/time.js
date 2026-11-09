// Dates in Ho Chi Minh time (UTC+7), which has no daylight saving.
const OFFSET_MS = 7 * 60 * 60 * 1000;

export function hcmParts(now = new Date()) {
  const iso = new Date(now.getTime() + OFFSET_MS).toISOString();
  return { date: iso.slice(0, 10), month: iso.slice(0, 7), time: iso.slice(11, 16) };
}

export function nowLine(now = new Date()) {
  const p = hcmParts(now);
  const week = "日月火水木金土"[new Date(now.getTime() + OFFSET_MS).getUTCDay()];
  return `${p.date}（${week}）${p.time}（ホーチミン時間）`;
}
