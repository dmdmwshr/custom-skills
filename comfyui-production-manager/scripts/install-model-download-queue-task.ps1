[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

throw 'ComfyUI 旧模型下载任务已迁移到通用 Meifu 下载队列。此脚本不会创建、更新或启动旧任务；请在获得明确授权后使用 meifu-resumable-download 的受管后台任务。'
