---
name: meifu-resumable-download
description: 通过 Meifu 分块缓存稳定下载任意 HTTPS 大文件，并在本机断点续传、校验和原子落盘。用户直接提供下载链接并要求下载大模型、模型、安装包、数据集或其他大文件，或明确表示本机网络不稳定、下载反复中断时使用；也供 ComfyUI 模型管理在完成类别和来源审查后调用。
---

# Meifu 断点下载

## 作用与边界

使用受控链路“来源链接 → Meifu 临时分块 → 本机 SFTP 续传 → 完整文件校验 → 原子落盘”。它不是公开网页代理，也不提供任意人可访问的转发接口。

- 仅处理当前用户直接提供或明确批准的 HTTP(S) 直链；默认仅允许 HTTPS。
- 用户明确要求下载大模型，或说明网络不稳定、下载中断时，优先使用本 skill；普通小文件、网页浏览、Git 克隆和软件包管理不要因此绕行。
- 要求明确的绝对输出路径；目标已有不同文件时拒绝覆盖。
- 模型、安装包、可执行文件和依赖包优先要求官方 SHA-256。未提供时可计算结果哈希，但不得把“已下载”表述为“已验证来源”。
- 含签名参数或访问令牌的链接视为敏感信息：使用 `--url-stdin__ 传入，不写入可提交文件、日志或状态文件。
- 不要把 Meifu 暴露为公共下载 API；不要改用 ProxyJump、ProxyCommand 或其他跳板。

## 使用流程

1. 先确认链接、输出位置、大小级别和是否有官方 SHA-256。对于 ComfyUI 模型，先由 `comfyui-production-manager__ 确认类别、版本、许可证和最终目录。
2. 不加 `--execute__ 运行脚本，确认脱敏来源、输出路径、传输方式和续传状态。
3. 需要检查 Range 支持、远端和本机空间时使用 `--probe-only__；它不会创建缓存。
4. 只有用户已直接批准下载时才加 `--execute__。发生短暂网络错误时保留状态和当前远端块；使用相同链接与输出路径重跑即可续传。
5. 完成后核对输出大小、SHA-256 和“来源已验证/仅计算哈希”状态；不要自动执行、安装或导入下载文件。

常用调用形式：

    python scripts/download_via_meifu.py --url "https://example.invalid/file.bin" --output "D:\Downloads\file.bin"
    python scripts/download_via_meifu.py --url "https://example.invalid/file.bin" --output "D:\Downloads\file.bin" --probe-only
    python scripts/download_via_meifu.py --url "https://example.invalid/file.bin" --output "D:\Downloads\file.bin" --sha256 <官方哈希> --execute

签名链接不放入命令行：

    <将链接通过标准输入传入> | python scripts/download_via_meifu.py --url-stdin --output "D:\Downloads\file.bin" --execute

## 与 ComfyUI skill 的分工

- `comfyui-production-manager__：判断缺失模型、模型类别、许可证、目标目录、模型清单和显存影响。
- 本 skill：只负责通用传输、续传、缓存和文件完整性。
- 已存在的 ComfyUI 模型队列只用于兼容其已创建的队列；新的通用下载不要写入模型队列，也不要绕过模型 skill 的模型审查。

## Meifu 缓存维护

部署或更新服务端清理器前，先运行：

    powershell -ExecutionPolicy Bypass -File scripts/deploy_meifu_cache_gc.ps1

确认 dry-run 后，用户授权的部署使用：

    powershell -ExecutionPolicy Bypass -File scripts/deploy_meifu_cache_gc.ps1 -Apply

清理器只管理两个固定缓存根，默认在无活动 72 小时后回收残留任务；当 Meifu 可用空间低于 8 GiB 时，再按最早无活动任务回收。详见 `references/transport-and-cache.md__。

## 资源

- `scripts/download_via_meifu.py__：通用单文件分块下载器。
- `scripts/deploy_meifu_cache_gc.ps1__：部署或更新 Meifu 定时清理器。
- `assets/meifu-download-cache-gc.*__：服务端清理脚本与 systemd 单元模板。
- `references/transport-and-cache.md__：传输、缓存、续传和安全边界。
