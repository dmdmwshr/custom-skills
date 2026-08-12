#requires -Version 7.0

[CmdletBinding()]
param(
    [switch]$RefreshDependencies
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

$expectedPlaywrightVersion = '1.62.1'
$skillRoot = Split-Path -Parent $PSScriptRoot
$runtimeRoot = Join-Path $env:LOCALAPPDATA 'LocalVaultCredentials\browser-login-runtime'
$manifestSource = Join-Path $skillRoot 'browser-login\package.json'
$lockSource = Join-Path $skillRoot 'browser-login\package-lock.json'
$runnerSource = Join-Path $PSScriptRoot 'local-web-login-runner.mjs'

foreach ($requiredFile in @($manifestSource, $lockSource, $runnerSource)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "技能文件缺失：$requiredFile"
    }
}

$node = Get-Command node -ErrorAction Stop
$nodeVersionText = (& $node.Source --version).Trim()
if ($nodeVersionText -notmatch '^v(?<major>\d+)\.') {
    throw "无法识别 Node.js 版本：$nodeVersionText"
}
if ([int]$Matches.major -lt 20) {
    throw "浏览器运行器至少需要 Node.js 20；当前版本为：$nodeVersionText"
}

New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
$runtimeManifest = Join-Path $runtimeRoot 'package.json'
$runtimeLock = Join-Path $runtimeRoot 'package-lock.json'
$runtimeRunner = Join-Path $runtimeRoot 'local-web-login-runner.mjs'
Copy-Item -LiteralPath $manifestSource -Destination $runtimeManifest -Force
Copy-Item -LiteralPath $lockSource -Destination $runtimeLock -Force
Copy-Item -LiteralPath $runnerSource -Destination $runtimeRunner -Force

$runtimePackage = Join-Path $runtimeRoot 'node_modules\playwright\package.json'
$installedVersion = $null
if (Test-Path -LiteralPath $runtimePackage -PathType Leaf) {
    $installedVersion = (Get-Content -LiteralPath $runtimePackage -Encoding UTF8 -Raw | ConvertFrom-Json).version
}

if ($RefreshDependencies -or $installedVersion -ne $expectedPlaywrightVersion) {
    $npm = Get-Command npm -ErrorAction Stop
    & $npm.Source ci --omit=dev --ignore-scripts --prefix $runtimeRoot
    if ($LASTEXITCODE -ne 0) {
        throw "浏览器运行时依赖安装失败，退出码：$LASTEXITCODE"
    }
}

if (-not (Test-Path -LiteralPath $runtimePackage -PathType Leaf)) {
    throw '浏览器运行时依赖未安装完成。'
}
$verifiedVersion = (Get-Content -LiteralPath $runtimePackage -Encoding UTF8 -Raw | ConvertFrom-Json).version
if ($verifiedVersion -ne $expectedPlaywrightVersion) {
    throw "Playwright 版本不匹配：期望 $expectedPlaywrightVersion，实际 $verifiedVersion"
}

[pscustomobject]@{
    RuntimeReady = $true
    NodeVersion = $nodeVersionText
    PlaywrightVersion = $verifiedVersion
    RunnerHash = (Get-FileHash -LiteralPath $runtimeRunner -Algorithm SHA256).Hash
} | Format-List
