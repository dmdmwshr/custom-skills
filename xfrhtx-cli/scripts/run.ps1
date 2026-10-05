param(
    [string]$EntryPoint,
    [string]$InstallRoot,
    [Parameter(Position=0, ValueFromRemainingArguments=$true)]
    [string[]]$ToolArguments
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$env:PYTHONUTF8 = '1'
try {
    if ($env:OS -ne 'Windows_NT') { throw 'This skill requires native Windows.' }
    if (-not $EntryPoint) {
        $stateDirectory = if ($InstallRoot) { $InstallRoot } else { Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'XFRHTXReader' }
        $receipt = Join-Path $stateDirectory 'installation.json'
        if (Test-Path -LiteralPath $receipt -PathType Leaf) {
            $entryInfo = [IO.File]::ReadAllText($receipt, [Text.Encoding]::UTF8) | ConvertFrom-Json
            $candidate = [string]$entryInfo.entry_point
            if ($candidate -and -not [IO.Path]::IsPathRooted($candidate)) {
                $base = [IO.Path]::GetFullPath($stateDirectory).TrimEnd('\') + '\'
                $candidate = [IO.Path]::GetFullPath((Join-Path $base $candidate))
                if (-not $candidate.StartsWith($base, [StringComparison]::OrdinalIgnoreCase)) { throw 'Relative receipt entry is outside the installation.' }
            }
            if ($entryInfo.installed -and $candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) { $EntryPoint = $candidate }
        }
        if (-not $EntryPoint -and $InstallRoot -and (Test-Path -LiteralPath (Join-Path $InstallRoot 'portable-manifest.json') -PathType Leaf)) {
            $portable = Join-Path $InstallRoot 'xfrhtx.exe'
            if (Test-Path -LiteralPath $portable -PathType Leaf) { $EntryPoint = $portable }
        }
        if (-not $EntryPoint -and $InstallRoot) { throw 'No initialized tool found at InstallRoot.' }
        if (-not $EntryPoint) {
            $candidates = @(Get-Command xfrhtx.exe -All -CommandType Application -ErrorAction SilentlyContinue | Where-Object { -not (Test-Path -LiteralPath (Join-Path (Split-Path -Parent $_.Source) 'jmtoolkit.dll')) } | Select-Object -ExpandProperty Source -Unique)
            if ($candidates.Count -eq 1) { $EntryPoint = $candidates[0] }
            elseif ($candidates.Count -gt 1) { throw 'Multiple CLI installations found; use EntryPoint to select one.' }
        }
        if (-not $EntryPoint) {
            $known = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.local\bin\xfrhtx.exe'
            if (Test-Path -LiteralPath $known -PathType Leaf) { $EntryPoint = $known }
        }
    }
    if (-not $EntryPoint -or -not (Test-Path -LiteralPath $EntryPoint -PathType Leaf)) { throw 'xfrhtx is not initialized. Initialize the separate tool as described in references/setup.md.' }
    if (Test-Path -LiteralPath (Join-Path (Split-Path -Parent $EntryPoint) 'jmtoolkit.dll')) { throw 'EntryPoint identifies the GUI client, not the reader CLI.' }
    $version = & $EntryPoint --version 2>$null
    if ($LASTEXITCODE -ne 0 -or ($version -join '') -notmatch '^xfrhtx [0-9]+\.[0-9]+\.[0-9]+$') { throw 'EntryPoint is not a verified xfrhtx reader.' }
    if ([version](($version -join '').Substring(7)) -lt [version]'0.3.0') { throw 'xfrhtx-reader 0.3.0 or newer is required; initialize the tool separately.' }
    if (-not $ToolArguments) { $ToolArguments = @('status', '--json') }
    & $EntryPoint @ToolArguments
    exit $LASTEXITCODE
} catch {
    @{schema_version='1.0';ok=$false;error=@{code='tool_unavailable';message=$_.Exception.Message}} | ConvertTo-Json -Depth 4 -Compress
    exit 2
}
