// Weather from Open-Meteo, place names from OpenStreetMap. Neither needs Claude, so the morning
// message keeps coming after the monthly limit.
const WMO = [
  [[0], "快晴"], [[1, 2], "晴れ"], [[3], "くもり"], [[45, 48], "霧"],
  [[51, 53, 55, 56, 57], "霧雨"], [[61, 63, 65, 66, 67, 80, 81, 82], "雨"],
  [[71, 73, 75, 77, 85, 86], "雪"], [[95, 96, 99], "雷雨"],
];
const RAIN_FROM = 50; // percent

export function skyWord(code) {
  const hit = WMO.find(([codes]) => codes.includes(code));
  return hit ? hit[1] : "不明";
}

export function rainLine(times, chances) {
  const wet = [];
  chances.forEach((c, i) => { if (c >= RAIN_FROM) wet.push(i); });
  const max = Math.max(0, ...chances);
  if (!wet.length) return `雨の心配は少なめです（最大${max}%）`;
  const hour = (i) => Number(times[i].slice(11, 13));
  const spans = [];
  for (const i of wet) {
    const last = spans[spans.length - 1];
    if (last && last[1] === i - 1) last[1] = i;
    else spans.push([i, i]);
  }
  const words = spans.map(([a, b]) => `${hour(a)}〜${hour(b) + 1}時`);
  return `雨：${words.join("、")}（最大${max}%）`;
}

export async function forecast(lat, lon) {
  const url = new URL("https://api.open-meteo.com/v1/forecast");
  url.search = new URLSearchParams({
    latitude: lat, longitude: lon, timezone: "auto", forecast_days: "1",
    daily: "weather_code,temperature_2m_max,temperature_2m_min",
    hourly: "precipitation_probability",
  });
  const res = await fetch(url);
  if (!res.ok) throw new Error(`open-meteo ${res.status}`);
  return res.json();
}

export function weatherText(place, data) {
  const d = data.daily;
  return [
    `${place}の今日（天気：Open-Meteo）`,
    `${Math.round(d.temperature_2m_min[0])}〜${Math.round(d.temperature_2m_max[0])}℃ ${skyWord(d.weather_code[0])}`,
    rainLine(data.hourly.time, data.hourly.precipitation_probability),
  ].join("\n");
}

export async function placeName(lat, lon) {
  const fallback = `緯度${lat}・経度${lon}`;
  try {
    const url = new URL("https://nominatim.openstreetmap.org/reverse");
    url.search = new URLSearchParams({ format: "jsonv2", lat, lon, zoom: "10", "accept-language": "ja" });
    const res = await fetch(url, { headers: { "user-agent": "TatsukiDesk-assistant-bot/1.0" } });
    if (!res.ok) return fallback;
    const a = (await res.json()).address || {};
    return a.city || a.town || a.municipality || a.county || a.state || a.country || fallback;
  } catch {
    return fallback;
  }
}

export const roundCoord = (v) => Math.round(v * 100) / 100; // about 1 km: enough for weather
