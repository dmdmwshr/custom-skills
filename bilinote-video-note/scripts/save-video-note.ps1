[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$VideoUrl,

    [Parameter(Mandatory = $false)]
    [string]$Platform,

    [Parameter(Mandatory = $false)]
    [string]$ProviderId,

    [Parameter(Mandatory = $false)]
    [string]$ModelName,

    [string]$VerifiedDownloadReceipt,

    [string]$ProgressReceiptPath,

    [string]$ResumeProgressReceiptPath
)

# Compatibility entry. The single implementation is video-intake/scripts/video_intake.py (note).
# Parameters, JSON output and exit behavior (0 success / 1 failure with JSON) match the legacy script.
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

function Find-Core {
    $candidates = @()
    if ($env:VIDEO_INTAKE_CORE) { $candidates += $env:VIDEO_INTAKE_CORE }
    $candidates += (Join-Path $PSScriptRoot "..\..\video-intake\scripts\video_intake.py")
    $candidates += (Join-Path $env:USERPROFILE ".codex\skills\video-intake\scripts\video_intake.py")
    $candidates += (Join-Path $env:USERPROFILE ".cc-switch\skills\video-intake\scripts\video_intake.py")
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return (Get-Item -LiteralPath $candidate).FullName }
    }
    return $null
}

function Find-Python {
    if ($env:VIDEO_INTAKE_PYTHON -and (Test-Path -LiteralPath $env:VIDEO_INTAKE_PYTHON -PathType Leaf)) { return @($env:VIDEO_INTAKE_PYTHON) }
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) { return @($py.Source, "-3") }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) { return @($python.Source) }
    return $null
}

$core = Find-Core
$python = @(Find-Python)
if (-not $core -or $python.Count -eq 0) {
    [ordered]@{
        success = $false; platform = $null; source_id = $null; task_id = $null; note_path = $null
        attachments_dir = $null; backend_result_path = $null
        message = "video-intake core or Python was not found; install the video-intake skill."
    } | ConvertTo-Json -Depth 5
    exit 1
}

$forward = @($core, "note", "--video-url", $VideoUrl)
if (-not [string]::IsNullOrWhiteSpace($Platform)) { $forward += @("--platform", $Platform) }
if (-not [string]::IsNullOrWhiteSpace($ProviderId)) { $forward += @("--provider-id", $ProviderId) }
if (-not [string]::IsNullOrWhiteSpace($ModelName)) { $forward += @("--model-name", $ModelName) }
if ($VerifiedDownloadReceipt) { $forward += @("--verified-download-receipt", $VerifiedDownloadReceipt) }
if ($ProgressReceiptPath) { $forward += @("--progress-receipt", $ProgressReceiptPath) }
if ($ResumeProgressReceiptPath) { $forward += @("--resume-progress-receipt", $ResumeProgressReceiptPath) }

$env:PYTHONUTF8 = "1"
$prefix = @($python | Select-Object -Skip 1)
& $python[0] @prefix @forward
exit $LASTEXITCODE
