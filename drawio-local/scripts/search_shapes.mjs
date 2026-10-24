#!/usr/bin/env node
// draw.io 形状/图标检索（旧 MCP search_shapes 的等价入口）。算法为 @drawio/mcp 1.7.0 的 shape-search.js / icon-search.js
// 原样副本（vendor/，Apache-2.0）。形状索引（约 4.8 MB）取自官方 jsDelivr，按 ETag 缓存在用户缓存目录；图标补充走官方
// icons.diagrams.net，设 DRAWIO_ICON_SERVICE_URL=off 可关闭。仅发出检索关键词，不上传任何图表内容。
// 用法：node search_shapes.mjs "aws lambda" [limit]
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";
import { buildTagMap } from "./vendor/shape-search.js";
import { searchShapesAndIcons, resolveIconServiceUrl } from "./vendor/icon-search.js";

const query = process.argv[2];
const limit = Math.min(Number(process.argv[3]) || 10, 50);
if (!query) { process.stderr.write("用法：search_shapes.mjs <关键词> [数量]\n"); process.exit(1); }

const URL_ = process.env.DRAWIO_SHAPE_INDEX_URL || "https://cdn.jsdelivr.net/gh/jgraph/drawio-mcp@main/shape-search/search-index.json";
const base = process.env.LOCALAPPDATA || process.env.XDG_CACHE_HOME || join(homedir(), ".cache");
const dir = join(base, "drawio-skill");
const cacheFile = join(dir, "shape-index.json"), etagFile = join(dir, "shape-index.etag");

async function loadIndex() {
  if (!/^https?:\/\//i.test(URL_)) return JSON.parse(readFileSync(URL_, "utf8"));
  const headers = {};
  if (existsSync(cacheFile) && existsSync(etagFile)) headers["If-None-Match"] = readFileSync(etagFile, "utf8");
  try {
    const res = await fetch(URL_, { headers, signal: AbortSignal.timeout(20000) });
    if (res.status === 304) return JSON.parse(readFileSync(cacheFile, "utf8"));
    if (res.ok) {
      const text = await res.text();
      const index = JSON.parse(text);
      if (!Array.isArray(index) || index.length === 0) throw new Error("形状索引格式异常");
      mkdirSync(dir, { recursive: true });
      writeFileSync(cacheFile, text); if (res.headers.get("etag")) writeFileSync(etagFile, res.headers.get("etag"));
      return index;
    }
  } catch (e) { if (!existsSync(cacheFile)) throw e; }
  return JSON.parse(readFileSync(cacheFile, "utf8"));
}

try {
  const index = await loadIndex();
  const results = await searchShapesAndIcons(index, buildTagMap(index), query, limit, { serviceUrl: resolveIconServiceUrl(process.env.DRAWIO_ICON_SERVICE_URL) });
  console.log(results.length ? JSON.stringify(results, null, 2) : `No shapes found for query: ${query}`);
} catch (e) {
  process.stderr.write(`Error: 无法加载形状索引（${e.message}）。可设 DRAWIO_SHAPE_INDEX_URL 指向本地文件。\n`); process.exit(1);
}
