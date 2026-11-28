/**
 * Morning Log Bots — Google Apps Script bound to the spreadsheet "Life Log Bots".
 *
 * Two Telegram bots, both asked at 4:00 where the owner is:
 *   Sleep & Steps: the answer (text or a screenshot) is saved at once, with an Undo button.
 *   Meals: a photo or text is saved at once with all 17 nutrients (the Desk's list), each shown as a share of
 *          the owner's daily target, plus the day's running total. A message that corrects the last meal
 *          replaces it; Undo removes it.
 * Rows go to the sheets "Sleep & Steps" and "Meals". The Mac's Desk takes them once a day
 * (desk/lifebots.py, through doGet below) and tells this script the Mac's time zone and the owner's targets.
 *
 * Runs every minute (trigger made by setup()). Telegram is polled with getUpdates; no webhook.
 * Script Properties: SLEEP_TOKEN, MEALS_TOKEN, ANTHROPIC_KEY (set by the owner), DESK_KEY (made by setup()),
 * and the script's own state (OWNER, OFFSET_*, ASKED_*, TZ*, TARGETS, LAST_MEAL, PENDING_MEAL from the old draft flow).
 * Nothing here holds the owner's location: a location pin is turned into a time zone name and dropped.
 */

var MODEL = 'claude-haiku-5-5';
var API = 'https://api.anthropic.com/v1/messages';
var API_VERSION = '2026-10-21';
var DEFAULT_TZ = 'Asia/Ho_Chi_Minh';
var ASK_FROM = '04:00';
var ASK_UNTIL = '12:00'; // a missed minute (Google's own pause) still asks, but never in the afternoon
var BOTS = ['sleep', 'meals'];
var TOKEN = { sleep: 'SLEEP_TOKEN', meals: 'MEALS_TOKEN' };
var SHEETS = {
  sleep: { name: 'Sleep & Steps', cols: ['id', 'date', 'kind', 'sleep_min', 'bedtime', 'wake_time', 'steps', 'text', 'saved_at', 'imported_at', 'deleted'] },
  // columns are found by their header name, so the nutrients added later sit after 'deleted' in older sheets
  meals: { name: 'Meals', cols: ['id', 'date', 'meal', 'items', 'kcal', 'protein_g', 'fat_g', 'carb_g', 'salt_g', 'text', 'saved_at', 'imported_at', 'deleted',
    'saturated_fat_g', 'fiber_g', 'calcium_mg', 'magnesium_mg', 'iron_mg', 'zinc_mg', 'vitamin_a_ug', 'vitamin_d_ug', 'vitamin_b1_mg', 'vitamin_b2_mg', 'vitamin_b6_mg', 'vitamin_c_mg'] },
  settings: { name: 'Settings', cols: ['key', 'value', 'updated'] }
};
// the Desk's 17 nutrients (desk/lifelog.py NUTRIENTS): sheet column, label, unit, decimals, the most one item may hold, the Desk's key
var NUT = [
  ['kcal', 'Energy', 'kcal', 0, 5000, 'energy_kcal'],
  ['protein_g', 'Protein', 'g', 1, 500], ['fat_g', 'Fat', 'g', 1, 500], ['saturated_fat_g', 'Saturated fat', 'g', 1, 200],
  ['carb_g', 'Carbs', 'g', 1, 1500], ['fiber_g', 'Fiber', 'g', 1, 200], ['salt_g', 'Salt', 'g', 1, 100],
  ['calcium_mg', 'Calcium', 'mg', 0, 5000], ['magnesium_mg', 'Magnesium', 'mg', 0, 3000], ['iron_mg', 'Iron', 'mg', 1, 200], ['zinc_mg', 'Zinc', 'mg', 1, 200],
  ['vitamin_a_ug', 'Vitamin A', 'µg', 0, 20000], ['vitamin_d_ug', 'Vitamin D', 'µg', 1, 500], ['vitamin_b1_mg', 'Vitamin B1', 'mg', 2, 100],
  ['vitamin_b2_mg', 'Vitamin B2', 'mg', 2, 100], ['vitamin_b6_mg', 'Vitamin B6', 'mg', 2, 100], ['vitamin_c_mg', 'Vitamin C', 'mg', 0, 5000]
];
function deskKey(n) { return n[5] || n[0]; }
var FIX_HOURS = 6; // a message this soon after a meal may correct it
var QUESTION = {
  sleep: 'Good morning. How long did you sleep last night, and how many steps did you walk yesterday?\nType it ("7h30m, 8200 steps") or send a screenshot.',
  meals: 'What did you eat yesterday? Send photos or type it, one message or many.\nI save each one right away with all 17 nutrients and how far it gets you toward your daily targets.'
};
var YES = /^\s*(yes|y|yep|ok|okay|sure|save|save it|good|looks good|correct|はい|うん|ok|おけ|オッケー|おっけー|いいよ|いいです|大丈夫|登録|登録して|それで|それでいい|お願い|おねがい|👍|✅)\s*[!！。.]*\s*$/i;
var CANCEL = /^\s*(cancel|discard|never mind|キャンセル|やめる|やめて|取り消し|いらない)\s*[!！。.]*\s*$/i;
var MEALS = ['breakfast', 'lunch', 'dinner', 'snack'];
var TEXT = ['date', 'bedtime', 'wake_time', 'text', 'items'];

// ---------- small helpers ----------

function now() { return new Date(); }
function props() { return PropertiesService.getScriptProperties(); }
function prop(k) { return props().getProperty(k) || ''; }
function setProp(k, v) { if (v === null || v === undefined || v === '') props().deleteProperty(k); else props().setProperty(k, String(v)); }

function tz() { return prop('TZ') || DEFAULT_TZ; }
function validTz(name) {
  if (!/^[A-Za-z_]+(\/[A-Za-z0-9_+\-]+){0,2}$/.test(name || '') && name !== 'UTC') return false;
  try { Utilities.formatDate(now(), name, 'yyyy-MM-dd'); return true; } catch (e) { return false; }
}
function localDate(t) { return Utilities.formatDate(t || now(), tz(), 'yyyy-MM-dd'); }
function localTime(t) { return Utilities.formatDate(t || now(), tz(), 'HH:mm'); }
function addDays(iso, n) {
  var d = new Date(iso + 'T12:00:00Z');
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}
var MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
function shortDate(iso) { return MON[Number(iso.slice(5, 7)) - 1] + ' ' + Number(iso.slice(8, 10)); }
function dayWord(iso) {
  var t = localDate();
  return shortDate(iso) + (iso === t ? ' (today)' : iso === addDays(t, -1) ? ' (yesterday)' : '');
}
function hm(min) { var h = Math.floor(min / 60), m = min % 60; return h + 'h' + (m ? ' ' + m + 'm' : ''); }
function num(n) { return Math.round(n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ','); }
function newId() { return 'r' + Utilities.getUuid().replace(/-/g, '').slice(0, 11); } // a letter first: Sheets never turns it into a number
function okDate(d) {
  var t = localDate();
  return typeof d === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(d) && d <= t && d >= addDays(t, -7);
}
function okClock(s) { return typeof s === 'string' && /^([01]?\d|2[0-3]):[0-5]\d$/.test(s); }

// ---------- the sheet ----------

function book() { return SpreadsheetApp.getActiveSpreadsheet(); }
function sheet(kind) {
  var s = SHEETS[kind], sh = book().getSheetByName(s.name);
  if (!sh) {
    sh = book().insertSheet(s.name);
    sh.getRange(1, 1, 1, s.cols.length).setValues([s.cols]);
    sh.setFrozenRows(1);
  }
  return sh;
}
// the header row, with any column this version added (an older sheet gets them at its right end)
function header(kind) {
  var sh = sheet(kind), v = sh.getDataRange().getValues(), head = (v[0] || []).map(function (h) { return h === undefined || h === null ? '' : String(h); });
  while (head.length && !head[head.length - 1]) head.pop();
  var missing = SHEETS[kind].cols.filter(function (c) { return head.indexOf(c) < 0; });
  if (missing.length) {
    sh.getRange(1, head.length + 1, 1, missing.length).setValues([missing]);
    head = head.concat(missing);
  }
  return head;
}
function rows(kind) {
  var head = header(kind), v = sheet(kind).getDataRange().getValues(), out = [];
  for (var i = 1; i < v.length; i++) {
    var r = { _row: i + 1 };
    for (var c = 0; c < head.length; c++) if (head[c]) r[head[c]] = v[i][c] === undefined ? '' : v[i][c];
    if (r.id) out.push(r);
  }
  return out;
}
function append(kind, obj) {
  sheet(kind).appendRow(header(kind).map(function (c) {
    var v = obj[c] === undefined || obj[c] === null ? '' : obj[c];
    return TEXT.indexOf(c) >= 0 && v !== '' ? "'" + v : v; // dates and clock times stay text, not Sheets dates in another time zone
  }));
}
function setCell(kind, row, col, value) {
  sheet(kind).getRange(row, header(kind).indexOf(col) + 1).setValue(value);
}
function showSetting(key, value) {
  var sh = sheet('settings'), v = sh.getDataRange().getValues(), stamp = now().toISOString();
  for (var i = 1; i < v.length; i++) if (v[i][0] === key) { sh.getRange(i + 1, 2, 1, 2).setValues([[value, stamp]]); return; }
  sh.appendRow([key, value, stamp]);
}

// ---------- Telegram ----------

function tg(bot, method, payload) {
  var token = prop(TOKEN[bot]);
  if (!token) return null;
  var res = UrlFetchApp.fetch('https://api.telegram.org/bot' + token + '/' + method, {
    method: 'post', contentType: 'application/json', payload: JSON.stringify(payload || {}), muteHttpExceptions: true
  });
  try { return JSON.parse(res.getContentText()); } catch (e) { return null; }
}
function say(bot, text, buttons) {
  var p = { chat_id: Number(prop('OWNER')), text: text };
  if (buttons) p.reply_markup = { inline_keyboard: [buttons.map(function (b) { return { text: b[0], callback_data: b[1] }; })] };
  return tg(bot, 'sendMessage', p);
}
function photoBase64(bot, message) {
  var fileId = null;
  if (message.photo && message.photo.length) {
    var pick = message.photo[message.photo.length - 1];
    message.photo.forEach(function (p) { if (p.width <= 1600 && p.height <= 1600) pick = p; });
    fileId = pick.file_id;
  } else if (message.document && /^image\/(jpeg|png|webp)$/.test(message.document.mime_type || '')) {
    fileId = message.document.file_id;
  }
  if (!fileId) return null;
  var f = tg(bot, 'getFile', { file_id: fileId });
  if (!f || !f.ok) return null;
  var blob = UrlFetchApp.fetch('https://api.telegram.org/file/bot' + prop(TOKEN[bot]) + '/' + f.result.file_path).getBlob();
  var type = message.document ? message.document.mime_type : 'image/jpeg';
  return { type: 'image', source: { type: 'base64', media_type: type, data: Utilities.base64Encode(blob.getBytes()) } };
}

// ---------- Claude ----------

function claude(system, content, schema) {
  var key = prop('ANTHROPIC_KEY');
  if (!key) throw new Error('nokey');
  var body = {
    model: prop('MODEL') || MODEL, max_tokens: 12000, system: system,
    output_config: { effort: 'low', format: { type: 'json_schema', schema: schema } },
    messages: [{ role: 'user', content: content }]
  };
  var res = UrlFetchApp.fetch(API, {
    method: 'post', contentType: 'application/json', muteHttpExceptions: true,
    headers: { 'x-api-key': key, 'anthropic-version': API_VERSION }, payload: JSON.stringify(body)
  });
  var code = res.getResponseCode();
  if (code === 401 || code === 403) throw new Error('badkey');
  if (code !== 200) throw new Error('busy');
  var out = JSON.parse(res.getContentText());
  if (out.stop_reason === 'refusal' || out.stop_reason === 'max_tokens') throw new Error('unread');
  var text = (out.content || []).filter(function (b) { return b.type === 'text'; }).map(function (b) { return b.text; }).join('');
  return JSON.parse(text);
}
function nullable(obj) { return { anyOf: [obj, { type: 'null' }] }; }
var SLEEP_SCHEMA = {
  type: 'object', additionalProperties: false, required: ['sleep', 'steps'],
  properties: {
    sleep: nullable({ type: 'object', additionalProperties: false, required: ['minutes', 'bedtime', 'wake_time', 'date'], properties: {
      minutes: { type: 'integer' }, bedtime: { type: ['string', 'null'] }, wake_time: { type: ['string', 'null'] }, date: { type: 'string' } } }),
    steps: nullable({ type: 'object', additionalProperties: false, required: ['steps', 'date'], properties: {
      steps: { type: 'integer' }, date: { type: 'string' } } })
  }
};
var ITEM = (function () {
  var p = { name: { type: 'string' }, amount: { type: 'string' } };
  NUT.forEach(function (n) { p[n[0]] = { type: 'number' }; });
  return { type: 'object', additionalProperties: false, required: Object.keys(p), properties: p };
})();
var MEAL_SCHEMA = {
  type: 'object', additionalProperties: false, required: ['food', 'change', 'date', 'meals'],
  properties: {
    food: { type: 'boolean' }, change: { type: 'string', enum: ['new', 'fix'] }, date: { type: 'string' },
    meals: { type: 'array', items: { type: 'object', additionalProperties: false, required: ['meal', 'items'], properties: {
      meal: { type: 'string', enum: MEALS }, items: { type: 'array', items: ITEM } } } }
  }
};

function readSleep(text, image) {
  var t = localDate();
  var system = 'You read sleep and step records for one person. Return only what the message states or the screenshot shows; never guess.\n' +
    'sleep.minutes = total sleep in minutes ("7h30m", "7時間半" = 450). bedtime and wake_time as HH:MM (24h) when given, else null.\n' +
    'sleep.date = the day the person woke up: ' + t + ' unless the message names another day.\n' +
    'steps.date = ' + addDays(t, -1) + ' (yesterday) unless the message or screenshot names another day ("today" = ' + t + ').\n' +
    'Missing → null. Dates are YYYY-MM-DD.';
  var content = [];
  if (image) content.push(image);
  content.push({ type: 'text', text: 'Now: ' + t + ' ' + localTime() + '.\nMessage: ' + (text || '(screenshot only)') });
  return claude(system, content, SLEEP_SCHEMA);
}
function readMeal(prev, text, image) {
  var t = localDate(), hour = Number(localTime().slice(0, 2));
  var base = hour < 12 ? addDays(t, -1) : t;
  var system = 'You estimate what one person ate: dishes, amounts, energy and nutrients, for a food log.\n' +
    'Use usual portion sizes when no amount is given. Every number is per item, for the amount eaten: ' +
    NUT.map(function (n) { return n[0] + ' (' + n[1] + ', ' + n[2] + ')'; }).join(', ') + '. salt_g is the salt equivalent of the sodium.\n' +
    'Fill every nutrient with your best estimate from standard food composition data; use 0 only when the food truly has none. Never skip one.\n' +
    'Item names and amounts in English, short. meal is breakfast, lunch, dinner or snack: from the words or the time named; otherwise your best fit.\n' +
    'date: ' + base + ' unless the message names another day ("today" = ' + t + ', "yesterday" = ' + addDays(t, -1) + '). YYYY-MM-DD.\n' +
    'If a last saved meal is given and the message corrects it (amounts, items, which meal, which day, "that was half", "no rice"), return change="fix" and the whole corrected set, keeping what it does not change.\n' +
    'Otherwise return change="new" and only the food in this message.\n' +
    'If the message is not about food at all, return food=false, change="new" and no meals.';
  var content = [];
  if (image) content.push(image);
  content.push({ type: 'text', text: (prev ? 'Last saved meal:\n' + JSON.stringify({ date: prev.date, meals: prev.meals }) + '\n\n' : '') +
    'Now: ' + t + ' ' + localTime() + '.\nMessage: ' + (text || '(photo only)') });
  return claude(system, content, MEAL_SCHEMA);
}

// ---------- the minute ----------

function tick() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) return;
  try {
    BOTS.forEach(poll);
    if (pending() && prop('OWNER')) saveMeal(pending(), 'Saved:'); // a draft left from the old flow is saved, not lost
    maybeAsk();
  } finally {
    lock.releaseLock();
  }
}

function poll(bot) {
  if (!prop(TOKEN[bot])) return;
  var res = tg(bot, 'getUpdates', { offset: Number(prop('OFFSET_' + bot) || 0), timeout: 0, allowed_updates: ['message', 'callback_query'] });
  if (!res || !res.ok) return;
  res.result.forEach(function (u) {
    setProp('OFFSET_' + bot, u.update_id + 1); // saved first: one bad message never repeats forever
    try { handle(bot, u); } catch (e) { if (isOwner(u)) say(bot, trouble(e)); }
  });
}

function trouble(e) {
  var m = String(e && e.message);
  if (m === 'nokey') return 'The Anthropic key is not set in the script yet, so I can\'t read this. Nothing was saved.';
  if (m === 'badkey') return 'Anthropic did not accept the key. Nothing was saved.';
  if (m === 'busy') return 'Claude is busy right now. Please send it again in a minute. Nothing was saved.';
  return 'Something went wrong, so nothing was saved. Please send it again.';
}

function senderId(u) {
  var m = u.message || (u.callback_query && u.callback_query.message);
  var from = u.message ? u.message.from : u.callback_query && u.callback_query.from;
  if (!m || !m.chat || m.chat.type !== 'private' || !from) return null;
  return String(from.id);
}
function isOwner(u) { var id = senderId(u); return !!id && id === prop('OWNER'); }

function handle(bot, u) {
  var id = senderId(u);
  if (!id) return;
  var m = u.message;
  if (!prop('OWNER') && m && /^\/start\b/.test(m.text || '')) {
    setProp('OWNER', id); // the first person to say /start owns both bots; everyone else is ignored
  }
  if (id !== prop('OWNER')) return;
  if (u.callback_query) return onButton(bot, u.callback_query);
  if (!m) return;
  var text = (m.text || m.caption || '').trim();
  if (/^\/start\b/.test(text)) return say(bot, (bot === 'sleep' ? 'Hi. Every morning at 4:00 I\'ll ask about your sleep and steps.' : 'Hi. Every morning at 4:00 I\'ll ask what you ate. Anything you send is saved right away with all 17 nutrients.') + '\nTime zone now: ' + tz() + '. Send your location or /tz Area/City to change it.');
  if (/^\/tz\b/.test(text)) return onTz(bot, text.replace(/^\/tz(@\w+)?\s*/, ''));
  if (/^\/help\b/.test(text)) return say(bot, bot === 'sleep' ? 'Send "7h30m, 8200 steps" or a screenshot. /tz shows or changes the time zone.' : 'Send a photo or text of what you ate. It is saved right away; send a fix ("the rice was half") or press Undo. /tz shows or changes the time zone.');
  if (m.location) return onLocation(bot, m.location);
  if (bot === 'sleep') return onSleep(text, photoBase64(bot, m));
  return onMeal(text, photoBase64(bot, m));
}

// ---------- time zone ----------

function setTz(name, from) {
  setProp('TZ', name);
  setProp('TZ_FROM', from);
  setProp('TZ_AT', now().toISOString());
  showSetting('tz', name + ' (from ' + from + ')');
}
function onTz(bot, arg) {
  if (!arg) return say(bot, 'Time zone: ' + tz() + ' (from ' + (prop('TZ_FROM') || 'default') + '). It is ' + localTime() + ' there.');
  if (!validTz(arg)) return say(bot, 'I don\'t know that time zone. Try a name like Asia/Tokyo or Europe/Berlin.');
  setTz(arg, 'you');
  return say(bot, 'Time zone set to ' + arg + '. It is ' + localTime() + ' there, so I\'ll ask at 4:00 local time.');
}
function onLocation(bot, loc) {
  var name = '';
  try {
    var r = UrlFetchApp.fetch('https://timeapi.io/api/timezone/coordinate?latitude=' + Number(loc.latitude).toFixed(2) + '&longitude=' + Number(loc.longitude).toFixed(2), { muteHttpExceptions: true });
    name = (JSON.parse(r.getContentText()) || {}).timeZone || '';
  } catch (e) { name = ''; }
  if (!validTz(name)) return say(bot, 'I couldn\'t tell the time zone from that. Send /tz Area/City, like /tz Asia/Tokyo.');
  setTz(name, 'location');
  return say(bot, 'Time zone set to ' + name + '. It is ' + localTime() + ' there. I didn\'t keep the location.');
}

// ---------- Sleep & Steps ----------

function onSleep(text, image) {
  if (!text && !image) return say('sleep', 'Send it as text ("7h30m, 8200 steps") or a screenshot.');
  var r = readSleep(text, image), saved = [], ids = [], at = now().toISOString(), parts = [], doubt = [];
  if (r.sleep && r.sleep.minutes) {
    if (r.sleep.minutes < 1 || r.sleep.minutes > 1200 || !okDate(r.sleep.date)) doubt.push('sleep');
    else {
      var sid = newId();
      append('sleep', { id: sid, date: r.sleep.date, kind: 'sleep', sleep_min: r.sleep.minutes, bedtime: okClock(r.sleep.bedtime) ? r.sleep.bedtime : '',
        wake_time: okClock(r.sleep.wake_time) ? r.sleep.wake_time : '', text: text.slice(0, 500), saved_at: at });
      ids.push(sid); saved.push('sleep');
      var st = targets().total_min;
      parts.push('sleep ' + hm(r.sleep.minutes) + (r.sleep.bedtime && r.sleep.wake_time ? ' (' + r.sleep.bedtime + '–' + r.sleep.wake_time + ')' : '') + ' for ' + dayWord(r.sleep.date) +
        (st && st.target > 0 ? ' — ' + pct(r.sleep.minutes, st.target) + ' of your ' + hm(Math.round(st.target)) + ' target' : ''));
    }
  }
  if (r.steps && r.steps.steps !== null && r.steps.steps !== undefined) {
    if (r.steps.steps < 0 || r.steps.steps > 200000 || !okDate(r.steps.date)) doubt.push('steps');
    else {
      var tid = newId();
      append('sleep', { id: tid, date: r.steps.date, kind: 'steps', steps: r.steps.steps, text: text.slice(0, 500), saved_at: at });
      ids.push(tid); saved.push('steps');
      var pt = targets().steps;
      parts.push(num(r.steps.steps) + ' steps for ' + dayWord(r.steps.date) + (pt && pt.target > 0 ? ' — ' + pct(r.steps.steps, pt.target) + ' of your ' + num(pt.target) + ' target' : ''));
    }
  }
  if (!saved.length) {
    return say('sleep', doubt.length ? 'That ' + doubt.join(' and ') + ' looks off (the number or the day), so I didn\'t save it. Could you send it again?' : 'I couldn\'t find sleep or steps in that. Try "7h30m, 8200 steps".');
  }
  var more = saved.length === 1 && !doubt.length ? (saved[0] === 'sleep' ? '\nSend your steps too when you have them.' : '\nSend your sleep too when you have it.') : '';
  if (doubt.length) more += '\nThe ' + doubt.join(' and ') + ' looked off, so I left it out.';
  return say('sleep', 'Saved: ' + parts.join(', ') + '.' + more, [['Undo', 'undo:sleep:' + ids.join(',')]]);
}

// ---------- targets (sent by the Mac from body/targets.json) ----------

function targets() { try { return JSON.parse(prop('TARGETS') || '{}') || {}; } catch (e) { return {}; } }
function round(v, dec) { var f = Math.pow(10, dec); return Math.round(v * f) / f; }
function fmt(v, dec) {
  var s = round(v, dec).toFixed(dec);
  if (s.indexOf('.') >= 0) s = s.replace(/\.?0+$/, '');
  var p = s.split('.');
  p[0] = p[0].replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return p.join('.');
}
function pct(v, t) { return t > 0 ? Math.round(v / t * 100) + '%' : ''; }
// "Protein 45 / 130 g · 35% · 85 g to go"
function progress(label, v, unit, dec, tg) {
  if (!tg || !(tg.target > 0)) return label + ' ' + fmt(v, dec) + ' ' + unit;
  var line = label + ' ' + fmt(v, dec) + ' / ' + fmt(tg.target, dec) + ' ' + unit + ' · ' + pct(v, tg.target), gap = tg.target - v;
  if (tg.dir === 'below') line += gap >= 0 ? ' · ' + fmt(gap, dec) + ' ' + unit + ' left' : ' · ' + fmt(-gap, dec) + ' ' + unit + ' over the limit';
  else if (tg.dir === 'above') line += gap > 0 ? ' · ' + fmt(gap, dec) + ' ' + unit + ' to go' : ' · reached';
  return line;
}

// ---------- Meals ----------

function pending() { try { return JSON.parse(prop('PENDING_MEAL') || 'null'); } catch (e) { return null; } } // the old draft flow
function lastMeal() {
  try {
    var l = JSON.parse(prop('LAST_MEAL') || 'null');
    return l && now().getTime() - l.at < FIX_HOURS * 3600000 ? l : null;
  } catch (e) { return null; }
}
function okNum(v, max) { return typeof v === 'number' && isFinite(v) && v >= 0 && v <= max; }
function cleanMeals(meals) {
  var byMeal = {};
  (meals || []).forEach(function (g) { // one group per meal, so a message is at most four rows (and the Undo button fits)
    var k = MEALS.indexOf(g && g.meal) >= 0 ? g.meal : 'snack';
    byMeal[k] = (byMeal[k] || []).concat((g && g.items) || []);
  });
  return Object.keys(byMeal).map(function (k) {
    return { meal: k, items: byMeal[k].filter(function (it) {
      return it && it.name && okNum(it.kcal, 5000) && okNum(it.protein_g, 500) && okNum(it.fat_g, 500) && okNum(it.carb_g, 1500);
    }).map(function (it) {
      var o = { name: String(it.name).slice(0, 80), amount: String(it.amount || '').slice(0, 40) };
      NUT.forEach(function (n) { o[n[0]] = okNum(it[n[0]], n[4]) ? round(it[n[0]], n[3] + 1) : 0; }); // a strange minor number counts as 0, not the whole dish
      return o;
    }) };
  }).filter(function (g) { return g.items.length; }).sort(function (a, b) { return MEALS.indexOf(a.meal) - MEALS.indexOf(b.meal); });
}
function total(items, k) { return items.reduce(function (s, it) { return s + (Number(it[k]) || 0); }, 0); }
function dayTotals(date) {
  var all = rows('meals').filter(function (r) {
    var d = r.date instanceof Date ? Utilities.formatDate(r.date, tz(), 'yyyy-MM-dd') : String(r.date);
    return d === date && r.deleted !== true && r.deleted !== 'TRUE';
  });
  var out = { meals: all.length };
  NUT.forEach(function (n) { out[n[0]] = total(all, n[0]); });
  return out;
}
function mealText(head, d) {
  var all = [], lines = [head + ' ' + dayWord(d.date)], tg = targets(), has = NUT.some(function (n) { return tg[deskKey(n)]; });
  d.meals.forEach(function (g) {
    lines.push('', g.meal.charAt(0).toUpperCase() + g.meal.slice(1));
    g.items.forEach(function (it) { all.push(it); lines.push('• ' + it.name + (it.amount ? ' (' + it.amount + ')' : '') + ' — ' + fmt(it.kcal, 0) + ' kcal'); });
  });
  lines.push('', has ? 'This message (share of your daily target)' : 'This message');
  NUT.forEach(function (n) {
    var v = total(all, n[0]), t = tg[deskKey(n)];
    lines.push(n[1] + ' ' + fmt(v, n[3]) + ' ' + n[2] + (t && t.target > 0 ? ' (' + pct(v, t.target) + ')' : ''));
  });
  var day = dayTotals(d.date);
  lines.push('', dayWord(d.date) + ' so far, ' + day.meals + (day.meals === 1 ? ' meal' : ' meals') + (has ? ' (total / target)' : ''));
  NUT.forEach(function (n) { lines.push(progress(n[1], day[n[0]], n[2], n[3], tg[deskKey(n)])); });
  if (!has) lines.push('Your daily targets come from the Desk once a day.');
  lines.push('', 'These are estimates. Send a fix ("the rice was half") or press Undo.');
  return lines.join('\n');
}
function onMeal(text, image) {
  var old = pending();
  if (old) saveMeal(old, 'Saved:'); // a draft from before meals were saved at once
  var last = lastMeal();
  if (!image && (YES.test(text))) return say('meals', last || old ? 'It\'s already saved.' : 'Send a photo or tell me what you ate.');
  if (!image && CANCEL.test(text)) {
    if (!last) return say('meals', 'There\'s nothing recent to remove. Use the Undo button under a meal.');
    undoRows('meals', last.ids);
    setProp('LAST_MEAL', '');
    return say('meals', 'Removed the last meal. Nothing from it is saved now.');
  }
  if (!text && !image) return say('meals', 'Send a photo or tell me what you ate.');
  var r = readMeal(last, text, image);
  if (!r.food && !image) return say('meals', 'That doesn\'t look like food. Send a photo or tell me what you ate.');
  var meals = cleanMeals(r.meals);
  if (!meals.length) return say('meals', 'I couldn\'t find any food in that. Try again with a photo or a few words.');
  var fix = r.change === 'fix' && last;
  var d = { date: okDate(r.date) ? r.date : (fix ? last.date : localDate()), meals: meals, texts: (fix ? last.texts || [] : []).concat(text ? [text.slice(0, 300)] : []).slice(-10) };
  if (fix) undoRows('meals', last.ids);
  return saveMeal(d, fix ? 'Fixed and saved:' : 'Saved:');
}
function saveMeal(d, head) {
  var at = now().toISOString(), ids = [];
  d.meals.forEach(function (g) {
    var id = newId(), row = { id: id, date: d.date, meal: g.meal, text: (d.texts || []).join(' / ').slice(0, 500), saved_at: at,
      items: g.items.map(function (it) { return it.name + (it.amount ? ' (' + it.amount + ')' : ''); }).join('; ') };
    NUT.forEach(function (n) { row[n[0]] = round(total(g.items, n[0]), n[3] + 1); });
    append('meals', row);
    ids.push(id);
  });
  setProp('PENDING_MEAL', '');
  setProp('LAST_MEAL', JSON.stringify({ ids: ids, date: d.date, meals: d.meals, texts: d.texts || [], at: now().getTime() }));
  return say('meals', mealText(head || 'Saved:', d), [['Undo', 'undo:meals:' + ids.join(',')]]);
}
function undoRows(kind, ids) {
  var n = 0;
  rows(kind).forEach(function (r) {
    if (ids.indexOf(String(r.id)) >= 0 && r.deleted !== true && r.deleted !== 'TRUE') {
      setCell(kind, r._row, 'deleted', true);
      setCell(kind, r._row, 'imported_at', ''); // the Desk takes the removal on its next import
      n++;
    }
  });
  return n;
}

// ---------- buttons ----------

function onButton(bot, q) {
  var data = q.data || '', reply = '';
  var p = data.split(':');
  if (p[0] === 'undo' && SHEETS[p[1]] && (p[1] === bot)) {
    var want = (p[2] || '').split(','), n = undoRows(p[1], want);
    var last = lastMeal();
    if (bot === 'meals' && last && last.ids.join(',') === want.join(',')) setProp('LAST_MEAL', '');
    reply = n ? 'Undone' : 'Already undone';
    if (n) tg(bot, 'editMessageReplyMarkup', { chat_id: q.message.chat.id, message_id: q.message.message_id, reply_markup: { inline_keyboard: [] } });
    if (n) say(bot, 'Undone. ' + (bot === 'sleep' ? 'Send it again when you\'re ready.' : 'Nothing from that is saved now.'));
  } else if ((p[0] === 'save' || p[0] === 'cancel') && bot === 'meals') {
    var d = pending();
    if (!d || d.v !== p[1]) reply = 'That draft is no longer open';
    else {
      tg(bot, 'editMessageReplyMarkup', { chat_id: q.message.chat.id, message_id: q.message.message_id, reply_markup: { inline_keyboard: [] } });
      if (p[0] === 'save') saveMeal(d, 'Saved:');
      else { setProp('PENDING_MEAL', ''); say(bot, 'Canceled. Nothing was saved.'); }
      reply = p[0] === 'save' ? 'Saved' : 'Canceled';
    }
  }
  tg(bot, 'answerCallbackQuery', { callback_query_id: q.id, text: reply });
}

// ---------- 4:00 ----------

function maybeAsk() {
  if (!prop('OWNER')) return;
  var t = localTime(), d = localDate();
  if (t < ASK_FROM || t >= ASK_UNTIL) return;
  BOTS.forEach(function (bot) {
    if (!prop(TOKEN[bot]) || prop('ASKED_' + bot) === d) return;
    setProp('ASKED_' + bot, d);
    say(bot, QUESTION[bot]);
  });
}

// ---------- the Desk (web app) ----------

// {"protein_g":[130,"above"], ...} from the Desk; only known keys and plain numbers are kept
function setTargets(raw) {
  var given; try { given = JSON.parse(raw); } catch (e) { return; }
  var keep = NUT.map(deskKey).concat(['steps', 'total_min']), out = {};
  keep.forEach(function (k) {
    var t = given && given[k];
    if (t && typeof t[0] === 'number' && isFinite(t[0]) && t[0] > 0 && ['above', 'below', 'near'].indexOf(t[1]) >= 0) out[k] = { target: t[0], dir: t[1] };
  });
  setProp('TARGETS', JSON.stringify(out));
  showSetting('targets', Object.keys(out).length + ' targets from the Desk');
}

function same(a, b) {
  if (!a || !b || a.length !== b.length) return false;
  var x = 0;
  for (var i = 0; i < a.length; i++) x |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return x === 0;
}
function json(obj) { return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON); }

function doGet(e) {
  var q = (e && e.parameter) || {};
  if (!same(q.key || '', prop('DESK_KEY'))) return json({ ok: false });
  var lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    if (q.op === 'tz') {
      // the Mac's clock follows where it is. A pin or /tz from the owner stays until the Mac's zone changes
      if (validTz(q.tz) && (q.tz !== prop('MAC_TZ') || prop('TZ_FROM') === 'mac' || !prop('TZ'))) {
        setProp('MAC_TZ', q.tz);
        if (q.tz !== prop('TZ') || prop('TZ_FROM') !== 'mac') setTz(q.tz, 'mac');
      }
      if (q.targets) setTargets(q.targets);
      return json({ ok: true, tz: tz(), from: prop('TZ_FROM') || 'default', targets: Object.keys(targets()).length });
    }
    if (q.op === 'export') {
      var out = { ok: true, tz: tz() };
      ['sleep', 'meals'].forEach(function (k) {
        out[k] = rows(k).filter(function (r) { return r.imported_at === '' || r.imported_at === null; }).map(function (r) {
          var o = {};
          SHEETS[k].cols.forEach(function (c) { o[c] = r[c] instanceof Date ? Utilities.formatDate(r[c], tz(), c === 'date' ? 'yyyy-MM-dd' : 'HH:mm') : r[c]; });
          o.deleted = o.deleted === true || o.deleted === 'TRUE';
          return o;
        });
      });
      return json(out);
    }
    if (q.op === 'ack') {
      var want = String(q.ids || '').split(',').filter(String), stamp = now().toISOString(), n = 0;
      ['sleep', 'meals'].forEach(function (k) {
        rows(k).forEach(function (r) { if (want.indexOf(String(r.id)) >= 0 && !r.imported_at) { setCell(k, r._row, 'imported_at', stamp); n++; } });
      });
      return json({ ok: true, acked: n });
    }
    return json({ ok: true, tz: tz() });
  } finally {
    lock.releaseLock();
  }
}

// ---------- once, by hand ----------

function setup() {
  ['sleep', 'meals', 'settings'].forEach(sheet);
  if (!prop('DESK_KEY')) setProp('DESK_KEY', Utilities.getUuid().replace(/-/g, '') + Utilities.getUuid().replace(/-/g, ''));
  BOTS.forEach(function (bot) { tg(bot, 'deleteWebhook', {}); });
  ScriptApp.getProjectTriggers().forEach(function (t) { if (t.getHandlerFunction() === 'tick') ScriptApp.deleteTrigger(t); });
  ScriptApp.newTrigger('tick').timeBased().everyMinutes(1).create();
  showSetting('tz', tz() + ' (from ' + (prop('TZ_FROM') || 'default') + ')');
  var missing = ['SLEEP_TOKEN', 'MEALS_TOKEN', 'ANTHROPIC_KEY'].filter(function (k) { return !prop(k); });
  Logger.log('Done. ' + (missing.length ? 'Still missing in Script Properties: ' + missing.join(', ') + '. ' : '') +
    'Now send /start to both bots. The Desk key is: ' + prop('DESK_KEY'));
}
