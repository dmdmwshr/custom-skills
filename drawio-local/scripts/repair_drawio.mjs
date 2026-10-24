#!/usr/bin/env node
// 官方 drawio Skill 的“修复遍”（jgraph/drawio-mcp，Apache-2.0）：剥离图外文字、转义属性值里的 < 与孤立 &、
// 修正默认层自指父级。对格式正确的文件不做任何改动。用法：repair_drawio.mjs <file.drawio>
import { readFileSync, writeFileSync } from "node:fs";
const file = process.argv[2];
if (!file) { process.stderr.write("用法：repair_drawio.mjs <file.drawio>\n"); process.exit(1); }
let xml = readFileSync(file, "utf8");
const diagram = xml.match(/<mxfile[\s\S]*?<\/mxfile>|<mxGraphModel[\s\S]*?<\/mxGraphModel>/);
if (diagram && xml.trim() !== diagram[0]) xml = diagram[0];
xml = xml.replace(/="([^"]*)"/g, (m, v) => "=\"" + v.replace(/</g, "&lt;").replace(/&(?!(?:amp|lt|gt|quot|apos|#[0-9]+|#x[0-9a-fA-F]+);)/g, "&amp;") + "\"");
xml = xml.replace(/<mxCell id="([^"]+)" parent="\1"\s*\/>/g, "<mxCell id=\"$1\" parent=\"0\"/>");
writeFileSync(file, xml);
