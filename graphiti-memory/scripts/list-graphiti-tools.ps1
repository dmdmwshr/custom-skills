$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

$Endpoint = 'http://127.0.0.1:8010/mcp'
$TempFile = Join-Path $env:TEMP ("graphiti_mcp_init_{0}.txt" -f ([guid]::NewGuid().ToString()))

$InitializeBody = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"graphiti-memory-skill","version":"1.0.0"}}}'

curl.exe -sS -i -N -X POST $Endpoint `
    -H 'Accept: application/json, text/event-stream' `
    -H 'Content-Type: application/json' `
    --data $InitializeBody | Tee-Object -FilePath $TempFile | Out-Null

$SessionId = Select-String -Path $TempFile -Pattern '^mcp-session-id:' |
    ForEach-Object { $_.Line -replace '^mcp-session-id:\s*', '' } |
    Select-Object -First 1

if (-not $SessionId) {
    Get-Content -Encoding UTF8 $TempFile
    throw 'MCP initialize did not return mcp-session-id.'
}

$SessionId = $SessionId.Trim()
$InitializedBody = '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}'
$ToolsBody = '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'

curl.exe -sS -i -N -X POST $Endpoint `
    -H 'Accept: application/json, text/event-stream' `
    -H 'Content-Type: application/json' `
    -H "mcp-session-id: $SessionId" `
    --data $InitializedBody | Out-Null

$ToolsResponse = curl.exe -sS -N -X POST $Endpoint `
    -H 'Accept: application/json, text/event-stream' `
    -H 'Content-Type: application/json' `
    -H "mcp-session-id: $SessionId" `
    --data $ToolsBody

$DataLine = $ToolsResponse | Where-Object { $_ -like 'data: *' } | Select-Object -First 1
if (-not $DataLine) {
    $ToolsResponse
    throw 'tools/list response did not include an SSE data line.'
}

$Payload = $DataLine -replace '^data:\s*', ''
$Json = $Payload | ConvertFrom-Json

$Json.result.tools |
    Select-Object -ExpandProperty name |
    Sort-Object
