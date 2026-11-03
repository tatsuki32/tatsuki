#!/usr/bin/env node
// 公開前の自動チェック：npm run validate-writing <file...>
// AIっぽい表現・禁止ワード・書式の統一・構成の抜けを調べる。依存なし（Node 18+）。
// 終了コード：問題（❌）があれば 1、注意（⚠️）だけなら 0。
import { readFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const files = process.argv.slice(2);
if (!files.length) { console.error("使い方：npm run validate-writing <ファイル> [ファイル…]"); process.exit(2); }

const words = name => { const p = join(here, name); return existsSync(p) ? readFileSync(p, "utf8").split(/\r?\n/).map(s => s.trim()).filter(s => s && !s.startsWith("#")) : []; };
const BANNED = [...words("banned-words.txt"), ...words("banned-words.local.txt")];
// AIっぽい言い回し：[正規表現, 説明, 何回までなら許すか]
const AI = [
  [/いかがでしたか/g, "「いかがでしたか」", 0],
  [/と言えるでしょう|といえるでしょう/g, "「〜と言えるでしょう」", 0],
  [/ことが(重要|大切|不可欠)です/g, "「〜ことが重要です」", 1],
  [/さまざまな|様々な/g, "「さまざまな」", 2],
  [/多岐にわたる/g, "「多岐にわたる」", 0],
  [/深掘り/g, "「深掘り」", 1],
  [/^(まとめると|結論として|総じて)/gm, "締めの決まり文句", 0],
  [/することができます/g, "「〜することができます」（「〜できます」で足りる）", 1],
  [/において/g, "「において」", 2],
  [/——|—/g, "ダッシュ（—）", 2],
  [/\b(delve|tapestry|leverage|seamless|game-?changer|in today's fast-paced)\b/gi, "英語のAIっぽい語", 0],
];

let errors = 0, warns = 0;
for (const file of files) {
  let text;
  try { text = readFileSync(file, "utf8"); } catch { console.error(`❌ 読めません：${file}`); errors++; continue; }
  const lines = text.split(/\r?\n/);
  const out = [];
  const lineOf = i => text.slice(0, i).split("\n").length;
  const add = (lvl, msg) => { out.push(`  ${lvl} ${msg}`); lvl === "❌" ? errors++ : warns++; };

  // 1. 禁止ワード
  for (const w of BANNED) { let i = -1; while ((i = text.indexOf(w, i + 1)) >= 0) add("❌", `禁止ワード「${w}」（${lineOf(i)}行目）`); }
  // 2. AIっぽい表現
  for (const [re, label, allow] of AI) {
    const hits = [...text.matchAll(re)];
    if (hits.length > allow) add(allow ? "⚠️" : "❌", `AIっぽい表現 ${label} が ${hits.length} 回（${hits.slice(0, 3).map(m => lineOf(m.index) + "行目").join("・")}）`);
  }
  // 3. 書式の統一
  const desu = (text.match(/(です|ます|でした|ました|ません|でしょう|ください)[。！？\n]/g) || []).length;
  const da = (text.match(/(?<!(まし|でし))(だ|である|ない|[うくつぬむるぐぶ]|た)[。！？]/g) || []).length;
  if (desu && da) add(Math.min(desu, da) >= 2 ? "❌" : "⚠️", `文体が混ざっています（です・ます ${desu} / だ・である ${da}）`);
  const zen = (text.match(/[０-９]/g) || []).length, han = (text.match(/[0-9]/g) || []).length;
  if (zen && han) add("⚠️", `数字の全角（${zen}）と半角（${han}）が混ざっています`);
  if (/[“”]/.test(text) && /「/.test(text)) add("⚠️", "かぎかっこ「」と “ ” が混ざっています");
  if (/[!?]/.test(text) && /[！？]/.test(text)) add("⚠️", "感嘆符・疑問符の全角と半角が混ざっています");
  if (/[^\S\n]+$/m.test(text)) add("⚠️", "行末に空白があります");
  const bangs = (text.match(/[!！]/g) || []).length;
  if (bangs > 3) add("⚠️", `感嘆符が ${bangs} 個（3個まで）`);
  // 4. 構成の抜け
  const heads = lines.map((l, i) => [/^(#{1,6})\s/.exec(l)?.[1].length, i]).filter(h => h[0]);
  if (/\.(md|markdown)$/i.test(file)) {
    if (!heads.some(h => h[0] === 1)) add("❌", "タイトル（# 見出し）がありません");
    for (let k = 1; k < heads.length; k++) if (heads[k][0] > heads[k - 1][0] + 1) add("⚠️", `見出しのレベルが飛んでいます（${heads[k][1] + 1}行目）`);
  }
  const body = lines.filter(l => l.trim() && !/^(#|\s*[-*]|\s*\d+\.|>|```)/.test(l));
  if (body.length && body.slice(0, 3).join("").length < 20) add("⚠️", "冒頭が短すぎます（最初の3行で何の話か分かるように）");
  const paras = text.split(/\n\s*\n/).filter(p => !/^(#|```|\s*[-*]|\s*\d+\.)/.test(p.trim()));
  paras.forEach(p => { if (p.replace(/\s/g, "").length > 300) add("⚠️", `長い段落があります（${p.replace(/\s/g, "").length}字、${lineOf(text.indexOf(p))}行目〜）`); });
  const longS = text.split(/[。！？!?]\s*/).filter(s => s.replace(/\s/g, "").length > 80).length;
  if (longS) add("⚠️", `80字を超える長い文が ${longS} 個`);
  if (body.length >= 8 && !heads.some(h => /まとめ|おわりに|最後に|次に試す|結論/.test(lines[h[1]]))) add("⚠️", "締めの見出し（まとめ・次に試すこと など）がありません");

  console.log(out.length ? `📝 ${file}\n${out.join("\n")}` : `✅ ${file}：問題なし`);
}
console.log(`\n結果：❌ ${errors} 件・⚠️ ${warns} 件${errors ? "（❌ を直してから公開してください）" : ""}`);
process.exit(errors ? 1 : 0);
