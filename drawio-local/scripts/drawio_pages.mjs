#!/usr/bin/env node
// 本地 .drawio 多页读写：list / get / set。语义移植自 @drawio/mcp 1.7.0 的 pages.js（Apache-2.0），
// 使用 Node 内置 zlib 和既有 Python3 标准 XML 解析器，无第三方依赖。set 前自动留 .bak-<时间> 备份，写入走同目录临时文件 + 重命名。
import { readFileSync, writeFileSync, renameSync, unlinkSync, existsSync } from "node:fs";
import { randomBytes } from "node:crypto";
import { checkXml } from "./xml_wellformed.mjs";
import { inflateRawSync, deflateRawSync } from "node:zlib";

const MAX_INFLATED = 64 * 1024 * 1024;

function fail(message) { process.stderr.write("Error: " + message + "\n"); process.exit(1); }
function assertPath(file) {
  if (!/\.(drawio|xml)$/i.test(file)) fail("只支持 .drawio 和 .xml 文件：" + file);
  if (!existsSync(file)) fail("文件不存在：" + file);
}
function diagrams(text) {
  try { return checkXml(text, { diagrams: true }).diagrams; }
  catch (e) { fail("图文件 XML 解析失败：" + e.message); }
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
  let xml = (source === "-" ? readFileSync(0, "utf8") : readFileSync(source, "utf8")).trim();
  let info;
  try { info = checkXml(xml, { stripDeclaration: true }); xml = info.xml; } catch (e) { fail("set 的内容不是良构 XML：" + e.message); }
  if (info.root !== "mxGraphModel") fail(`set 的内容根元素必须是 mxGraphModel，实际是 ${info.root}`);
  if (info.hasDiagram) fail("set 的内容不能包含 <diagram> 标签");
  const body = compressed(page.body) ? deflateRawSync(Buffer.from(encodeURIComponent(xml))).toString("base64") : xml;
  const whole = text.slice(page.start, page.end);
  const replacement = whole.endsWith("/>")
    ? `${whole.slice(0, -2)}>${body}</diagram>`
    : whole.slice(0, page.bodyStart - page.start) + body + whole.slice(page.bodyEnd - page.start);
  const result = text.slice(0, page.start) + replacement + text.slice(page.end);
  try { checkXml(text); checkXml(result); } catch (e) { fail("写入前整体校验失败，主文件未改动：" + e.message); }

  // 备份与临时文件都用排他创建（wx）：名字带毫秒、进程号和随机后缀，绝不覆盖已有对象；失败时只清理本次自己创建的
  const unique = () => `${new Date().toISOString().replace(/[-:T.Z]/g, "")}-${process.pid}-${randomBytes(4).toString("hex")}`;
  const exclusiveWrite = (prefix, suffix, data) => {
    for (let attempt = 0; attempt < 20; attempt++) {
      const target = `${prefix}${unique()}${suffix}`;
      try { writeFileSync(target, data, { encoding: "utf8", flag: "wx" }); return target; }
      catch (e) { if (e.code !== "EEXIST") throw e; }
    }
    throw new Error("无法创建唯一文件名");
  };
  let backup = null, tmp = null;
  try {
    if (readFileSync(file, "utf8") !== text) throw new Error("文件在读取之后被其他程序改动，已放弃写入");
    backup = exclusiveWrite(`${file}.bak-`, "", text);
    tmp = exclusiveWrite(`${file}.`, ".tmp", result);
    if (readFileSync(file, "utf8") !== text) throw new Error("文件在写入前一刻被其他程序改动，已放弃写入");
    renameSync(tmp, file); tmp = null;
    if (readFileSync(file, "utf8") !== result) throw new Error("写入后回读与预期不一致，请从备份恢复：" + backup);
  } catch (e) {
    if (tmp) { try { unlinkSync(tmp); } catch {} }
    if (backup && readFileSync(file, "utf8") === text) { try { unlinkSync(backup); } catch {} }  // 主文件未变时备份没有意义，只删本次自建的
    fail(e.message);
  }
  console.log(`已更新第 ${page.index} 页（${page.attrs.name || ""}），备份 ${backup}`);
}
