// 标准 XML 解析桥：只启动本机既有 Python3，不联网、不安装依赖。
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
const script = fileURLToPath(new URL("./xml_parser.py", import.meta.url));
export function checkXml(input, { stripDeclaration = false, diagrams = false } = {}) {
  const python = process.env.DRAWIO_PYTHON || (process.platform === "win32"
    ? "C:/Users/12070/AppData/Local/Programs/Python/Python312/python.exe" : "python3");
  const result = spawnSync(python, ["-I", "-B", "-X", "utf8", script], {
    input: JSON.stringify({ xml: String(input), stripDeclaration, diagrams }), encoding: "utf8",
    windowsHide: true, timeout: 30000, maxBuffer: 128 * 1024 * 1024,
  });
  if (result.error) throw new Error(`本机 Python3 XML 校验不可用：${result.error.message}（可用 DRAWIO_PYTHON 指定既有解释器）`);
  let parsed;
  try { parsed = JSON.parse(result.stdout); }
  catch { throw new Error("Python3 XML 校验未返回有效结果：" + (result.stderr || "empty output").slice(0, 400)); }
  if (result.status !== 0 || parsed.error) throw new Error(parsed.error || "XML 解析失败");
  return parsed;
}
