/**
 * Meta インサイト取得（Google スプレッドシートの Apps Script）
 *
 * 1つのスプレッドシート＝1つのクライアント（Facebook ページ1つ＋Instagram 1つ）。
 * 毎朝、前日までの数字を Meta Graph API から読んで、シートに書く。読むだけで、投稿や変更はしない。
 *
 * 置くもの
 *   - シート「設定」：ページ ID・Instagram ID・指標の一覧（初回の setup で作る）
 *   - スクリプト プロパティ META_TOKEN：システムユーザーの鍵（メニュー「鍵を設定」で入れる）
 * 鍵はシートにもコードにも書かない。シートをクライアントに渡すときは「閲覧者」で共有する。
 */

const SHEETS = {
  config: '設定',
  summary: '概要',
  igDaily: 'IG_日別',
  igPosts: 'IG_投稿',
  fbDaily: 'FB_日別',
  fbPosts: 'FB_投稿',
  log: 'ログ',
};

const DEFAULT_CONFIG = [
  ['クライアント名', '', 'シートの表示用'],
  ['FacebookページID', '', 'ビジネス設定 → ページ の ID'],
  ['InstagramID', '', 'ビジネス設定 → Instagramアカウント の ID（17841… で始まる）'],
  ['APIバージョン', '', '空欄ならアプリの既定のバージョン。例：v24.0'],
  ['IG日別の指標', 'reach,views,accounts_engaged,total_interactions,likes,comments,shares,saves,profile_views', 'Instagram の1日ごとの合計（metric_type=total_value）。取れない指標は空欄になり、ログに残る'],
  ['IG日別の指標（時系列）', 'follower_count', '1日ごとの値（follower_count は直近30日まで）'],
  ['IG投稿の指標', 'reach,views,saved,shares,total_interactions', '投稿ごと'],
  ['IGリールの追加指標', 'ig_reels_avg_watch_time,ig_reels_video_view_total_time', 'リールだけに足す（ミリ秒）'],
  ['FB日別の指標', 'page_follows,page_media_view,page_total_media_view_unique,page_post_engagements,page_views_total', 'Facebook ページの1日ごと。取れない指標は空欄'],
  ['FB投稿の指標', '', '投稿ごとのインサイト（空欄なら、リアクション・コメント・シェアの数だけ）'],
  ['投稿の取得件数', '50', '新しい順に何件まで読むか'],
  ['毎日の取り直し日数', '3', '数字が後から確定するので、直近何日を毎回取り直すか'],
];

/* ---------- メニュー ---------- */

function onOpen() {
  SpreadsheetApp.getUi().createMenu('インサイト')
    .addItem('① 初期設定（シートを作る）', 'setup')
    .addItem('② 鍵を設定', 'setToken')
    .addItem('③ 過去30日を取り込む', 'backfill30')
    .addItem('④ 毎朝の自動取得をオン', 'installTrigger')
    .addSeparator()
    .addItem('今すぐ取得', 'runDaily')
    .addItem('接続を確かめる', 'checkConnection')
    .addItem('毎朝の自動取得をオフ', 'removeTrigger')
    .addToUi();
}

function setup() {
  const ss = SpreadsheetApp.getActive();
  if (ss.getSpreadsheetTimeZone() !== 'Asia/Tokyo') ss.setSpreadsheetTimeZone('Asia/Tokyo');
  let cfg = ss.getSheetByName(SHEETS.config);
  if (!cfg) {
    cfg = ss.insertSheet(SHEETS.config);
    cfg.getRange(1, 1, 1, 3).setValues([['項目', '値', '説明']]).setFontWeight('bold');
    cfg.getRange(2, 1, DEFAULT_CONFIG.length, 3).setValues(DEFAULT_CONFIG);
    cfg.setColumnWidth(1, 200).setColumnWidth(2, 420).setColumnWidth(3, 420);
    cfg.setFrozenRows(1);
  } else {
    // 足りない項目だけ足す（本人が直した値は消さない）
    const have = cfg.getRange(1, 1, cfg.getLastRow(), 1).getValues().map(r => r[0]);
    DEFAULT_CONFIG.filter(r => have.indexOf(r[0]) < 0).forEach(r => cfg.appendRow(r));
  }
  ensureSheet_(SHEETS.igDaily, ['日付']);
  ensureSheet_(SHEETS.igPosts, ['投稿日時', '種類', 'キャプション', 'リンク', 'いいね', 'コメント', '投稿ID']);
  ensureSheet_(SHEETS.fbDaily, ['日付']);
  ensureSheet_(SHEETS.fbPosts, ['投稿日時', '本文', 'リンク', 'リアクション', 'コメント', 'シェア', '投稿ID']);
  ensureSheet_(SHEETS.log, ['日時', '内容']);
  buildSummary_();
  // 最初からある空のシート（シート1・Sheet1）は消す
  ['シート1', 'Sheet1'].forEach(n => {
    const s = ss.getSheetByName(n);
    if (s && s.getLastRow() === 0 && ss.getSheets().length > 1) ss.deleteSheet(s);
  });
  ss.setActiveSheet(ss.getSheetByName(SHEETS.config));
  SpreadsheetApp.getUi().alert('シートを作りました。「設定」のページ ID と Instagram ID を確かめてから、②「鍵を設定」へ進んでください。');
}

function setToken() {
  const ui = SpreadsheetApp.getUi();
  const res = ui.prompt('鍵を設定', 'Meta のシステムユーザーで作った鍵（アクセストークン）を貼ってください。\nシートには保存されず、このスクリプトの中にだけ残ります。', ui.ButtonSet.OK_CANCEL);
  if (res.getSelectedButton() !== ui.Button.OK) return;
  const t = res.getResponseText().trim();
  if (!t) return;
  PropertiesService.getScriptProperties().setProperty('META_TOKEN', t);
  PropertiesService.getScriptProperties().deleteProperty('PAGE_TOKEN');
  ui.alert('鍵を保存しました。「接続を確かめる」で読めるか確かめられます。');
}

function installTrigger() {
  removeTrigger(true);
  ScriptApp.newTrigger('runDaily').timeBased().everyDays(1).atHour(6).inTimezone('Asia/Tokyo').create();
  SpreadsheetApp.getUi().alert('毎朝6時（日本時間）ごろに自動で取得します。');
}

function removeTrigger(quiet) {
  ScriptApp.getProjectTriggers().filter(t => t.getHandlerFunction() === 'runDaily').forEach(t => ScriptApp.deleteTrigger(t));
  if (quiet !== true) SpreadsheetApp.getUi().alert('自動取得を止めました。');
}

function checkConnection() {
  const c = config_();
  const lines = [];
  try {
    const ig = graph_(c.igId, { fields: 'username,followers_count,media_count' });
    lines.push('Instagram：@' + ig.username + '（フォロワー ' + ig.followers_count + '）');
  } catch (e) { lines.push('Instagram：読めません → ' + e.message); }
  try {
    const p = graph_(c.pageId, { fields: 'name,followers_count' }, pageToken_());
    lines.push('Facebook：' + p.name + '（フォロワー ' + p.followers_count + '）');
  } catch (e) { lines.push('Facebook：読めません → ' + e.message); }
  SpreadsheetApp.getUi().alert(lines.join('\n'));
}

/* ---------- 取得 ---------- */

function runDaily() {
  const c = config_();
  const days = [];
  for (let i = c.redoDays; i >= 1; i--) days.push(daysAgo_(i));
  fetchDays_(days);
  fetchIgPosts_();
  fetchFbPosts_();
  log_('毎日の取得：' + days[0] + '〜' + days[days.length - 1]);
}

function backfill30() {
  const days = [];
  for (let i = 30; i >= 1; i--) days.push(daysAgo_(i));
  fetchDays_(days);
  fetchIgPosts_();
  fetchFbPosts_();
  log_('過去30日の取り込み：' + days[0] + '〜' + days[days.length - 1]);
  SpreadsheetApp.getUi().alert('過去30日を取り込みました。取れなかった指標は「ログ」にあります。');
}

function fetchDays_(days) {
  const c = config_();
  if (c.igId) {
    const rows = {};
    days.forEach(d => { rows[d] = igDay_(d); });
    upsertDaily_(SHEETS.igDaily, rows);
  }
  if (c.pageId) {
    const rows = {};
    days.forEach(d => { rows[d] = fbDay_(d); });
    upsertDaily_(SHEETS.fbDaily, rows);
  }
}

function igDay_(day) {
  const c = config_();
  const r = range_(day);
  const out = {};
  const tv = pickMetrics_(c.igId + '/insights', c.igMetrics, { period: 'day', metric_type: 'total_value', since: r.since, until: r.until }, null,
    item => item.total_value ? item.total_value.value : '');
  Object.assign(out, tv);
  const ts = pickMetrics_(c.igId + '/insights', c.igSeries, { period: 'day', since: r.since, until: r.until }, null,
    item => (item.values && item.values.length) ? item.values[0].value : '');
  Object.assign(out, ts);
  return out;
}

function fbDay_(day) {
  const c = config_();
  const r = range_(day);
  return pickMetrics_(c.pageId + '/insights', c.fbMetrics, { period: 'day', since: r.since, until: r.until }, pageToken_(),
    item => (item.values && item.values.length) ? item.values[0].value : '');
}

/** 指標をまとめて頼み、だめなら1つずつ頼む。取れない指標は空欄にしてログへ。 */
function pickMetrics_(path, metrics, params, token, valueOf) {
  const out = {};
  if (!metrics.length) return out;
  metrics.forEach(m => { out[m] = ''; });
  const apply = data => (data || []).forEach(item => {
    let v = valueOf(item);
    if (v !== null && typeof v === 'object') v = sumObject_(v);
    out[item.name] = v;
  });
  try {
    apply(graph_(path, Object.assign({}, params, { metric: metrics.join(',') }), token).data);
    return out;
  } catch (e) {
    metrics.forEach(m => {
      try {
        apply(graph_(path, Object.assign({}, params, { metric: m }), token).data);
      } catch (e2) {
        warnOnce_(path.split('/').pop() + ':' + m, '取れない指標 ' + m + '：' + e2.message);
      }
    });
    return out;
  }
}

function fetchIgPosts_() {
  const c = config_();
  if (!c.igId) return;
  const res = graph_(c.igId + '/media', {
    fields: 'id,timestamp,media_type,media_product_type,caption,permalink,like_count,comments_count',
    limit: Math.min(c.postLimit, 100),
  });
  const media = res.data || [];
  const metricNames = c.igPostMetrics.concat(c.igReelMetrics.filter(m => c.igPostMetrics.indexOf(m) < 0));
  const header = ['投稿日時', '種類', 'キャプション', 'リンク', 'いいね', 'コメント'].concat(metricNames).concat(['投稿ID', '更新']);
  const rows = media.map(m => {
    const isReel = m.media_product_type === 'REELS';
    const want = isReel ? metricNames : c.igPostMetrics;
    const vals = pickMetrics_(m.id + '/insights', want, {}, null, item => (item.values && item.values.length) ? item.values[0].value : (item.total_value ? item.total_value.value : ''));
    return [new Date(m.timestamp), kindJa_(m), short_(m.caption), m.permalink, m.like_count || 0, m.comments_count || 0]
      .concat(metricNames.map(n => vals[n] === undefined ? '' : vals[n]))
      .concat([m.id, new Date()]);
  });
  writeTable_(SHEETS.igPosts, header, rows);
}

function fetchFbPosts_() {
  const c = config_();
  if (!c.pageId) return;
  const token = pageToken_();
  const res = graph_(c.pageId + '/posts', {
    fields: 'id,created_time,message,permalink_url,shares,reactions.summary(true).limit(0),comments.summary(true).limit(0)',
    limit: Math.min(c.postLimit, 100),
  }, token);
  const posts = res.data || [];
  const header = ['投稿日時', '本文', 'リンク', 'リアクション', 'コメント', 'シェア'].concat(c.fbPostMetrics).concat(['投稿ID', '更新']);
  const rows = posts.map(p => {
    const vals = pickMetrics_(p.id + '/insights', c.fbPostMetrics, {}, token, item => (item.values && item.values.length) ? item.values[0].value : '');
    return [new Date(p.created_time), short_(p.message), p.permalink_url,
      p.reactions && p.reactions.summary ? p.reactions.summary.total_count : 0,
      p.comments && p.comments.summary ? p.comments.summary.total_count : 0,
      p.shares ? p.shares.count : 0]
      .concat(c.fbPostMetrics.map(n => vals[n] === undefined ? '' : vals[n]))
      .concat([p.id, new Date()]);
  });
  writeTable_(SHEETS.fbPosts, header, rows);
}

/* ---------- Graph API ---------- */

function graph_(path, params, token) {
  const c = config_();
  const t = token || systemToken_();
  const q = Object.keys(params || {}).map(k => encodeURIComponent(k) + '=' + encodeURIComponent(params[k])).join('&');
  const url = 'https://graph.facebook.com/' + (c.version ? c.version + '/' : '') + path + (q ? '?' + q : '');
  const res = UrlFetchApp.fetch(url, {
    muteHttpExceptions: true,
    headers: { Authorization: 'Bearer ' + t },
  });
  const body = JSON.parse(res.getContentText() || '{}');
  if (res.getResponseCode() >= 400 || body.error) {
    const err = body.error || {};
    throw new Error((err.message || ('HTTP ' + res.getResponseCode())) + (err.code ? ' (code ' + err.code + ')' : ''));
  }
  return body;
}

function systemToken_() {
  const t = PropertiesService.getScriptProperties().getProperty('META_TOKEN');
  if (!t) throw new Error('鍵がありません。メニュー「インサイト」→「鍵を設定」で入れてください。');
  return t;
}

/** Facebook ページのインサイトはページの鍵が要る。システムユーザーの鍵から毎回取り出す（保存しない）。 */
function pageToken_() {
  const c = config_();
  if (!pageToken_.cache) {
    pageToken_.cache = graph_(c.pageId, { fields: 'access_token' }).access_token;
  }
  return pageToken_.cache;
}

/* ---------- シート ---------- */

function config_() {
  if (config_.cache) return config_.cache;
  const sh = SpreadsheetApp.getActive().getSheetByName(SHEETS.config);
  if (!sh) throw new Error('「設定」シートがありません。メニューの①を先に実行してください。');
  const map = {};
  sh.getRange(2, 1, Math.max(sh.getLastRow() - 1, 1), 2).getValues().forEach(r => { map[String(r[0]).trim()] = String(r[1]).trim(); });
  const list = k => (map[k] || '').split(',').map(s => s.trim()).filter(Boolean);
  config_.cache = {
    client: map['クライアント名'],
    pageId: map['FacebookページID'],
    igId: map['InstagramID'],
    version: map['APIバージョン'],
    igMetrics: list('IG日別の指標'),
    igSeries: list('IG日別の指標（時系列）'),
    igPostMetrics: list('IG投稿の指標'),
    igReelMetrics: list('IGリールの追加指標'),
    fbMetrics: list('FB日別の指標'),
    fbPostMetrics: list('FB投稿の指標'),
    postLimit: parseInt(map['投稿の取得件数'], 10) || 50,
    redoDays: Math.max(parseInt(map['毎日の取り直し日数'], 10) || 3, 1),
  };
  return config_.cache;
}

function ensureSheet_(name, header) {
  const ss = SpreadsheetApp.getActive();
  let sh = ss.getSheetByName(name);
  if (!sh) {
    sh = ss.insertSheet(name);
    sh.getRange(1, 1, 1, header.length).setValues([header]).setFontWeight('bold');
    sh.setFrozenRows(1);
  }
  return sh;
}

/** 日別のシートに、日付をキーにして上書き・追加する。列は指標が増えたら右に足す。 */
function upsertDaily_(name, rowsByDay) {
  const sh = ensureSheet_(name, ['日付']);
  const days = Object.keys(rowsByDay);
  if (!days.length) return;
  let header = sh.getRange(1, 1, 1, Math.max(sh.getLastColumn(), 1)).getValues()[0].filter(String);
  days.forEach(d => Object.keys(rowsByDay[d]).forEach(m => { if (header.indexOf(m) < 0) header.push(m); }));
  sh.getRange(1, 1, 1, header.length).setValues([header]).setFontWeight('bold');
  const last = sh.getLastRow();
  const existing = last > 1 ? sh.getRange(2, 1, last - 1, header.length).getValues() : [];
  const index = {};
  existing.forEach((r, i) => { index[fmt_(r[0])] = i; });
  days.forEach(d => {
    const row = header.map((h, j) => j === 0 ? toDate_(d) : (rowsByDay[d][h] !== undefined ? rowsByDay[d][h] : ''));
    if (index[d] !== undefined) {
      // 前に取れていて今回取れなかった値は消さない
      existing[index[d]] = row.map((v, j) => (v === '' && existing[index[d]][j] !== '') ? existing[index[d]][j] : v);
    } else {
      index[d] = existing.length;
      existing.push(row);
    }
  });
  existing.sort((a, b) => new Date(a[0]) - new Date(b[0]));
  sh.getRange(2, 1, existing.length, header.length).setValues(existing);
  sh.getRange(2, 1, existing.length, 1).setNumberFormat('yyyy-mm-dd');
}

function writeTable_(name, header, rows) {
  const sh = ensureSheet_(name, header);
  sh.clearContents();
  sh.getRange(1, 1, 1, header.length).setValues([header]).setFontWeight('bold');
  if (rows.length) {
    sh.getRange(2, 1, rows.length, header.length).setValues(rows);
    sh.getRange(2, 1, rows.length, 1).setNumberFormat('yyyy-mm-dd hh:mm');
    sh.getRange(2, header.length, rows.length, 1).setNumberFormat('yyyy-mm-dd hh:mm');
  }
}

/** 概要：直近7日と前の7日、直近28日。数式なので、データが増えると自動で変わる。 */
function buildSummary_() {
  const ss = SpreadsheetApp.getActive();
  let sh = ss.getSheetByName(SHEETS.summary);
  if (!sh) sh = ss.insertSheet(SHEETS.summary, 0);
  sh.clear();
  sh.getRange('A1').setFormula('="インサイトの概要 " & IFERROR(VLOOKUP("クライアント名",\'' + SHEETS.config + '\'!A:B,2,FALSE),"")').setFontSize(16).setFontWeight('bold');
  sh.getRange('A2').setFormula('="最終更新：" & IFERROR(TEXT(MAX(\'' + SHEETS.igDaily + '\'!A:A),"yyyy-mm-dd"),"まだ")');

  const block = (top, title, sheet) => {
    sh.getRange(top, 1).setValue(title).setFontWeight('bold').setFontSize(12);
    sh.getRange(top + 1, 1, 1, 5).setValues([['指標', '直近7日', '前の7日', '増減', '直近28日']]).setFontWeight('bold').setBackground('#f1f3f4');
    // 指標名は日別シートの見出し（B列〜）から取る。最大15個。
    for (let i = 0; i < 15; i++) {
      const r = top + 2 + i;
      const col = 'INDEX(\'' + sheet + '\'!$1:$1,' + (i + 2) + ')';
      const colRange = 'INDEX(\'' + sheet + '\'!$A:$Z,0,' + (i + 2) + ')';
      const dates = '\'' + sheet + '\'!$A:$A';
      const sum = (from, to) => 'SUMIFS(' + colRange + ',' + dates + ',">="&(TODAY()-' + from + '),' + dates + ',"<"&(TODAY()-' + to + '))';
      sh.getRange(r, 1).setFormula('=IFERROR(IF(' + col + '="","",' + col + '),"")');
      sh.getRange(r, 2).setFormula('=IF($A' + r + '="","",' + sum(7, 0) + ')');
      sh.getRange(r, 3).setFormula('=IF($A' + r + '="","",' + sum(14, 7) + ')');
      sh.getRange(r, 4).setFormula('=IF(OR($A' + r + '="",C' + r + '=0),"",B' + r + '/C' + r + '-1)');
      sh.getRange(r, 5).setFormula('=IF($A' + r + '="","",' + sum(28, 0) + ')');
    }
    sh.getRange(top + 2, 2, 15, 3).setNumberFormat('#,##0');
    sh.getRange(top + 2, 5, 15, 1).setNumberFormat('#,##0');
    sh.getRange(top + 2, 4, 15, 1).setNumberFormat('+0%;-0%;0%');
    return top + 18;
  };
  let next = block(4, 'Instagram（1日ごとの値の合計）', SHEETS.igDaily);
  next = block(next + 1, 'Facebook ページ（1日ごとの値の合計）', SHEETS.fbDaily);

  sh.getRange(next + 1, 1).setValue('Instagram の投稿 上位5件（リーチ順）').setFontWeight('bold').setFontSize(12);
  sh.getRange(next + 2, 1).setFormula('=IFERROR(QUERY(\'' + SHEETS.igPosts + '\'!A:Z,"select * where A is not null order by G desc limit 5",1),"投稿のデータはまだありません")');

  sh.getRange(3, 1).setValue('※ リーチは日ごとの合計なので、同じ人が何日も見ると重なって数えます。');
  sh.getRange(3, 1).setFontColor('#666666');
  sh.setColumnWidth(1, 240);
  [2, 3, 4, 5].forEach(c => sh.setColumnWidth(c, 110));
}

/* ---------- 小物 ---------- */

function range_(day) {
  // その日（日本時間）の 0:00〜24:00 を UNIX 秒で
  const start = Utilities.parseDate(day + ' 00:00:00', 'Asia/Tokyo', 'yyyy-MM-dd HH:mm:ss');
  return { since: Math.floor(start.getTime() / 1000), until: Math.floor(start.getTime() / 1000) + 86400 };
}
function daysAgo_(n) { return Utilities.formatDate(new Date(Date.now() - n * 86400000), 'Asia/Tokyo', 'yyyy-MM-dd'); }
function fmt_(v) { return v instanceof Date ? Utilities.formatDate(v, 'Asia/Tokyo', 'yyyy-MM-dd') : String(v); }
function toDate_(d) { return Utilities.parseDate(d, 'Asia/Tokyo', 'yyyy-MM-dd'); }
function short_(s) { s = String(s || '').replace(/\s+/g, ' ').trim(); return s.length > 80 ? s.slice(0, 80) + '…' : s; }
function sumObject_(o) { return Object.keys(o).reduce((a, k) => a + (Number(o[k]) || 0), 0); }
function kindJa_(m) {
  if (m.media_product_type === 'REELS') return 'リール';
  if (m.media_product_type === 'STORY') return 'ストーリー';
  return { IMAGE: '画像', VIDEO: '動画', CAROUSEL_ALBUM: 'カルーセル' }[m.media_type] || m.media_type;
}
function log_(msg) {
  const sh = ensureSheet_(SHEETS.log, ['日時', '内容']);
  sh.insertRowAfter(1);
  sh.getRange(2, 1, 1, 2).setValues([[new Date(), msg]]);
  if (sh.getLastRow() > 500) sh.deleteRows(501, sh.getLastRow() - 500);
}
function warnOnce_(key, msg) {
  if (!warnOnce_.seen) warnOnce_.seen = {};
  if (warnOnce_.seen[key]) return;
  warnOnce_.seen[key] = true;
  log_(msg);
}
