[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$VideoUrl,

    [Parameter(Mandatory = $false)]
    [string]$Platform,

    [Parameter(Mandatory = $false)]
    [string]$ProviderId,

    [Parameter(Mandatory = $false)]
    [string]$ModelName
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

$target = Join-Path $PSScriptRoot "save-video-note.ps1"
$invokeArgs = @{
    VideoUrl = $VideoUrl
}

if (-not [string]::IsNullOrWhiteSpace($Platform)) {
    $invokeArgs["Platform"] = $Platform
}
if (-not [string]::IsNullOrWhiteSpace($ProviderId)) {
    $invokeArgs["ProviderId"] = $ProviderId
}
if (-not [string]::IsNullOrWhiteSpace($ModelName)) {
    $invokeArgs["ModelName"] = $ModelName
}

& $target @invokeArgs
