# 第三方来源

- `xml-essentials.md`、`xml-reference.md`、`mermaid-reference.md`：来自 `@drawio/mcp` 1.7.0（jgraph/drawio-mcp，shared/），Apache-2.0，原样保存。
- `scripts/vendor/shape-search.js`、`scripts/vendor/icon-search.js`：来自同一包 `src/`，Apache-2.0，原样保存。
- `scripts/repair_drawio.mjs`：官方 drawio Skill（jgraph/drawio-mcp plugins/codex/drawio/skills/drawio）中“修复遍”的脚本化；`scripts/drawio_pages.mjs`：移植自同一包 `src/pages.js`（去掉 pako 依赖、加写前备份）。
- 上游仓库：https://github.com/jgraph/drawio-mcp ，https://github.com/jgraph/drawio-desktop 。
- `scripts/xml_wellformed.mjs`、`scripts/drawio_url.mjs`：本 Skill 自写；URL 格式取自 @drawio/mcp 1.7.0 / 官方 Skill（Apache-2.0）。
