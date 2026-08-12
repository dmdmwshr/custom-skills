#requires -Version 7.0

[CmdletBinding()]
param(
    [ValidateSet('Save', 'Info', 'Login')]
    [string]$Action = 'Info',

    [Parameter(Mandatory)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')]
    [string]$AccountKey,

    [switch]$Force,

    [ValidateRange(30, 600)]
    [int]$CaptchaTimeoutSeconds = 180,

    [ValidateRange(0, 600)]
    [int]$KeepBrowserOpenSeconds = 20
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

$skillRoot = Split-Path -Parent $PSScriptRoot
$mappingPath = Join-Path $skillRoot 'local\accounts.json'
$credentialScript = Join-Path $PSScriptRoot 'Manage-LocalVaultCredential.ps1'
$runtimeRoot = Join-Path $env:LOCALAPPDATA 'LocalVaultCredentials\browser-login-runtime'
$runtimeRunner = Join-Path $runtimeRoot 'local-web-login-runner.mjs'
$runtimePackage = Join-Path $runtimeRoot 'node_modules\playwright\package.json'

function Get-AccountDefinition {
    if (-not (Test-Path -LiteralPath $mappingPath -PathType Leaf)) {
        throw "找不到本地站点映射：$mappingPath"
    }

    $mapping = Get-Content -LiteralPath $mappingPath -Encoding UTF8 -Raw | ConvertFrom-Json
    $account = $mapping.PSObject.Properties[$AccountKey].Value
    if ($null -eq $account) {
        throw "站点映射不存在：$AccountKey"
    }

    $allowedFields = @('url', 'username', 'secretName', 'captcha')
    $actualFields = @($account.PSObject.Properties.Name)
    $unexpectedFields = @($actualFields | Where-Object { $_ -notin $allowedFields })
    $missingFields = @($allowedFields | Where-Object { $_ -notin $actualFields })
    if ($unexpectedFields.Count -gt 0 -or $missingFields.Count -gt 0) {
        throw '站点映射字段必须且只能为 url、username、secretName、captcha；映射中不能保存密码或其他敏感数据。'
    }

    $uri = $null
    if (-not [uri]::TryCreate([string]$account.url, [System.UriKind]::Absolute, [ref]$uri) -or $uri.Scheme -notin @('http', 'https')) {
        throw '站点映射中的 url 必须是完整的 HTTP 或 HTTPS 地址。'
    }
    if ([string]::IsNullOrWhiteSpace([string]$account.username)) {
        throw '站点映射中的 username 不能为空。'
    }
    if ([string]$account.secretName -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$') {
        throw '站点映射中的 secretName 不符合 LocalVault 名称规则。'
    }
    if ($account.captcha -isnot [bool]) {
        throw '站点映射中的 captcha 必须是 true 或 false。'
    }

    return $account
}

function Invoke-CredentialScript {
    param([Parameter(Mandatory)][string[]]$Arguments)

    & $credentialScript @Arguments
}

$account = Get-AccountDefinition
if (-not (Test-Path -LiteralPath $credentialScript -PathType Leaf)) {
    throw "LocalVault 通用入口缺失：$credentialScript"
}

switch ($Action) {
    'Save' {
        $saveArguments = @('-Action', 'Save', '-Name', [string]$account.secretName, '-Username', [string]$account.username)
        if ($Force) {
            $saveArguments += '-Force'
        }
        Invoke-CredentialScript -Arguments $saveArguments
    }
    'Info' {
        Invoke-CredentialScript -Arguments @('-Action', 'Info', '-Name', [string]$account.secretName)
        [pscustomobject]@{
            AccountKey = $AccountKey
            Url = [string]$account.url
            Username = [string]$account.username
            SecretName = [string]$account.secretName
            CaptchaRequired = [bool]$account.captcha
            BrowserRuntimeReady = (Test-Path -LiteralPath $runtimePackage -PathType Leaf)
        } | Format-List
    }
    'Login' {
        if (-not (Test-Path -LiteralPath $runtimeRunner -PathType Leaf) -or -not (Test-Path -LiteralPath $runtimePackage -PathType Leaf)) {
            throw "浏览器本地运行时未就绪。请先执行：$skillRoot\scripts\Initialize-LocalVaultBrowserRuntime.ps1"
        }

        $node = Get-Command node -ErrorAction Stop
        $runnerArguments = @(
            $runtimeRunner,
            '--url', [string]$account.url,
            '--captcha', ([string][bool]$account.captcha).ToLowerInvariant(),
            '--captcha-timeout-seconds', [string]$CaptchaTimeoutSeconds,
            '--keep-browser-open-seconds', [string]$KeepBrowserOpenSeconds
        )
        $invokeArguments = @(
            '-Action', 'Invoke',
            '-Name', [string]$account.secretName,
            '-CommandPath', $node.Source,
            '-CommandArgumentList'
        )
        $invokeArguments += $runnerArguments
        $invokeArguments += @(
            '-WorkingDirectory', $runtimeRoot
        )
        Invoke-CredentialScript -Arguments $invokeArguments
    }
}
