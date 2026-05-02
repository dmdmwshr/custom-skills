$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

Write-Output '== Graphiti /health =='
try {
    Invoke-RestMethod -Uri 'http://127.0.0.1:8010/health' -TimeoutSec 5 | ConvertTo-Json -Depth 10
}
catch {
    Write-Output $_.Exception.Message
}

Write-Output ''
Write-Output '== Listening ports =='
Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -in 8010, 7474, 7687 } |
    Select-Object LocalAddress, LocalPort, OwningProcess |
    Format-Table -AutoSize | Out-String

Write-Output '== Docker containers =='
docker ps --filter 'name=graphiti-official' --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'

Write-Output ''
Write-Output '== Docker compose source =='
docker inspect graphiti-official-graphiti-mcp-1 --format 'project={{index .Config.Labels "com.docker.compose.project"}} config={{index .Config.Labels "com.docker.compose.project.config_files"}} env={{index .Config.Labels "com.docker.compose.project.environment_file"}} image_version={{index .Config.Labels "org.opencontainers.image.version"}} graphiti_core={{index .Config.Labels "graphiti.core.version"}}' 2>$null

Write-Output ''
Write-Output '== Recent graphiti-mcp warnings/errors =='
docker logs --since 30m graphiti-official-graphiti-mcp-1 2>&1 |
    Select-String -Pattern 'ERROR|WARN|Traceback|Exception|429|timeout|failed|Cannot resolve|authentication|embedding' -CaseSensitive:$false |
    Select-Object -First 80

Write-Output ''
Write-Output '== Recent neo4j warnings/errors =='
docker logs --since 30m graphiti-official-neo4j-1 2>&1 |
    Select-String -Pattern 'ERROR|WARN|Exception|failed|authentication' -CaseSensitive:$false |
    Select-Object -First 80
