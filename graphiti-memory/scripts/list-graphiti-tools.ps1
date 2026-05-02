$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

$Endpoint = 'http://127.0.0.1:8010/mcp'
$TempRoot = Join-Path $env:TEMP ("graphiti_mcp_tools_{0}" -f ([guid]::NewGuid().ToString()))

$InitializeBody = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"graphiti-memory-skill","version":"1.0.0"}}}'
$InitializedBody = '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}'
$ToolsBody = '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'

function Write-Utf8NoBom {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Value
    )
    [System.IO.File]::WriteAllText($Path, $Value, [System.Text.UTF8Encoding]::new($false))
}

function Get-SsePayload {
    param(
        [string[]]$Lines,
        [Parameter(Mandatory = $true)][string]$Label
    )
    if (-not $Lines -or $Lines.Count -eq 0) {
        throw "$Label response was empty."
    }
    $DataLines = @($Lines | Where-Object { $_ -like 'data:*' })
    if (-not $DataLines -or $DataLines.Count -eq 0) {
        $Lines | Select-Object -First 20 | ForEach-Object { Write-Host $_ }
        throw "$Label response did not include an SSE data line."
    }
    return (($DataLines | ForEach-Object { $_ -replace '^data:\s*', '' }) -join "`n")
}

function Convert-SafeJson {
    param(
        [Parameter(Mandatory = $true)][string]$Payload,
        [Parameter(Mandatory = $true)][string]$Label,
        [string[]]$RawLines
    )
    try {
        return $Payload | ConvertFrom-Json
    }
    catch {
        Write-Host "Failed to parse $Label JSON payload. Raw response preview:"
        $RawLines | Select-Object -First 20 | ForEach-Object { Write-Host $_ }
        throw
    }
}

try {
    New-Item -ItemType Directory -Force -Path $TempRoot | Out-Null
    $InitializeFile = Join-Path $TempRoot 'initialize.json'
    $InitializedFile = Join-Path $TempRoot 'initialized.json'
    $ToolsFile = Join-Path $TempRoot 'tools-list.json'
    $InitResponseFile = Join-Path $TempRoot 'initialize-response.txt'

    Write-Utf8NoBom -Path $InitializeFile -Value $InitializeBody
    Write-Utf8NoBom -Path $InitializedFile -Value $InitializedBody
    Write-Utf8NoBom -Path $ToolsFile -Value $ToolsBody

    curl.exe -sS -i -N -X POST $Endpoint `
        -H 'Accept: application/json, text/event-stream' `
        -H 'Content-Type: application/json' `
        --data-binary "@$InitializeFile" | Tee-Object -FilePath $InitResponseFile | Out-Null

    $SessionId = Select-String -Path $InitResponseFile -Pattern '^mcp-session-id:' |
        ForEach-Object { $_.Line -replace '^mcp-session-id:\s*', '' } |
        Select-Object -First 1

    if (-not $SessionId) {
        Get-Content -Encoding UTF8 $InitResponseFile
        throw 'MCP initialize did not return mcp-session-id.'
    }

    $SessionId = $SessionId.Trim()

    curl.exe -sS -i -N -X POST $Endpoint `
        -H 'Accept: application/json, text/event-stream' `
        -H 'Content-Type: application/json' `
        -H "mcp-session-id: $SessionId" `
        --data-binary "@$InitializedFile" | Out-Null

    $ToolsResponse = @(curl.exe -sS -i -N -X POST $Endpoint `
        -H 'Accept: application/json, text/event-stream' `
        -H 'Content-Type: application/json' `
        -H "mcp-session-id: $SessionId" `
        --data-binary "@$ToolsFile" | Where-Object { $null -ne $_ })

    $Payload = Get-SsePayload -Lines $ToolsResponse -Label 'tools/list'
    $Json = Convert-SafeJson -Payload $Payload -Label 'tools/list' -RawLines $ToolsResponse

    $Json.result.tools |
        Select-Object -ExpandProperty name |
        Sort-Object
}
finally {
    if (Test-Path -LiteralPath $TempRoot) {
        Remove-Item -LiteralPath $TempRoot -Recurse -Force
    }
}
