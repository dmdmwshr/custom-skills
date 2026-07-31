[CmdletBinding()]
param(
    [Parameter()]
    [string]$InstallRoot = 'D:\Program_Files\Applio',

    [Parameter()]
    [ValidateRange(1, 65535)]
    [int]$Port = 6969
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

$root = [System.IO.Path]::GetFullPath($InstallRoot)
$configPath = Join-Path $root 'assets\config.json'
$runtimePath = Join-Path $root 'env\python.exe'
$launcherPath = Join-Path $root 'run-applio.bat'
$modelRoot = Join-Path $root 'logs'
$packageRoot = Join-Path $root 'model_packages'

function Get-LengthSum {
    param(
        [Parameter()]
        [object[]]$Files = @()
    )

    if ($Files.Count -eq 0) {
        return [int64]0
    }
    $measurement = $Files | Measure-Object -Property Length -Sum
    if ($null -eq $measurement.Sum) {
        return [int64]0
    }
    return [int64]$measurement.Sum
}

$version = $null
if (Test-Path -LiteralPath $configPath) {
    $config = Get-Content -Raw -Encoding UTF8 -LiteralPath $configPath | ConvertFrom-Json
    $version = $config.version
}

$pythonVersion = $null
if (Test-Path -LiteralPath $runtimePath) {
    $pythonVersion = (& $runtimePath --version 2>&1 | Out-String).Trim()
}

$processes = @(
    Get-CimInstance Win32_Process |
        Where-Object {
            $_.ExecutablePath -and
            ([System.IO.Path]::GetFullPath($_.ExecutablePath)).StartsWith(
                $root,
                [System.StringComparison]::OrdinalIgnoreCase
            )
        } |
        Select-Object ProcessId, Name, ExecutablePath, CommandLine
)

$listeners = @(
    Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue |
        Select-Object State, LocalAddress, LocalPort, OwningProcess
)

$modelFiles = @()
if (Test-Path -LiteralPath $modelRoot) {
    $modelFiles = @(
        Get-ChildItem -LiteralPath $modelRoot -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Extension -in '.pth', '.index' }
    )
}

$modelFolders = @(
    $modelFiles |
        Group-Object DirectoryName |
        ForEach-Object {
            $pth = @($_.Group | Where-Object Extension -eq '.pth')
            $index = @($_.Group | Where-Object Extension -eq '.index')
            [pscustomobject]@{
                directory = $_.Name
                pth = @($pth.Name)
                index = @($index.Name)
                paired = ($pth.Count -ge 1 -and $index.Count -ge 1)
                bytes = Get-LengthSum -Files @($_.Group)
            }
        }
)

$gpu = @()
$nvidiaSmi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if ($nvidiaSmi) {
    $gpuLines = @(
        & $nvidiaSmi.Source `
            --query-gpu=name,driver_version,memory.total,memory.free `
            --format=csv,noheader,nounits 2>$null
    )
    $gpu = @(
        $gpuLines | ForEach-Object {
            $parts = $_ -split ',\s*'
            [pscustomobject]@{
                name = $parts[0]
                driver = $parts[1]
                memory_total_mib = [int]$parts[2]
                memory_free_mib = [int]$parts[3]
            }
        }
    )
}

$modelBytes = Get-LengthSum -Files @($modelFiles)
$packageBytes = [int64]0
if (Test-Path -LiteralPath $packageRoot) {
    $packageFiles = @(
        Get-ChildItem -LiteralPath $packageRoot -Recurse -File -ErrorAction SilentlyContinue
    )
    $packageBytes = Get-LengthSum -Files $packageFiles
}

[pscustomobject]@{
    checked_at = (Get-Date).ToString('o')
    install = [pscustomobject]@{
        root = $root
        exists = Test-Path -LiteralPath $root
        version = $version
        launcher = $launcherPath
        launcher_exists = Test-Path -LiteralPath $launcherPath
        runtime = $runtimePath
        runtime_exists = Test-Path -LiteralPath $runtimePath
        python_version = $pythonVersion
    }
    service = [pscustomobject]@{
        url = "http://127.0.0.1:$Port"
        processes = $processes
        listeners = $listeners
    }
    storage = [pscustomobject]@{
        model_root = $modelRoot
        model_bytes = $modelBytes
        package_root = $packageRoot
        package_bytes = $packageBytes
    }
    models = $modelFolders
    gpu = $gpu
} | ConvertTo-Json -Depth 8
