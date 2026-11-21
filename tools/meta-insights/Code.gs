/**
 * Meta インサイト取得（Google スプレッドシートの Apps Script）
 *
 * 1つのスプレッドシートに、同じ会社のアカウント（Facebook ページ＋Instagram の組）をいくつでも入れられる。
 * アカウントごとに、日別のシートと分析のシートを分けて作る（例：IG_お宿ひさ家・分析_お宿ひさ家）。
 * 毎朝、前日までの数字を Meta Graph API から読んで、シートに書く。読むだけで、投稿や変更はしない。
 *
 * 置くもの
 *   - シート「アカウント」：名前・Facebook ページ ID・Instagram ID（1行に1組）
 *   - シート「設定」：指標の一覧など
 *   - スクリプト プロパティ META_TOKEN：システムユーザーの鍵（メニュー「鍵を設定」で入れる）
 * 鍵はシートにもコードにも書かない。シートをクライアントに渡すときは「閲覧者」で共有する。
 */

const SHEETS = {
  accounts: 'アカウント',
  config: '設定',
  summary: '概要',
  igPosts: 'IG_投稿',
  fbPosts: 'FB_投稿',
  log: 'ログ',
};

// フォロワー数のように「今の値」を見る指標（それ以外は期間の合計で比べる）
const LEVEL_METRICS = ['follower_count', 'page_follows'];

const DEFAULT_CONFIG = [
  ['APIバージョン', '', '空欄ならアプリの既定のバージョン。例：v24.0'],
  ['IG日別の指標', 'reach,views,accounts_engaged,total_interactions,likes,comments,shares,saves,profile_views', 'Instagram の1日ごとの合計（metric_type=total_value）。取れない指標は空欄になり、ログに残る'],
  ['IG日別の指標（時系列）', 'follower_count', '1日ごとの値（follower_count は直近30日まで）'],
  ['IG投稿の指標', 'reach,views,saved,shares,total_interactions', '投稿ごと'],
  ['IGリールの追加指標', 'ig_reels_avg_watch_time,ig_reels_video_view_total_time', 'リールだけに足す（ミリ秒）'],
  ['FB日別の指標', 'page_follows,page_media_view,page_total_media_view_unique,page_post_engagements,page_views_total', 'Facebook ページの1日ごと。取れない指標は空欄'],
  ['FB投稿の指標', '', '投稿ごとのインサイト（空欄なら、リアクション・コメント・シェアの数だけ）'],
  ['投稿の取得件数', '50', 'アカウントごとに、新しい順に何件まで読むか'],
  ['毎日の取り直し日数', '3', '数字が後から確定するので、直近何日を毎回取り直すか'],
];

// 日別のシートの先頭の列（この後ろに指標が並ぶ。シートはアカウントごとに分かれる）
const DAILY_KEYS = ['日付'];
const IG_POST_HEAD = ['アカウント', '投稿日時', '種類', 'キャプション', 'リンク', 'いいね', 'コメント'];
const FB_POST_HEAD = ['アカウント', '投稿日時', '本文', 'リンク', 'リアクション', 'コメント', 'シェア'];

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
    .addItem('概要と分析を作り直す', 'buildSummary')
    .addItem('旧の日別シートをアカウントごとに分ける（一度だけ）', 'migrateOld')
    .addItem('毎朝の自動取得をオフ', 'removeTrigger')
    .addToUi();
}

function setup() {
  const ss = SpreadsheetApp.getActive();
  if (ss.getSpreadsheetTimeZone() !== 'Asia/Tokyo') ss.setSpreadsheetTimeZone('Asia/Tokyo');

  let acc = ss.getSheetByName(SHEETS.accounts);
  if (!acc) {
    acc = ss.insertSheet(SHEETS.accounts, 0);
    acc.getRange(1, 1, 1, 3).setValues([['名前', 'FacebookページID', 'InstagramID']]).setFontWeight('bold');
    acc.getRange(2, 2, 50, 2).setNumberFormat('@'); // 長い ID が数字に丸められないように文字として持つ
    acc.setColumnWidth(1, 220).setColumnWidth(2, 200).setColumnWidth(3, 200);
    acc.setFrozenRows(1);
  }

  let cfg = ss.getSheetByName(SHEETS.config);
  if (!cfg) {
    cfg = ss.insertSheet(SHEETS.config);
    cfg.getRange(1, 1, 1, 3).setValues([['項目', '値', '説明']]).setFontWeight('bold');
    cfg.setFrozenRows(1);
  }
  const have = cfg.getLastRow() > 0 ? cfg.getRange(1, 1, cfg.getLastRow(), 1).getValues().map(r => r[0]) : [];
  DEFAULT_CONFIG.filter(r => have.indexOf(r[0]) < 0).forEach(r => cfg.appendRow(r)); // 本人が直した値は消さない
  cfg.setColumnWidth(1, 200).setColumnWidth(2, 420).setColumnWidth(3, 420);

  ensureSheet_(SHEETS.igPosts, IG_POST_HEAD);
  ensureSheet_(SHEETS.fbPosts, FB_POST_HEAD);
  ensureSheet_(SHEETS.log, ['日時', '内容']);
  buildSummary();

  // 最初からある空のシートは消す
  ['シート1', 'Sheet1'].forEach(n => {
    const s = ss.getSheetByName(n);
    if (s && s.getLastRow() === 0 && ss.getSheets().length > 1) ss.deleteSheet(s);
  });
  ss.setActiveSheet(acc);
  SpreadsheetApp.getUi().alert('シートを作りました。「アカウント」に名前と ID を入れてから、②「鍵を設定」へ進んでください。');
}

function setToken() {
  const ui = SpreadsheetApp.getUi();
  const res = ui.prompt('鍵を設定', 'Meta のシステムユーザーで作った鍵（アクセストークン）を貼ってください。\nシートには保存されず、このスクリプトの中にだけ残ります。', ui.ButtonSet.OK_CANCEL);
  if (res.getSelectedButton() !== ui.Button.OK) return;
  const t = res.getResponseText().trim();
  if (!t) return;
  PropertiesService.getScriptProperties().setProperty('META_TOKEN', t);
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
  const lines = [];
  accounts_().forEach(a => {
    lines.push('■ ' + a.name);
    if (a.igId) {
      try {
        const ig = graph_(a.igId, { fields: 'username,followers_count,media_count' });
        lines.push('  Instagram：@' + ig.username + '（フォロワー ' + ig.followers_count + '）');
      } catch (e) { lines.push('  Instagram：読めません → ' + e.message); }
    }
    if (a.pageId) {
      try {
        const p = graph_(a.pageId, { fields: 'name,followers_count' }, pageToken_(a.pageId));
        lines.push('  Facebook：' + p.name + '（フォロワー ' + p.followers_count + '）');
      } catch (e) { lines.push('  Facebook：読めません → ' + e.message); }
    }
  });
  SpreadsheetApp.getUi().alert(lines.join('\n') || '「アカウント」シートが空です。');
}

/* ---------- 取得 ---------- */

function runDaily() {
  const c = config_();
  const days = [];
  for (let i = c.redoDays; i >= 1; i--) days.push(daysAgo_(i));
  fetchAll_(days);
  log_('毎日の取得：' + days[0] + '〜' + days[days.length - 1]);
}

function backfill30() {
  const days = [];
  for (let i = 30; i >= 1; i--) days.push(daysAgo_(i));
  fetchAll_(days);
  log_('過去30日の取り込み：' + days[0] + '〜' + days[days.length - 1]);
  SpreadsheetApp.getUi().alert('過去30日を取り込みました。取れなかった指標は「ログ」にあります。');
}

function fetchAll_(days) {
  const accs = accounts_();
  if (!accs.length) throw new Error('「アカウント」シートに名前と ID を入れてください。');
  const igRows = {}, fbRows = {}, igPosts = [], fbPosts = [];
  accs.forEach(a => {
    // 1つのアカウントで失敗しても、ほかは続ける
    try {
      if (a.igId) {
        days.forEach(d => { igRows[d + '|' + a.name] = igDay_(a, d); });
        igPosts.push.apply(igPosts, igPostRows_(a));
      }
    } catch (e) { log_(a.name + ' の Instagram：' + e.message); }
    try {
      if (a.pageId) {
        days.forEach(d => { fbRows[d + '|' + a.name] = fbDay_(a, d); });
        fbPosts.push.apply(fbPosts, fbPostRows_(a));
      }
    } catch (e) { log_(a.name + ' の Facebook：' + e.message); }
  });
  upsertDaily_('IG', igRows);
  upsertDaily_('FB', fbRows);
  const c = config_();
  writePosts_(SHEETS.igPosts, IG_POST_HEAD.concat(igPostMetricNames_(c)), igPosts, accs.map(a => a.name));
  writePosts_(SHEETS.fbPosts, FB_POST_HEAD.concat(c.fbPostMetrics), fbPosts, accs.map(a => a.name));
  buildSummary();
}

function igDay_(a, day) {
  const c = config_();
  const r = range_(day);
  const out = {};
  Object.assign(out, pickMetrics_(a.igId + '/insights', c.igMetrics, { period: 'day', metric_type: 'total_value', since: r.since, until: r.until }, null,
    item => item.total_value ? item.total_value.value : ''));
  Object.assign(out, pickMetrics_(a.igId + '/insights', c.igSeries, { period: 'day', since: r.since, until: r.until }, null,
    item => (item.values && item.values.length) ? item.values[0].value : ''));
  return out;
}

function fbDay_(a, day) {
  const c = config_();
  const r = range_(day);
  return pickMetrics_(a.pageId + '/insights', c.fbMetrics, { period: 'day', since: r.since, until: r.until }, pageToken_(a.pageId),
    item => (item.values && item.values.length) ? item.values[0].value : '');
}

function igPostMetricNames_(c) {
  return c.igPostMetrics.concat(c.igReelMetrics.filter(m => c.igPostMetrics.indexOf(m) < 0));
}

function igPostRows_(a) {
  const c = config_();
  const names = igPostMetricNames_(c);
  const media = graph_(a.igId + '/media', {
    fields: 'id,timestamp,media_type,media_product_type,caption,permalink,like_count,comments_count',
    limit: Math.min(c.postLimit, 100),
  }).data || [];
  return media.map(m => {
    const want = m.media_product_type === 'REELS' ? names : c.igPostMetrics;
    const vals = pickMetrics_(m.id + '/insights', want, {}, null,
      item => (item.values && item.values.length) ? item.values[0].value : (item.total_value ? item.total_value.value : ''));
    return [a.name, new Date(m.timestamp), kindJa_(m), short_(m.caption), m.permalink, m.like_count || 0, m.comments_count || 0]
      .concat(names.map(n => vals[n] === undefined ? '' : vals[n]));
  });
}

function fbPostRows_(a) {
  const c = config_();
  const token = pageToken_(a.pageId);
  const posts = graph_(a.pageId + '/posts', {
    fields: 'id,created_time,message,permalink_url,shares,reactions.summary(true).limit(0),comments.summary(true).limit(0)',
    limit: Math.min(c.postLimit, 100),
  }, token).data || [];
  return posts.map(p => {
    const vals = pickMetrics_(p.id + '/insights', c.fbPostMetrics, {}, token, item => (item.values && item.values.length) ? item.values[0].value : '');
    return [a.name, new Date(p.created_time), short_(p.message), p.permalink_url,
      p.reactions && p.reactions.summary ? p.reactions.summary.total_count : 0,
      p.comments && p.comments.summary ? p.comments.summary.total_count : 0,
      p.shares ? p.shares.count : 0]
      .concat(c.fbPostMetrics.map(n => vals[n] === undefined ? '' : vals[n]));
  });
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
  } catch (e) {
    metrics.forEach(m => {
      try {
        apply(graph_(path, Object.assign({}, params, { metric: m }), token).data);
      } catch (e2) {
        warnOnce_(m, '取れない指標 ' + m + '：' + e2.message);
      }
    });
  }
  return out;
}

/* ---------- Graph API ---------- */

function graph_(path, params, token) {
  const c = config_();
  const t = token || systemToken_();
  const q = Object.keys(params || {}).map(k => encodeURIComponent(k) + '=' + encodeURIComponent(params[k])).join('&');
  const url = 'https://graph.facebook.com/' + (c.version ? c.version + '/' : '') + path + (q ? '?' + q : '');
  const res = UrlFetchApp.fetch(url, { muteHttpExceptions: true, headers: { Authorization: 'Bearer ' + t } });
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
function pageToken_(pageId) {
  if (!pageToken_.cache) pageToken_.cache = {};
  if (!pageToken_.cache[pageId]) pageToken_.cache[pageId] = graph_(pageId, { fields: 'access_token' }).access_token;
  return pageToken_.cache[pageId];
}

/* ---------- シート ---------- */

function accounts_() {
  if (accounts_.cache) return accounts_.cache;
  const sh = SpreadsheetApp.getActive().getSheetByName(SHEETS.accounts);
  if (!sh || sh.getLastRow() < 2) return (accounts_.cache = []);
  accounts_.cache = sh.getRange(2, 1, sh.getLastRow() - 1, 3).getDisplayValues()
    .map(r => ({ name: r[0].trim(), pageId: r[1].trim(), igId: r[2].trim() }))
    .filter(a => a.name && (a.pageId || a.igId));
  return accounts_.cache;
}

function config_() {
  if (config_.cache) return config_.cache;
  const sh = SpreadsheetApp.getActive().getSheetByName(SHEETS.config);
  if (!sh) throw new Error('「設定」シートがありません。メニューの①を先に実行してください。');
  const map = {};
  sh.getRange(2, 1, Math.max(sh.getLastRow() - 1, 1), 2).getDisplayValues().forEach(r => { map[String(r[0]).trim()] = String(r[1]).trim(); });
  const list = k => (map[k] || '').split(',').map(s => s.trim()).filter(Boolean);
  config_.cache = {
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

/** アカウントごとの日別シート名。例：IG_お宿ひさ家 */
function dailySheetName_(platform, acc) { return platform + '_' + acc; }

/** rowsByKey は「日付|アカウント」が鍵。アカウントごとのシートに分けて書く。 */
function upsertDaily_(platform, rowsByKey) {
  const byAcc = {};
  Object.keys(rowsByKey).forEach(k => {
    const i = k.indexOf('|');
    const d = k.slice(0, i), acc = k.slice(i + 1);
    (byAcc[acc] = byAcc[acc] || {})[d] = rowsByKey[k];
  });
  Object.keys(byAcc).forEach(acc => upsertSheet_(dailySheetName_(platform, acc), byAcc[acc]));
}

/** 1つのアカウントの日別シートに、日付をキーにして上書き・追加する。指標が増えたら右に列を足す。 */
function upsertSheet_(name, rowsByDate) {
  const sh = ensureSheet_(name, DAILY_KEYS);
  const header = sh.getRange(1, 1, 1, Math.max(sh.getLastColumn(), 1)).getValues()[0].map(String).filter(String);
  if (header[0] !== '日付') header.unshift('日付');
  Object.keys(rowsByDate).forEach(d => Object.keys(rowsByDate[d]).forEach(m => { if (header.indexOf(m) < 0) header.push(m); }));
  sh.getRange(1, 1, 1, header.length).setValues([header]).setFontWeight('bold');

  const last = sh.getLastRow();
  const old = last > 1 ? sh.getRange(2, 1, last - 1, header.length).getValues() : [];
  const byDate = {};
  old.forEach(r => { const k = fmt_(r[0]); if (k) byDate[k] = r; });
  Object.keys(rowsByDate).forEach(d => {
    const v = rowsByDate[d];
    const row = header.map((h, j) => j === 0 ? toDate_(d) : (v[h] !== undefined ? v[h] : ''));
    const prev = byDate[d];
    // 前に取れた値は消さない（今回空欄のところだけ前の値を残す）
    byDate[d] = prev ? row.map((x, j) => (x === '' && prev[j] !== '') ? prev[j] : x) : row;
  });
  const rows = Object.keys(byDate).sort().map(k => byDate[k]);
  if (rows.length) {
    sh.getRange(2, 1, rows.length, header.length).setValues(rows);
    sh.getRange(2, 1, rows.length, 1).setNumberFormat('yyyy-mm-dd');
  }
}

/** 投稿のシートは毎回書き直す。今回読めなかったアカウントの行は前のまま残す。 */
function writePosts_(name, header, newRows, names) {
  const sh = ensureSheet_(name, header);
  const fetched = {};
  newRows.forEach(r => { fetched[r[0]] = true; });
  const oldHeader = sh.getRange(1, 1, 1, Math.max(sh.getLastColumn(), 1)).getValues()[0];
  const keep = sh.getLastRow() > 1
    ? sh.getRange(2, 1, sh.getLastRow() - 1, oldHeader.length).getValues()
      .filter(r => r[0] && !fetched[r[0]] && names.indexOf(r[0]) >= 0)
      .map(r => header.map(h => { const i = oldHeader.indexOf(h); return i >= 0 ? r[i] : ''; }))
    : [];
  const rows = newRows.concat(keep).sort((a, b) => new Date(b[1]) - new Date(a[1]));
  sh.clearContents();
  sh.getRange(1, 1, 1, header.length).setValues([header]).setFontWeight('bold');
  if (rows.length) {
    sh.getRange(2, 1, rows.length, header.length).setValues(rows);
    sh.getRange(2, 2, rows.length, 1).setNumberFormat('yyyy-mm-dd hh:mm');
  }
}

/* ---------- 概要と分析 ---------- */

/** 概要：アカウントごとに、直近7日・前の7日・増減・直近28日と、投稿の上位5件・種類別。分析のシートも作り直す。 */
function buildSummary() {
  const ss = SpreadsheetApp.getActive();
  let sh = ss.getSheetByName(SHEETS.summary);
  if (!sh) sh = ss.insertSheet(SHEETS.summary, 0);
  sh.clear();
  sh.getRange('A1').setValue('インサイトの概要').setFontSize(16).setFontWeight('bold');
  sh.getRange('A2').setValue('最終更新：' + Utilities.formatDate(new Date(), 'Asia/Tokyo', 'yyyy-MM-dd HH:mm'));
  sh.getRange('A3').setValue('※ フォロワーは「今の値」と「7日前・28日前」で比べます。リーチなどは日ごとの合計なので、同じ人が何日も見ると重なって数えます。新しいアカウントを足したら、メニューの「概要と分析を作り直す」を押してください。').setFontColor('#666666');

  const q = s => "'" + String(s).replace(/'/g, "''") + "'";
  const block = (top, title, sheetName) => {
    sh.getRange(top, 1).setValue(title).setFontWeight('bold').setFontSize(12);
    sh.getRange(top + 1, 1, 1, 5).setValues([['指標', '直近7日（フォロワーは今）', '前の7日（フォロワーは7日前）', '増減', '直近28日（フォロワーは今）']]).setFontWeight('bold').setBackground('#f1f3f4');
    const S = q(sheetName);
    const n = 12; // 指標は日別シートの2列目から最大12個
    for (let i = 0; i < n; i++) {
      const r = top + 2 + i;
      const col = i + 2;
      const name = 'IFERROR(INDEX(' + S + '!$1:$1,1,' + col + '),"")';
      const vals = 'INDEX(' + S + '!$A:$Z,0,' + col + ')';
      const dates = S + '!$A:$A';
      const isLevel = 'OR($A' + r + '="follower_count",$A' + r + '="page_follows")';
      const sum = (from, to) => 'IFERROR(SUMIFS(' + vals + ',' + dates + ',">="&(TODAY()-' + from + '),' + dates + ',"<"&(TODAY()-' + to + ')),"")';
      const latest = 'IFERROR(LOOKUP(2,1/(' + vals + '<>""),' + vals + '),"")';
      const at = days => 'IFERROR(INDEX(' + vals + ',MATCH(TODAY()-' + days + ',' + dates + ',0)),"")';
      sh.getRange(r, 1).setFormula('=IF(' + name + '="","",' + name + ')');
      sh.getRange(r, 2).setFormula('=IF($A' + r + '="","",IF(' + isLevel + ',' + latest + ',' + sum(7, 0) + '))');
      sh.getRange(r, 3).setFormula('=IF($A' + r + '="","",IF(' + isLevel + ',' + at(7) + ',' + sum(14, 7) + '))');
      sh.getRange(r, 4).setFormula('=IF(OR($A' + r + '="",C' + r + '="",C' + r + '=0),"",IF(' + isLevel + ',TEXT(B' + r + '-C' + r + ',"+#,##0;-#,##0;0"),TEXT(B' + r + '/C' + r + '-1,"+0%;-0%;0%")))');
      sh.getRange(r, 5).setFormula('=IF($A' + r + '="","",IF(' + isLevel + ',' + latest + ',' + sum(28, 0) + '))');
    }
    sh.getRange(top + 2, 2, n, 3).setNumberFormat('#,##0');
    sh.getRange(top + 2, 4, n, 1).setHorizontalAlignment('right');
    return top + 2 + n + 1;
  };

  let row = 5;
  const accs = accounts_();
  if (!accs.length) sh.getRange(row, 1).setValue('「アカウント」シートに名前と ID を入れて、「概要と分析を作り直す」を押してください。');
  accs.forEach(a => {
    sh.getRange(row, 1).setValue(a.name).setFontSize(14).setFontWeight('bold');
    row += 1;
    if (a.igId) row = block(row, 'Instagram', dailySheetName_('IG', a.name));
    if (a.pageId) row = block(row, 'Facebook ページ', dailySheetName_('FB', a.name));
    if (a.igId) {
      const an = a.name.replace(/'/g, "''");
      sh.getRange(row, 1).setValue('Instagram の投稿 上位5件（リーチ順）').setFontWeight('bold');
      // IG_投稿 の H 列が「IG投稿の指標」の1つ目（既定は reach）
      sh.getRange(row + 1, 1).setFormula('=IFERROR(QUERY(' + q(SHEETS.igPosts) + '!A:Z,"select B,C,D,E,H,F,G where A = \'' + an + '\' order by H desc limit 5",1),"投稿のデータはまだありません")');
      sh.getRange(row + 8, 1).setValue('投稿の種類ごとの平均（次の投稿の作戦に使う）').setFontWeight('bold');
      sh.getRange(row + 9, 1).setFormula('=IFERROR(QUERY(' + q(SHEETS.igPosts) + '!A:Z,"select C, count(B), avg(H) where A = \'' + an + '\' and C is not null group by C label count(B) \'件\', avg(H) \'平均リーチ\' order by avg(H) desc",1),"まだ投稿のデータがありません")');
      row += 16;
      buildAnalysis_(a.name);
    }
    row += 1;
  });
  sh.setColumnWidth(1, 240);
  [2, 3, 4, 5].forEach(c => sh.setColumnWidth(c, 130));
}

/** 分析：1アカウントの日別の表（フォロワーの増減・率・7日平均）と、グラフ3つ。IG の日別シートから作る。 */
function buildAnalysis_(acc) {
  const ss = SpreadsheetApp.getActive();
  const src = ss.getSheetByName(dailySheetName_('IG', acc));
  const sh = ensureSheet_('分析_' + acc, ['日付']);
  sh.getCharts().forEach(c => sh.removeChart(c));
  sh.clear();
  if (!src || src.getLastRow() < 2) {
    sh.getRange(1, 1).setValue('まだデータがありません。「③ 過去30日を取り込む」を押してください。');
    return;
  }
  const vals = src.getDataRange().getValues();
  const h = vals[0];
  const at = m => h.indexOf(m);
  const ci = { f: at('follower_count'), reach: at('reach'), views: at('views'), likes: at('likes'),
    saves: at('saves'), shares: at('shares'), prof: at('profile_views') };
  const num = (r, i) => (i < 0 || r[i] === '' || r[i] === null || isNaN(Number(r[i]))) ? null : Number(r[i]);
  const ratio = (a, b) => (a !== null && b) ? a / b : '';

  const head = ['日付', 'フォロワー数', 'フォロワー増減', 'リーチ', '再生数', 'いいね', '保存', 'シェア', 'プロフィール表示',
    'いいね率（いいね÷再生）', '保存率（保存÷リーチ）', 'プロフィール率（プロフ÷リーチ）', 'フォロー率（増減÷リーチ）', 'リーチ7日平均'];
  const data = vals.slice(1).filter(r => r[0] !== '' && r[0] !== null)
    .sort((a, b) => new Date(a[0]) - new Date(b[0]));
  const out = [];
  let prevF = null;
  const reaches = [];
  data.forEach(r => {
    const f = num(r, ci.f), reach = num(r, ci.reach), views = num(r, ci.views), likes = num(r, ci.likes);
    const saves = num(r, ci.saves), shares = num(r, ci.shares), prof = num(r, ci.prof);
    const gain = (f !== null && prevF !== null) ? f - prevF : '';
    if (f !== null) prevF = f;
    reaches.push(reach || 0);
    const last7 = reaches.slice(-7);
    const avg7 = last7.length >= 7 ? Math.round(last7.reduce((s, x) => s + x, 0) / 7) : '';
    out.push([toDateOnly_(r[0]), f === null ? '' : f, gain, reach === null ? '' : reach, views === null ? '' : views,
      likes === null ? '' : likes, saves === null ? '' : saves, shares === null ? '' : shares, prof === null ? '' : prof,
      ratio(likes, views), ratio(saves, reach), ratio(prof, reach), gain === '' ? '' : ratio(gain, reach), avg7]);
  });
  if (!out.length) return;
  sh.getRange(1, 1, 1, head.length).setValues([head]).setFontWeight('bold').setWrap(true);
  sh.getRange(2, 1, out.length, head.length).setValues(out);
  sh.getRange(2, 1, out.length, 1).setNumberFormat('yyyy-mm-dd');
  sh.getRange(2, 2, out.length, head.length - 1).setNumberFormat('#,##0');
  sh.getRange(2, 10, out.length, 4).setNumberFormat('0.0%');
  sh.getRange(2, 3, out.length, 1).setNumberFormat('+#,##0;-#,##0;0');
  sh.setFrozenRows(1).setFrozenColumns(1);
  sh.setColumnWidths(1, head.length, 110);

  const n = out.length;
  const chart = (type, cols, title, anchorRow) => sh.newChart()
    .setChartType(type)
    .addRange(sh.getRange(1, 1, n + 1, 1))
    .addRange(sh.getRange(1, cols, n + 1, 1))
    .setOption('title', title)
    .setOption('legend', { position: 'none' })
    .setPosition(anchorRow, head.length + 2, 0, 0)
    .build();
  sh.insertChart(chart(Charts.ChartType.LINE, 2, 'フォロワー数の推移', 2));
  sh.insertChart(chart(Charts.ChartType.COLUMN, 3, 'フォロワーの増減（日ごと）', 20));
  sh.insertChart(chart(Charts.ChartType.COLUMN, 4, 'リーチ（日ごと）', 38));
}

/* ---------- 旧シートの移行（一度だけ） ---------- */

/** 前の版の「IG_日別」「FB_日別」（アカウント列つき）を、アカウントごとのシートに分ける。元のシートは消さない。 */
function migrateOld() {
  const ss = SpreadsheetApp.getActive();
  const moved = [];
  [['IG', 'IG_日別'], ['FB', 'FB_日別']].forEach(p => {
    const sh = ss.getSheetByName(p[1]);
    if (!sh || sh.getLastRow() < 2) return;
    const v = sh.getDataRange().getValues();
    const h = v[0];
    const rowsByKey = {};
    v.slice(1).forEach(r => {
      const d = fmt_(r[0]);
      const acc = String(r[1]).trim();
      if (!d || !acc || acc === 'アカウント') return;
      const o = {};
      h.forEach((m, i) => { if (i > 1 && m !== '') o[m] = r[i]; });
      rowsByKey[d + '|' + acc] = o;
    });
    upsertDaily_(p[0], rowsByKey);
    moved.push(p[1]);
  });
  log_('旧シートを分けました：' + (moved.join('、') || 'なし'));
  buildSummary();
  SpreadsheetApp.getUi().alert(moved.length ? '旧の日別シートをアカウントごとに分けました。元のシート（IG_日別・FB_日別）は残してあります。' : '分ける旧シートはありませんでした。');
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
function toDateOnly_(v) { return v instanceof Date ? toDate_(fmt_(v)) : toDate_(String(v)); }
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
