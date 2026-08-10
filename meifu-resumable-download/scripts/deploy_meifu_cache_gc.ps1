[CmdletBinding()]
param(
    [string]$HostName = 'meifu主机',
    [switch]$Apply
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

$skillRoot = Split-Path -Parent $PSScriptRoot
$assets = Join-Path $skillRoot 'assets'
$files = @(
    'meifu-download-cache-gc.sh',
    'meifu-download-cache-gc.service',
    'meifu-download-cache-gc.timer'
)
foreach ($name in $files) {
    $path = Join-Path $assets $name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "缺少部署资源：$path"
    }
}

$effective = @{}
& ssh -G $HostName | ForEach-Object {
    $key, $value = $_ -split ' ', 2
    if ($key -and $value) { $effective[$key.ToLowerInvariant()] = $value.Trim() }
}
if ($LASTEXITCODE -ne 0) { throw '无法读取 Meifu SSH 配置。' }
if ($effective['hostname'] -ne '192.129.128.54') {
    throw 'Meifu SSH 别名未解析到受控直连地址，已拒绝部署。'
}
if (($effective['proxycommand'] -and $effective['proxycommand'] -ne 'none') -or
    ($effective['proxyjump'] -and $effective['proxyjump'] -ne 'none')) {
    throw 'Meifu SSH 配置包含代理跳板，已拒绝部署。'
}

function Invoke-MeifuScript {
    param([Parameter(Mandatory)][string]$Script)
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Script))
    $result = & ssh -o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=30 -o ServerAliveCountMax=3 $HostName "printf %s '$encoded' | base64 -d | bash"
    if ($LASTEXITCODE -ne 0) { throw 'Meifu 远端命令失败。' }
    return $result
}

$inspection = @'
set -euo pipefail
printf 'cache-roots\n'
for root in /root/.cache/comfyui-models /root/.cache/meifu-downloads; do
  if [ -d "$root" ]; then du -sh "$root"; else printf 'absent %s\n' "$root"; fi
done
printf 'disk\n'
df -h /
printf 'units\n'
systemctl is-enabled meifu-download-cache-gc.timer 2>/dev/null || true
systemctl is-active meifu-download-cache-gc.timer 2>/dev/null || true
'@

if (-not $Apply) {
    Invoke-MeifuScript $inspection
    Write-Output '这是只读预检。确认后以 -Apply 部署定时缓存清理器。'
    exit 0
}

$remoteTemp = '/tmp/meifu-download-cache-gc-' + [Guid]::NewGuid().ToString('N')
Invoke-MeifuScript "set -euo pipefail; install -d -m 0700 '$remoteTemp'"
foreach ($name in $files) {
    $localPath = Join-Path $assets $name
    $encoded = [Convert]::ToBase64String([IO.File]::ReadAllBytes($localPath))
    Invoke-MeifuScript "set -euo pipefail; printf %s '$encoded' | base64 -d > '$remoteTemp/$name'"
}

$install = @'
set -euo pipefail
TMP='__TEMP__'
bash -n "$TMP/meifu-download-cache-gc.sh"
install -m 0750 "$TMP/meifu-download-cache-gc.sh" /usr/local/sbin/meifu-download-cache-gc
install -m 0644 "$TMP/meifu-download-cache-gc.service" /etc/systemd/system/meifu-download-cache-gc.service
install -m 0644 "$TMP/meifu-download-cache-gc.timer" /etc/systemd/system/meifu-download-cache-gc.timer
rm -rf -- "$TMP"
systemctl daemon-reload
systemctl enable --now meifu-download-cache-gc.timer
systemctl is-enabled meifu-download-cache-gc.timer
systemctl is-active meifu-download-cache-gc.timer
systemctl list-timers --all --no-pager meifu-download-cache-gc.timer
'@
Invoke-MeifuScript $install.Replace('__TEMP__', $remoteTemp)

$expectedHashes = @{}
foreach ($name in $files) {
    $expectedHashes[$name] = (Get-FileHash -LiteralPath (Join-Path $assets $name) -Algorithm SHA256).Hash.ToLowerInvariant()
}
$expectedHashes['meifu-download-cache-gc'] = $expectedHashes['meifu-download-cache-gc.sh']
$remoteHashes = Invoke-MeifuScript @'
set -euo pipefail
sha256sum /usr/local/sbin/meifu-download-cache-gc /etc/systemd/system/meifu-download-cache-gc.service /etc/systemd/system/meifu-download-cache-gc.timer
'@
foreach ($line in $remoteHashes) {
    $parts = $line -split '\s+', 2
    $fileName = Split-Path -Leaf $parts[1]
    if ($expectedHashes[$fileName] -ne $parts[0].ToLowerInvariant()) {
        throw "Meifu 已部署文件哈希不匹配：$fileName"
    }
}

Write-Output 'Meifu 定时缓存清理器已部署并启用；下一步可先执行远端 --dry-run 核对候选缓存。'
