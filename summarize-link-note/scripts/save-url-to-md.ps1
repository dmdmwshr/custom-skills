[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InputUrl,

    [Parameter(Mandatory = $false)]
    [string]$OutputName
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

$configFile = Join-Path (Split-Path $PSScriptRoot -Parent) "references/local-config.json"
$localConfig = Get-Content -LiteralPath $configFile -Raw -Encoding UTF8 | ConvertFrom-Json
$outputDir = $localConfig.output_dir
$nodeExecutable = $localConfig.node_path
$cliExecutable = $localConfig.cli_path
if (-not (Test-Path -LiteralPath $nodeExecutable) -or -not (Test-Path -LiteralPath $cliExecutable)) { throw "Configured summarize runtime is missing" }

function Test-PdfInput {
    param([string]$Value)

    if ($Value -match '(?i)\.pdf($|[?#])') {
        return $true
    }

    try {
        $uri = [Uri]$Value
        return $uri.AbsolutePath -match '(?i)\.pdf$'
    } catch {
        return $false
    }
}

function New-SafeName {
    param(
        [string]$Url,
        [string]$PreferredName
    )

    if (-not [string]::IsNullOrWhiteSpace($PreferredName)) {
        $base = $PreferredName
    } else {
        try {
            $uri = [Uri]$Url
            $hostName = $uri.Host
            $leaf = [System.IO.Path]::GetFileNameWithoutExtension($uri.AbsolutePath.Trim('/'))
            if ([string]::IsNullOrWhiteSpace($leaf)) {
                $leaf = "page"
            }
            $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
            $base = "$hostName-$leaf-$stamp"
        } catch {
            $base = "summarize-output-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
        }
    }

    $invalid = [System.IO.Path]::GetInvalidFileNameChars()
    foreach ($char in $invalid) {
        $base = $base.Replace($char, '-')
    }
    $base = $base -replace '\s+', '-'
    $base = $base.Trim('.').Trim()
    if ([string]::IsNullOrWhiteSpace($base)) {
        $base = "summarize-output-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    }
    if (-not $base.EndsWith('.md', [System.StringComparison]::OrdinalIgnoreCase)) {
        $base = "$base.md"
    }
    return $base
}

if (Test-PdfInput -Value $InputUrl) {
    throw "PDF input is excluded from summarize-link-note. Use the existing PDF conversion workflow instead."
}

New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
$fileName = New-SafeName -Url $InputUrl -PreferredName $OutputName
$outputPath = Join-Path $outputDir $fileName

$summarizeArgs = @(
    $InputUrl,
    "--format", "md",
    "--markdown-mode", "readability",
    "--length", "long",
    "--plain",
    "--stream", "off",
    "--metrics", "off"
)

$content = & $nodeExecutable $cliExecutable @summarizeArgs
$exitCode = $LASTEXITCODE
if ($exitCode -ne 0) {
    throw "summarize failed with exit code: $exitCode"
}

$content | Set-Content -Encoding UTF8 -Path $outputPath

Write-Output $outputPath
