$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

$GraphitiMcpDir = 'D:\Program_Files\graphiti\mcp_server'
$Healthcheck = Join-Path $GraphitiMcpDir 'scripts\graphiti_healthcheck.py'

if (-not (Test-Path $Healthcheck)) {
    throw "Healthcheck script not found: $Healthcheck"
}

Push-Location $GraphitiMcpDir
try {
    uv run python scripts\graphiti_healthcheck.py --smoke-timeout 60 --poll-interval 5
}
finally {
    Pop-Location
}
