#!/usr/bin/env node
// 本地构造 draw.io 网页编辑器链接（旧 open_drawio_* 的链接输出等价物）。只打印链接：不打开浏览器、不联网、不外发。
// 链接把图内容压缩后放在 # 片段里，浏览器打开后才由 draw.io 网页读取；用户明确要求再打开（默认 Ego）。
// 格式同 @drawio/mcp 1.7.0 / 官方 Skill：XML 用 #R<压缩>，Mermaid/CSV 用 #create={...}。
// 用法：node drawio_url.mjs <文件> [--type xml|mermaid|csv] [--lightbox] [--dark true|false|auto] [--border N]
// 类型默认按扩展名：.drawio/.xml→xml，.mmd/.mermaid→mermaid，.csv→csv。DRAWIO_BASE_URL 可指向自托管实例。
import { readFileSync } from "node:fs";
import { deflateRawSync } from "node:zlib";
import { extname } from "node:path";
import { checkXml } from "./xml_wellformed.mjs";

const args = process.argv.slice(2);
const file = args.find((a) => !a.startsWith("--") && args[args.indexOf(a) - 1] !== "--type" && args[args.indexOf(a) - 1] !== "--dark" && args[args.indexOf(a) - 1] !== "--border");
const opt = (name, def) => { const k = args.indexOf(name); return k >= 0 ? args[k + 1] : def; };
if (!file) { process.stderr.write("用法：drawio_url.mjs <文件> [--type xml|mermaid|csv] [--lightbox] [--dark true|false|auto] [--border N]\n"); process.exit(1); }
const ext = extname(file).toLowerCase();
const type = opt("--type", { ".drawio": "xml", ".xml": "xml", ".mmd": "mermaid", ".mermaid": "mermaid", ".csv": "csv" }[ext]);
if (!["xml", "mermaid", "csv"].includes(type)) { process.stderr.write("无法判断类型，请用 --type xml|mermaid|csv\n"); process.exit(1); }
const content = readFileSync(file, "utf8");
if (!content.trim()) { process.stderr.write("文件为空\n"); process.exit(1); }
if (type === "xml") { try { checkXml(content); } catch (e) { process.stderr.write(e.message + "\n"); process.exit(1); } }
const dark = opt("--dark", "auto");
const rawBorder = opt("--border", "10");
if (typeof rawBorder !== "string" || !/^\d+$/.test(rawBorder) || !Number.isSafeInteger(Number(rawBorder))) {
  process.stderr.write("--border 必须是非负整数\n"); process.exit(1);
}
const border = String(Number(rawBorder));
const lightbox = args.includes("--lightbox");

const compressed = deflateRawSync(Buffer.from(encodeURIComponent(content))).toString("base64");
const params = new URLSearchParams();
if (lightbox) { params.set("lightbox", "1"); } else { params.set("grid", "0"); params.set("pv", "0"); }
if (dark === "true") params.set("dark", "1");
params.set("border", border);
params.set("edit", "_blank");
const hash = type === "xml"
  ? "#R" + encodeURIComponent(compressed)
  : "#create=" + encodeURIComponent(JSON.stringify({ type, compressed: true, data: compressed, ...(type === "mermaid" ? { version: "12" } : {}) }));
console.log((process.env.DRAWIO_BASE_URL || "https://app.diagrams.net/") + "?" + params.toString() + hash);
