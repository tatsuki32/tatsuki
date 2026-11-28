// Runs Code.js in a fake Apps Script: a sheet in memory, Script Properties in a Map, Telegram and Claude answered by the test.
// node --test lifeos/22-morning-bots/test/
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const CODE = fs.readFileSync(path.join(here, '..', 'Code.js'), 'utf8');
const OWNER = 111, STRANGER = 222;

function world({ at = '2026-11-28T06:00:00Z', tz = 'Asia/Tokyo' } = {}) {
  const props = new Map([['SLEEP_TOKEN', 'S'], ['MEALS_TOKEN', 'M'], ['ANTHROPIC_KEY', 'k'], ['DESK_KEY', 'desk-secret']]);
  if (tz) props.set('TZ', tz);
  const sheets = new Map();
  const sent = [];              // {bot, method, payload}
  const updates = { S: [], M: [] };
  const claudeAnswers = [];     // queued JSON answers
  const claudeAsked = [];
  let uid = 1, clock = new Date(at);

  const fmt = (d, zone, f) => {
    const p = Object.fromEntries(new Intl.DateTimeFormat('en-CA', { timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
      .formatToParts(d).map(x => [x.type, x.value]));
    if (f === 'yyyy-MM-dd') return `${p.year}-${p.month}-${p.day}`;
    if (f === 'HH:mm') return `${p.hour}:${p.minute}`;
    throw new Error('format ' + f);
  };
  const sheetObj = name => {
    const data = sheets.get(name);
    return {
      getRange: (r, c, nr = 1, nc = 1) => ({
        setValues: vals => vals.forEach((row, i) => row.forEach((v, j) => { (data[r - 1 + i] ||= [])[c - 1 + j] = v; })),
        setValue: v => { (data[r - 1] ||= [])[c - 1] = v; },
      }),
      setFrozenRows() {},
      appendRow: row => data.push(row.map(v => typeof v === 'string' && v.startsWith("'") ? v.slice(1) : v)),
      getDataRange: () => ({ getValues: () => data.map(r => r.slice()) }),
    };
  };
  const reply = (code, obj) => ({ getResponseCode: () => code, getContentText: () => JSON.stringify(obj), getBlob: () => ({ getBytes: () => [1, 2, 3] }) });
  const ctx = {
    console,
    PropertiesService: { getScriptProperties: () => ({ getProperty: k => props.get(k) ?? null, setProperty: (k, v) => props.set(k, v), deleteProperty: k => props.delete(k) }) },
    SpreadsheetApp: { getActiveSpreadsheet: () => ({ getSheetByName: n => (sheets.has(n) ? sheetObj(n) : null), insertSheet: n => { sheets.set(n, []); return sheetObj(n); } }) },
    Utilities: {
      formatDate: (d, zone, f) => { new Intl.DateTimeFormat('en', { timeZone: zone }); return fmt(d, zone, f); },
      getUuid: () => String(uid++).padStart(8, '0') + '-bbbb-cccc-dddd-eeeeeeeeeeee',
      base64Encode: () => 'AQID',
    },
    LockService: { getScriptLock: () => ({ tryLock: () => true, waitLock() {}, releaseLock() {} }) },
    ContentService: { createTextOutput: t => ({ text: t, setMimeType() { return this; } }), MimeType: { JSON: 'json' } },
    Logger: { log() {} },
    ScriptApp: { getProjectTriggers: () => [], deleteTrigger() {}, newTrigger: () => ({ timeBased: () => ({ everyMinutes: () => ({ create() {} }) }) }) },
    UrlFetchApp: {
      fetch(url, opt = {}) {
        if (url.startsWith('https://api.anthropic.com')) {
          claudeAsked.push(JSON.parse(opt.payload));
          const a = claudeAnswers.shift();
          if (!a) throw new Error('no claude answer queued');
          return reply(200, { stop_reason: 'end_turn', content: [{ type: 'text', text: JSON.stringify(a) }] });
        }
        if (url.startsWith('https://timeapi.io')) return reply(200, { timeZone: 'Europe/Berlin' });
        const m = url.match(/^https:\/\/api\.telegram\.org\/bot(\w+)\/(\w+)$/);
        if (m) {
          const payload = JSON.parse(opt.payload || '{}');
          if (m[2] === 'getUpdates') { const u = updates[m[1]].filter(x => x.update_id >= (payload.offset || 0)); return reply(200, { ok: true, result: u }); }
          sent.push({ bot: m[1] === 'S' ? 'sleep' : 'meals', method: m[2], payload });
          if (m[2] === 'getFile') return reply(200, { ok: true, result: { file_path: 'p/1.jpg' } });
          return reply(200, { ok: true, result: {} });
        }
        if (url.startsWith('https://api.telegram.org/file/')) return reply(200, {});
        throw new Error('unexpected fetch ' + url);
      },
    },
  };
  vm.createContext(ctx);
  vm.runInContext(CODE, ctx);
  ctx.now = () => new Date(clock);
  let upd = 1;
  const api = {
    ctx, props, sheets, sent, claudeAnswers, claudeAsked,
    setClock: iso => { clock = new Date(iso); },
    send(bot, message, from = OWNER) {
      const u = { update_id: upd++, message: { message_id: upd, chat: { id: from, type: 'private' }, from: { id: from }, ...message } };
      updates[bot === 'sleep' ? 'S' : 'M'].push(u);
      return u;
    },
    press(bot, data, from = OWNER) {
      updates[bot === 'sleep' ? 'S' : 'M'].push({ update_id: upd++, callback_query: { id: 'q' + upd, data, from: { id: from }, message: { message_id: 9, chat: { id: from, type: 'private' } } } });
    },
    tick: () => ctx.tick(),
    said: bot => sent.filter(s => s.method === 'sendMessage' && (!bot || s.bot === bot)).map(s => s.payload.text),
    rows: name => (sheets.get(name) || [[]]).slice(1),
  };
  return api;
}

function owned(opts) {
  const w = world(opts);
  w.send('sleep', { text: '/start' });
  w.tick();
  w.sent.length = 0;
  return w;
}

test('the first /start owns the bots; a stranger gets nothing back', () => {
  const w = world();
  w.send('meals', { text: '/start' });
  w.send('meals', { text: '/start' }, STRANGER);
  w.send('meals', { text: 'lunch curry' }, STRANGER);
  w.tick();
  assert.equal(w.props.get('OWNER'), String(OWNER));
  assert.equal(w.sent.filter(s => s.method === 'sendMessage').length, 1);
  assert.equal(w.claudeAsked.length, 0);
});

test('asks once at 4:00 local time, in the owner\'s time zone', () => {
  const w = owned({ at: '2026-11-28T18:59:00Z', tz: 'Asia/Tokyo' }); // 3:59 in Tokyo
  w.tick();
  assert.equal(w.said().length, 0);
  w.setClock('2026-11-28T19:00:00Z'); // 4:00 Tokyo
  w.tick();
  w.tick();
  assert.equal(w.said('sleep').length, 1);
  assert.equal(w.said('meals').length, 1);
  assert.match(w.said('sleep')[0], /sleep/);
  w.setClock('2026-11-29T05:00:00Z'); // 14:00 the same day: no second question
  w.tick();
  assert.equal(w.said().length, 2);
});

test('a missed 4:00 still asks in the morning, never in the afternoon', () => {
  const w = world({ at: '2026-11-28T22:30:00Z', tz: 'Asia/Tokyo' }); // 7:30 Tokyo
  w.send('sleep', { text: '/start' });
  w.tick();
  assert.equal(w.said().length, 3); // the welcome, then both questions
  const v = owned({ at: '2026-11-29T04:30:00Z', tz: 'Asia/Tokyo' }); // 13:30
  v.tick();
  assert.equal(v.said().length, 0);
});

test('sleep and steps are saved at once, with their days, and Undo marks them deleted', () => {
  const w = owned({ at: '2026-11-28T20:00:00Z' }); // 5:00 Nov 29 in Tokyo
  w.claudeAnswers.push({ sleep: { minutes: 450, bedtime: '22:00', wake_time: '05:30', date: '2026-11-29' }, steps: { steps: 8200, date: '2026-11-28' } });
  w.send('sleep', { text: '7時間半 8200歩' });
  w.tick();
  const rows = w.rows('Sleep & Steps');
  assert.equal(rows.length, 2);
  assert.deepEqual(rows.map(r => [r[1], r[2], r[3], r[6]]), [['2026-11-29', 'sleep', 450, ''], ['2026-11-28', 'steps', '', 8200]]);
  const msg = w.sent.find(s => s.method === 'sendMessage' && /Saved/.test(s.payload.text));
  assert.match(msg.payload.text, /7h 30m.*today.*8,200 steps.*yesterday/);
  const undo = msg.payload.reply_markup.inline_keyboard[0][0].callback_data;
  assert.ok(undo.length <= 64);
  w.press('sleep', undo);
  w.tick();
  assert.deepEqual(w.rows('Sleep & Steps').map(r => r[10]), [true, true]);
});

test('a number out of range is not saved', () => {
  const w = owned({ at: '2026-11-28T20:00:00Z' });
  w.claudeAnswers.push({ sleep: { minutes: 5000, bedtime: null, wake_time: null, date: '2026-11-29' }, steps: null });
  w.send('sleep', { text: '5000 minutes' });
  w.tick();
  assert.equal(w.rows('Sleep & Steps').length, 0);
  assert.match(w.said('sleep').at(-1), /looks off/);
});

const NUTS = { kcal: 750, protein_g: 20, fat_g: 25, saturated_fat_g: 9, carb_g: 110, fiber_g: 4, salt_g: 3, calcium_mg: 60, magnesium_mg: 50,
  iron_mg: 1.5, zinc_mg: 2.4, vitamin_a_ug: 120, vitamin_d_ug: 0.2, vitamin_b1_mg: 0.15, vitamin_b2_mg: 0.12, vitamin_b6_mg: 0.3, vitamin_c_mg: 8 };
const CURRY = { name: 'Curry rice', amount: '1 plate', ...NUTS };
const MEAL = (items, change = 'new', meal = 'lunch') => ({ food: true, change, date: '2026-11-28', meals: [{ meal, items }] });
const TARGETS = JSON.stringify({ energy_kcal: [1850, 'below'], protein_g: [130, 'above'], salt_g: [6, 'below'], vitamin_c_mg: [300, 'above'], steps: [15000, 'above'], total_min: [480, 'above'], bogus: [1, 'above'] });
const byHead = rows => { const [h, ...r] = rows; return r.map(x => Object.fromEntries(h.map((k, i) => [k, x[i] ?? '']))); };

test('a meal is saved at once with all 17 nutrients, its share of each target and the day so far', () => {
  const w = owned({ at: '2026-11-28T20:00:00Z' }); // 5:00 in Tokyo: the default day is yesterday
  w.ctx.doGet({ parameter: { key: 'desk-secret', op: 'tz', tz: 'Asia/Tokyo', targets: TARGETS } });
  assert.deepEqual(Object.keys(JSON.parse(w.props.get('TARGETS'))).sort(), ['energy_kcal', 'protein_g', 'salt_g', 'steps', 'total_min', 'vitamin_c_mg']);
  w.claudeAnswers.push(MEAL([CURRY]));
  w.send('meals', { text: '昼 カレー' });
  w.tick();
  const rows = byHead(w.sheets.get('Meals'));
  assert.equal(rows.length, 1);
  assert.equal(rows[0].date, '2026-11-28');
  assert.equal(rows[0].items, 'Curry rice (1 plate)');
  assert.equal(rows[0].kcal, 750);
  assert.equal(rows[0].vitamin_b1_mg, 0.15);
  assert.equal(rows[0].iron_mg, 1.5);
  const text = w.said('meals').at(-1);
  assert.match(text, /^Saved: Nov 28 \(yesterday\)/);
  assert.match(text, /Curry rice \(1 plate\) — 750 kcal/);
  assert.match(text, /Energy 750 kcal \(41%\)/);
  assert.match(text, /Vitamin B6 0\.3 mg\n/);
  assert.match(text, /Energy 750 \/ 1,850 kcal · 41% · 1,100 kcal left/);
  assert.match(text, /Protein 20 \/ 130 g · 15% · 110 g to go/);
  for (const label of ['Saturated fat', 'Fiber', 'Calcium', 'Magnesium', 'Iron', 'Zinc', 'Vitamin A', 'Vitamin D', 'Vitamin B1', 'Vitamin B2', 'Vitamin C']) assert.match(text, new RegExp(label + ' '));
  assert.match(w.claudeAsked[0].system, /vitamin_b6_mg/);
  assert.deepEqual(w.claudeAsked[0].output_config.format.schema.properties.meals.items.properties.items.items.required.length, 19);
});

test('a fix replaces the last meal; new food adds to the day; salt over the limit says so', () => {
  const w = owned({ at: '2026-11-28T20:00:00Z' });
  w.ctx.doGet({ parameter: { key: 'desk-secret', op: 'tz', tz: 'Asia/Tokyo', targets: TARGETS } });
  w.claudeAnswers.push(MEAL([CURRY]));
  w.send('meals', { text: 'lunch curry' });
  w.tick();
  w.claudeAnswers.push(MEAL([{ ...CURRY, amount: 'half rice', kcal: 600 }], 'fix'));
  w.send('meals', { text: 'ご飯は半分' });
  w.tick();
  assert.match(w.claudeAsked[1].messages[0].content.at(-1).text, /Last saved meal:[\s\S]*Curry rice/);
  let rows = byHead(w.sheets.get('Meals'));
  assert.deepEqual(rows.map(r => [r.kcal, r.deleted]), [[750, true], [600, '']]);
  assert.match(w.said('meals').at(-1), /^Fixed and saved:[\s\S]*so far, 1 meal[\s\S]*Energy 600 \/ 1,850/);

  w.claudeAnswers.push(MEAL([{ ...CURRY, name: 'Ramen', amount: '1 bowl', kcal: 500, salt_g: 5 }], 'new', 'dinner'));
  w.send('meals', { text: '夜 ラーメン' });
  w.tick();
  rows = byHead(w.sheets.get('Meals'));
  assert.equal(rows.filter(r => r.deleted !== true).length, 2);
  const text = w.said('meals').at(-1);
  assert.match(text, /so far, 2 meals/);
  assert.match(text, /Energy 1,100 \/ 1,850 kcal · 59% · 750 kcal left/);
  assert.match(text, /Salt 8 \/ 6 g · 133% · 2 g over the limit/);
});

test('Undo and cancel remove a meal; yes needs no draft any more', () => {
  const w = owned({ at: '2026-11-28T20:00:00Z' });
  w.claudeAnswers.push(MEAL([CURRY]));
  w.send('meals', { text: 'curry' });
  w.tick();
  const undo = w.sent.filter(s => s.method === 'sendMessage').at(-1).payload.reply_markup.inline_keyboard[0][0].callback_data;
  w.press('meals', undo);
  w.tick();
  assert.equal(byHead(w.sheets.get('Meals'))[0].deleted, true);
  assert.equal(w.props.has('LAST_MEAL'), false);
  w.claudeAnswers.push(MEAL([CURRY]));
  w.send('meals', { text: 'curry again' });
  w.send('meals', { text: 'はい' });
  w.send('meals', { text: 'cancel' });
  w.tick();
  assert.match(w.said('meals').at(-2), /already saved/);
  assert.match(w.said('meals').at(-1), /Removed the last meal/);
  assert.deepEqual(byHead(w.sheets.get('Meals')).map(r => r.deleted), [true, true]);
});

test('a draft left from the old flow is saved on the next minute', () => {
  const w = owned({ at: '2026-11-28T20:00:00Z' });
  w.props.set('PENDING_MEAL', JSON.stringify({ v: 'rx', date: '2026-11-28', meals: [{ meal: 'lunch', items: [{ name: 'Pho', amount: '1 bowl', kcal: 450, protein_g: 25, fat_g: 8, carb_g: 60, salt_g: 4 }] }], texts: ['pho'] }));
  w.tick();
  const rows = byHead(w.sheets.get('Meals'));
  assert.deepEqual([rows[0].items, rows[0].kcal, rows[0].fiber_g], ['Pho (1 bowl)', 450, 0]);
  assert.equal(w.props.has('PENDING_MEAL'), false);
  assert.match(w.said('meals').at(-1), /^Saved:/);
});

test('an older Meals sheet gets the new nutrient columns at its right end', () => {
  const w = owned({ at: '2026-11-28T20:00:00Z' });
  const old = ['id', 'date', 'meal', 'items', 'kcal', 'protein_g', 'fat_g', 'carb_g', 'salt_g', 'text', 'saved_at', 'imported_at', 'deleted'];
  w.sheets.set('Meals', [old.slice(), ['rold', '2026-11-28', 'breakfast', 'Toast', 200, 6, 3, 35, 0.8, 'toast', 'x', '', '']]);
  w.claudeAnswers.push(MEAL([CURRY]));
  w.send('meals', { text: 'curry' });
  w.tick();
  const head = w.sheets.get('Meals')[0];
  assert.deepEqual(head.slice(0, 13), old);
  assert.equal(head.length, 25);
  assert.match(w.said('meals').at(-1), /so far, 2 meals[\s\S]*Energy 950 kcal/);
});

test('a photo goes to Claude as an image', () => {
  const w = owned({ at: '2026-11-28T20:00:00Z' });
  w.claudeAnswers.push(MEAL([CURRY]));
  w.send('meals', { photo: [{ file_id: 'a', width: 320, height: 240 }, { file_id: 'b', width: 1280, height: 960 }, { file_id: 'c', width: 2560, height: 1920 }] });
  w.tick();
  assert.equal(w.sent.find(s => s.method === 'getFile').payload.file_id, 'b');
  assert.equal(w.claudeAsked[0].messages[0].content[0].type, 'image');
});

test('time zone: /tz, a location pin, and the Mac', () => {
  const w = owned();
  w.send('sleep', { text: '/tz Not/AZone' });
  w.send('sleep', { text: '/tz America/New_York' });
  w.tick();
  assert.equal(w.props.get('TZ'), 'America/New_York');
  w.send('meals', { location: { latitude: 52.52, longitude: 13.4 } });
  w.tick();
  assert.equal(w.props.get('TZ'), 'Europe/Berlin');
  assert.ok(w.said('meals').some(t => /didn't keep the location/.test(t)));
  // the Mac keeps saying where it was before: the pin stays
  w.props.set('MAC_TZ', 'Asia/Tokyo');
  JSON.parse(w.ctx.doGet({ parameter: { key: 'desk-secret', op: 'tz', tz: 'Asia/Tokyo' } }).text);
  assert.equal(w.props.get('TZ'), 'Europe/Berlin');
  // the Mac moved: it wins
  const r = JSON.parse(w.ctx.doGet({ parameter: { key: 'desk-secret', op: 'tz', tz: 'Asia/Bangkok' } }).text);
  assert.equal(r.tz, 'Asia/Bangkok');
});

test('the Desk: a wrong key gets nothing; export then ack', () => {
  const w = owned({ at: '2026-11-28T20:00:00Z' });
  w.claudeAnswers.push({ sleep: { minutes: 420, bedtime: null, wake_time: null, date: '2026-11-29' }, steps: null });
  w.send('sleep', { text: '7h' });
  w.tick();
  assert.deepEqual(JSON.parse(w.ctx.doGet({ parameter: { key: 'nope', op: 'export' } }).text), { ok: false });
  const ex = JSON.parse(w.ctx.doGet({ parameter: { key: 'desk-secret', op: 'export' } }).text);
  assert.equal(ex.sleep.length, 1);
  assert.equal(ex.sleep[0].sleep_min, 420);
  assert.equal(ex.sleep[0].deleted, false);
  JSON.parse(w.ctx.doGet({ parameter: { key: 'desk-secret', op: 'ack', ids: ex.sleep[0].id } }).text);
  assert.equal(JSON.parse(w.ctx.doGet({ parameter: { key: 'desk-secret', op: 'export' } }).text).sleep.length, 0);
});
