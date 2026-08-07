#requires -Version 7.0

[CmdletBinding()]
param(
    [ValidateSet('Save', 'Info', 'Invoke', 'Remove')]
    [string]$Action = 'Info',

    [Parameter(Mandatory)]
    [string]$Name,

    [string]$Username,

    [string]$CommandPath,

    [string[]]$CommandArgumentList = @(),

    [string]$WorkingDirectory = (Get-Location).Path,

    [switch]$Force
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

$vaultName = 'LocalVault'

function Import-LocalVaultModules {
    foreach ($moduleName in @(
        'Microsoft.PowerShell.SecretManagement',
        'Microsoft.PowerShell.SecretStore'
    )) {
        if (-not (Get-Module -ListAvailable -Name $moduleName)) {
            throw "缺少 PowerShell 模块：$moduleName。请先按技能说明安装。"
        }
        Import-Module $moduleName -ErrorAction Stop
    }
}

function Ensure-LocalVault {
    $registered = Get-SecretVault -Name $vaultName -ErrorAction SilentlyContinue
    if ($null -eq $registered) {
        Register-SecretVault `
            -Name $vaultName `
            -ModuleName 'Microsoft.PowerShell.SecretStore' `
            -DefaultVault `
            -ErrorAction Stop
    }
}

function Get-ExistingSecretInfo {
    return Get-SecretInfo -Vault $vaultName -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -eq $Name }
}

function Save-LocalCredential {
    $existing = Get-ExistingSecretInfo
    if ($null -ne $existing -and -not $Force) {
        $answer = Read-Host "密钥 $Name 已存在，是否覆盖？输入 Y 确认"
        if ($answer -notmatch '^[Yy]$') {
            Write-Host '已取消覆盖，原密钥未改变。'
            return
        }
    }

    $credential = Get-Credential `
        -UserName $Username `
        -Message '请输入网站账号密码；密码不会显示，也不会写入文件'

    if ($null -eq $credential) {
        throw '没有取得凭据，未保存。'
    }

    try {
        Set-Secret -Name $Name -Vault $vaultName -Secret $credential -ErrorAction Stop
    }
    finally {
        $credential = $null
    }

    Write-Host "已保存密钥：$Name（PSCredential / $vaultName）。"
}

function Show-LocalCredentialInfo {
    $info = Get-ExistingSecretInfo
    if ($null -eq $info) {
        Write-Host "未找到密钥：$Name"
        return
    }

    Write-Host "名称：$($info.Name)"
    Write-Host "类型：$($info.Type)"
    Write-Host "密钥库：$($info.VaultName)"
}

function Resolve-ExecutablePath {
    param([Parameter(Mandatory)][string]$Path)

    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        return (Resolve-Path -LiteralPath $Path).Path
    }

    $command = Get-Command -Name $Path -ErrorAction Stop
    return $command.Source
}

function Invoke-LocalCredentialProcess {
    if ([string]::IsNullOrWhiteSpace($CommandPath)) {
        throw 'Invoke 动作必须提供 -CommandPath。'
    }
    if (-not (Test-Path -LiteralPath $WorkingDirectory -PathType Container)) {
        throw "工作目录不存在：$WorkingDirectory"
    }

    $credential = Get-Secret -Name $Name -Vault $vaultName -ErrorAction Stop
    if ($credential -isnot [System.Management.Automation.PSCredential]) {
        $credential = $null
        throw "密钥 $Name 不是 PSCredential 类型，请用 Save 动作重新保存。"
    }

    $networkCredential = $null
    $payloadJson = $null
    $process = $null
    try {
        $networkCredential = $credential.GetNetworkCredential()
        $payload = [ordered]@{
            username = [string]$credential.UserName
            password = [string]$networkCredential.Password
        }
        $payloadJson = $payload | ConvertTo-Json -Compress

        $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
        $startInfo.FileName = Resolve-ExecutablePath -Path $CommandPath
        $startInfo.UseShellExecute = $false
        $startInfo.CreateNoWindow = $true
        $startInfo.WorkingDirectory = (Resolve-Path -LiteralPath $WorkingDirectory).Path
        $startInfo.RedirectStandardInput = $true
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true
        foreach ($argument in $CommandArgumentList) {
            [void]$startInfo.ArgumentList.Add($argument)
        }

        $process = [System.Diagnostics.Process]::new()
        $process.StartInfo = $startInfo
        if (-not $process.Start()) {
            throw "无法启动本地程序：$CommandPath"
        }

        $process.StandardInput.WriteLine($payloadJson)
        $process.StandardInput.Close()
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()

        # 防止可信本地运行器误把密码写入输出；不记录或回显密钥值。
        $safeStdout = $stdout.Replace([string]$networkCredential.Password, '[REDACTED]')
        $safeStderr = $stderr.Replace([string]$networkCredential.Password, '[REDACTED]')
        if (-not [string]::IsNullOrWhiteSpace($safeStdout)) {
            Write-Output $safeStdout.TrimEnd()
        }
        if (-not [string]::IsNullOrWhiteSpace($safeStderr)) {
            Write-Warning $safeStderr.TrimEnd()
        }

        if ($process.ExitCode -ne 0) {
            throw "本地程序退出码为 $($process.ExitCode)。"
        }
    }
    finally {
        if ($null -ne $process) {
            $process.Dispose()
        }
        $payload = $null
        $payloadJson = $null
        $networkCredential = $null
        $credential = $null
    }
}

function Remove-LocalCredential {
    $existing = Get-ExistingSecretInfo
    if ($null -eq $existing) {
        Write-Host "未找到密钥：$Name"
        return
    }

    if (-not $Force) {
        $answer = Read-Host "确认删除密钥 $Name？输入 DELETE 确认"
        if ($answer -cne 'DELETE') {
            Write-Host '已取消删除。'
            return
        }
    }

    Remove-Secret -Name $Name -Vault $vaultName -Confirm:$false -ErrorAction Stop
    Write-Host "已删除密钥：$Name"
}

Import-LocalVaultModules
Ensure-LocalVault

switch ($Action) {
    'Save' { Save-LocalCredential }
    'Info' { Show-LocalCredentialInfo }
    'Invoke' { Invoke-LocalCredentialProcess }
    'Remove' { Remove-LocalCredential }
}
