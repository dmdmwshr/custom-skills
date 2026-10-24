#!/usr/bin/env node
// 本地 .drawio 多页读写：list / get / set。语义移植自 @drawio/mcp 1.7.0 的 pages.js（Apache-2.0），
// 只用 Node 内置 zlib，无第三方依赖。set 前自动留 .bak-<时间> 备份，写入走同目录临时文件 + 重命名。
import { readFileSync, writeFileSync, renameSync, unlinkSync, existsSync } from "node:fs";
import { inflateRawSync, deflateRawSync } from "node:zlib";

const MAX_INFLATED = 64 * 1024 * 1024;
const DIAGRAM_RE = /<diagram\b([^>]*?)(?:\/>|>([\s\S]*?)<\/diagram>)/g;
const ATTR_RE = /([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*"([^"]*)"/g;

function fail(message) { process.stderr.write("Error: " + message + "\n"); process.exit(1); }
function assertPath(file) {
  if (!/\.(drawio|xml)$/i.test(file)) fail("只支持 .drawio 和 .xml 文件：" + file);
  if (!existsSync(file)) fail("文件不存在：" + file);
}
function attrs(raw) {
  const out = {}; let m; ATTR_RE.lastIndex = 0;
  while ((m = ATTR_RE.exec(raw)) !== null) out[m[1]] = m[2];
  return out;
}
function diagrams(text) {
  const list = []; let m, i = 0; DIAGRAM_RE.lastIndex = 0;
  while ((m = DIAGRAM_RE.exec(text)) !== null) list.push({ index: i++, attrs: attrs(m[1]), body: m[2] || "", start: m.index, end: m.index + m[0].length });
  return list;
}
const compressed = (body) => { const t = body.trim(); return t.length > 0 && !t.startsWith("<"); };
function decompress(body) {
  const t = body.trim();
  if (!compressed(t)) return t;
  try { return decodeURIComponent(inflateRawSync(Buffer.from(t, "base64"), { maxOutputLength: MAX_INFLATED }).toString("utf8")); }
  catch (e) { fail("页面解压失败：" + e.message); }
}
function find(list, ref) {
  const s = String(ref);
  if (/^\d+$/.test(s)) {
    const i = Number.parseInt(s, 10);
    if (i >= list.length) fail(`页序号 ${i} 超出范围（共 ${list.length} 页）`);
    return list[i];
  }
  let hit = list.filter((d) => d.attrs.name === s);
  if (hit.length === 0) hit = list.filter((d) => d.attrs.id === s);
  if (hit.length === 0) fail(`找不到名称或 id 为 "${s}" 的页；现有页名：${list.map((d) => d.attrs.name).join(", ")}`);
  if (hit.length > 1) fail(`多页同名 "${s}"（序号 ${hit.map((d) => d.index).join(", ")}），请改用序号或 id`);
  return hit[0];
}

const [cmd, file, pageRef, source] = process.argv.slice(2);
if (!["list", "get", "set"].includes(cmd) || !file || (cmd !== "list" && pageRef == null) || (cmd === "set" && !source)) {
  fail("用法：drawio_pages.mjs list <file> | get <file> <page> | set <file> <page> <新 mxGraphModel XML 文件，- 表示标准输入>");
}
assertPath(file);
const text = readFileSync(file, "utf8");
const list = diagrams(text);

if (cmd === "list") {
  console.log(JSON.stringify(list.map((d) => ({ index: d.index, id: d.attrs.id || null, name: d.attrs.name || null, approxSizeBytes: Buffer.byteLength(d.body, "utf8") })), null, 2));
} else if (cmd === "get") {
  process.stdout.write(decompress(find(list, pageRef).body) + "\n");
} else {
  const page = find(list, pageRef);
  const xml = (source === "-" ? readFileSync(0, "utf8") : readFileSync(source, "utf8")).trim();
  if (!xml.startsWith("<mxGraphModel")) fail("set 的内容必须是单页的 <mxGraphModel> XML，不能是完整 <mxfile> 或其他内容");
  if (/<\/?diagram\b/i.test(xml)) fail("set 的内容不能包含 <diagram> 标签");
  const body = compressed(page.body) ? deflateRawSync(Buffer.from(encodeURIComponent(xml))).toString("base64") : xml;
  const whole = text.slice(page.start, page.end);
  const replacement = whole.endsWith("/>")
    ? `${whole.slice(0, -2)}>${body}</diagram>`
    : whole.slice(0, whole.indexOf(">") + 1) + body + whole.slice(whole.lastIndexOf("</diagram>"));
  const stamp = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 14);
  const backup = `${file}.bak-${stamp}`;
  writeFileSync(backup, text, "utf8");
  const tmp = `${file}.${process.pid}.tmp`;
  try { writeFileSync(tmp, text.slice(0, page.start) + replacement + text.slice(page.end), "utf8"); renameSync(tmp, file); }
  catch (e) { try { unlinkSync(tmp); } catch {} fail(e.message); }
  console.log(`已更新第 ${page.index} 页（${page.attrs.name || ""}），备份 ${backup}`);
}
