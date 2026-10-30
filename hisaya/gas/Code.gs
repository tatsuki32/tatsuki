// お宿 ひさ家 予約ページ（Google Apps Script）
// スプレッドシート「ひさ家 予約管理」の拡張機能 → Apps Script に貼り付けて、ウェブアプリとしてデプロイする。
// お客様の名前・電話・メールはシートに保存せず、宿へのメールにだけ載せる。

const TZ = 'Asia/Tokyo';
const SHEET = { rooms: '空室', plans: 'プラン', settings: '設定', log: '受付ログ' };

function doGet() {
  const s = getSettings_();
  return HtmlService.createHtmlOutputFromFile('Index')
    .setTitle(`ご予約 | ${s['宿名']}`)
    .addMetaTag('viewport', 'width=device-width, initial-scale=1');
}

// 予約ページが最初に読むデータ。個人情報は含まない。
function getData() {
  const s = getSettings_();
  const today = dateKey_(new Date());
  const openDays = {};
  readRows_(SHEET.rooms).forEach((r) => {
    const k = dateKey_(r[0]);
    if (k && k >= today && r[1] !== '') openDays[k] = Math.max(0, Number(r[1]) || 0);
  });
  return {
    inn: {
      name: s['宿名'], area: s['エリア'], address: s['住所'], tel: s['電話'],
      checkin: s['チェックイン'], checkout: s['チェックアウト'],
    },
    status: s['受付'] || '停止中',
    lineUrl: /^https:\/\//.test(s['LINE予約URL'] || '') ? s['LINE予約URL'] : '',
    openDays,
    plans: getPlans_(),
  };
}

// 予約を受け付ける。空室を減らし、宿にメールを送る。
function book(input) {
  const s = getSettings_();
  if (s['受付'] === '停止中') return { ok: false, message: 'ただいまオンラインでの受付を止めています。お電話でご予約ください。' };
  if (input.website) return { ok: true, id: '-' }; // 迷惑投稿よけの隠し欄に入力があれば、何もせず終える

  const p = clean_(input);
  const err = validate_(p);
  if (err) return { ok: false, message: err };

  const plan = getPlans_().find((x) => x.name === p.plan);
  if (!plan || !inSeason_(p.date, plan)) return { ok: false, message: 'このプランは選んだ日にはご用意がありません。' };

  const lock = LockService.getScriptLock();
  lock.waitLock(15000);
  try {
    const sh = sheet_(SHEET.rooms);
    const rows = sh.getDataRange().getValues();
    const i = rows.findIndex((r, n) => n > 0 && dateKey_(r[0]) === p.date);
    if (i < 0) return { ok: false, message: 'この日は営業日ではありません。' };
    const left = Number(rows[i][1]) || 0;
    if (left < p.rooms) {
      return { ok: false, full: true, message: left === 0 ? '申し訳ありません。この日はたった今満室になりました。' : `この日の残りは ${left} 室です。お部屋の数を減らしてお申し込みください。` };
    }

    const id = Utilities.formatDate(new Date(), TZ, 'MMdd-HHmmss');
    sendInnMail_(s, p, plan, id, left - p.rooms);
    sh.getRange(i + 1, 2).setValue(left - p.rooms);
    sheet_(SHEET.log).appendRow([new Date(), id, p.date, plan.name, p.adults, p.rooms]);
    SpreadsheetApp.flush();

    if (s['お客様への自動返信'] === 'する') {
      try { sendGuestMail_(s, p, plan, id); } catch (e) { console.warn('自動返信に失敗', e); }
    }
    return { ok: true, id };
  } finally {
    lock.releaseLock();
  }
}

function sendInnMail_(s, p, plan, id, left) {
  const to = s['通知先メール'] || Session.getEffectiveUser().getEmail();
  const body = [
    `${s['宿名']} のウェブサイトから予約の申し込みがありました。`,
    'お客様に確定のご連絡をしてください。',
    '',
    `受付番号：${id}`,
    `宿泊日：${p.date}（1泊）`,
    `プラン：${plan.name}`,
    `人数：大人 ${p.adults} 名・${p.rooms} 室`,
    `料金の目安：${plan.price ? (plan.price * p.adults).toLocaleString('ja-JP') + '円〜' : '要確認'}`,
    `到着予定：${p.arrival}`,
    '',
    `お名前：${p.name}`,
    `電話：${p.tel}`,
    `メール：${p.email}`,
    `ご要望：${p.notes || 'なし'}`,
    '',
    `この日の残り部屋数は ${left} 室になりました（自動で減らしています）。`,
    'じゃらん・楽天の在庫も忘れずに減らしてください。',
    s['受付'] === 'テスト中' ? '\n※ テスト中の申し込みです。' : '',
  ].join('\n');
  MailApp.sendEmail({
    to,
    replyTo: p.email,
    subject: `【予約申込】${p.date} ${p.name} 様 ${p.adults}名`,
    body,
  });
}

function sendGuestMail_(s, p, plan, id) {
  const body = [
    `${p.name} 様`,
    '',
    `${s['宿名']} へのお申し込みをありがとうございます。`,
    '以下の内容で受け付けました。宿から確定のご連絡をもって、ご予約の成立となります。',
    '',
    `受付番号：${id}`,
    `宿泊日：${p.date}（1泊）`,
    `プラン：${plan.name}`,
    `人数：大人 ${p.adults} 名・${p.rooms} 室`,
    `チェックイン：${s['チェックイン']}　チェックアウト：${s['チェックアウト']}`,
    '',
    `${s['宿名']}　${s['住所']}　TEL ${s['電話']}`,
  ].join('\n');
  MailApp.sendEmail({ to: p.email, subject: `【${s['宿名']}】お申し込みを受け付けました`, body, name: s['宿名'] });
}

function clean_(x) {
  const str = (v, max) => String(v == null ? '' : v).replace(/[\u0000-\u0009\u000B-\u001F]/g, '').trim().slice(0, max);
  return {
    date: str(x.date, 10),
    plan: str(x.plan, 100),
    adults: Math.floor(Number(x.adults)),
    rooms: Math.floor(Number(x.rooms)),
    name: str(x.name, 60).replace(/\n/g, ' '),
    tel: str(x.tel, 20),
    email: str(x.email, 120),
    arrival: str(x.arrival, 20),
    notes: str(x.notes, 500),
  };
}

function validate_(p) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(p.date) || p.date < dateKey_(new Date())) return '宿泊日を選び直してください。';
  if (!(p.adults >= 1 && p.adults <= 20)) return '人数を選び直してください。';
  if (!(p.rooms >= 1 && p.rooms <= 10)) return 'お部屋の数を選び直してください。';
  if (!p.name) return 'お名前を入力してください。';
  if ((p.tel.match(/[0-9０-９]/g) || []).length < 10) return '電話番号を確認してください。';
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(p.email)) return 'メールアドレスを確認してください。';
  return '';
}

function getPlans_() {
  return readRows_(SHEET.plans)
    .filter((r) => r[0] && r[8] !== '×')
    .map((r) => ({
      name: String(r[0]),
      meal: String(r[1]),
      desc: String(r[2]),
      price: Number(r[3]) || null,
      priceNote: String(r[4]),
      season: { from: monthDay_(r[5]) || '01-01', to: monthDay_(r[6]) || '12-31' },
      tags: String(r[7]).split(/[、,]/).map((t) => t.trim()).filter(Boolean),
    }));
}

// 月-日の期間（年またぎ可）に入っているか
function inSeason_(date, plan) {
  const md = date.slice(5);
  const s = plan.season;
  return s.from <= s.to ? md >= s.from && md <= s.to : md >= s.from || md <= s.to;
}

function getSettings_() {
  const s = {};
  readRows_(SHEET.settings).forEach((r) => { if (r[0]) s[String(r[0]).trim()] = String(r[1]).trim(); });
  return s;
}

function sheet_(name) {
  const sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(name);
  if (!sh) throw new Error(`シート「${name}」が見つかりません`);
  return sh;
}

function readRows_(name) {
  return sheet_(name).getDataRange().getValues().slice(1);
}

// シートの日付（Date でも文字列でも）を yyyy-MM-dd にそろえる
function dateKey_(v) {
  if (v instanceof Date) return Utilities.formatDate(v, TZ, 'yyyy-MM-dd');
  const m = String(v).trim().match(/^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$/);
  return m ? `${m[1]}-${m[2].padStart(2, '0')}-${m[3].padStart(2, '0')}` : '';
}

// 「11-06」「11/6」や日付セルを MM-dd にそろえる
function monthDay_(v) {
  if (v instanceof Date) return Utilities.formatDate(v, TZ, 'MM-dd');
  const m = String(v).trim().match(/^(\d{1,2})[-/](\d{1,2})$/);
  return m ? `${m[1].padStart(2, '0')}-${m[2].padStart(2, '0')}` : '';
}
