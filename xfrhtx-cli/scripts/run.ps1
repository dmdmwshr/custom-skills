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
            if ($entryInfo.installed -and (Test-Path -LiteralPath $entryInfo.entry_point -PathType Leaf)) { $EntryPoint = $entryInfo.entry_point }
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
    if (-not $EntryPoint -or -not (Test-Path -LiteralPath $EntryPoint -PathType Leaf)) { throw 'xfrhtx is not initialized. Use scripts/initialize.ps1 when installation is authorized.' }
    if (Test-Path -LiteralPath (Join-Path (Split-Path -Parent $EntryPoint) 'jmtoolkit.dll')) { throw 'EntryPoint identifies the GUI client, not the reader CLI.' }
    $version = & $EntryPoint --version 2>$null
    if ($LASTEXITCODE -ne 0 -or ($version -join '') -notmatch '^xfrhtx [0-9]+\.[0-9]+\.[0-9]+$') { throw 'EntryPoint is not a verified xfrhtx reader.' }
    if ([version](($version -join '').Substring(7)) -lt [version]'0.2.0') { throw 'xfrhtx-reader 0.2.0 or newer is required; initialize the tool separately.' }
    if (-not $ToolArguments) { $ToolArguments = @('status', '--json') }
    & $EntryPoint @ToolArguments
    exit $LASTEXITCODE
} catch {
    @{schema_version='1.0';ok=$false;error=@{code='tool_unavailable';message=$_.Exception.Message}} | ConvertTo-Json -Depth 4 -Compress
    exit 2
}
