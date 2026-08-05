[CmdletBinding()]
param(
    [switch]$StopRunningTask
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

$TaskPath = '\DevProjects\COMFY\AUTO\'
$TaskName = 'DEV-COMFY-AUTO-01-ModelDownloadQueue'

$task = Get-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -ErrorAction SilentlyContinue
if (-not $task) {
    [pscustomobject]@{
        Removed = $false
        TaskPath = $TaskPath
        TaskName = $TaskName
        Message = '任务不存在，无需删除。'
    }
    exit 0
}

if ($task.State -eq 'Running') {
    if (-not $StopRunningTask) {
        throw "任务正在运行。请先安全暂停队列，或在明确接受中断后传入 -StopRunningTask：$TaskPath$TaskName"
    }
    Stop-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath
    Start-Sleep -Seconds 2
}

Unregister-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -Confirm:$false
$remaining = Get-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -ErrorAction SilentlyContinue
if ($remaining) {
    throw "删除后任务仍存在：$TaskPath$TaskName"
}
[pscustomobject]@{
    Removed = $true
    TaskPath = $TaskPath
    TaskName = $TaskName
    QueueStatePreserved = $true
    Message = '已精确删除计划任务；本地队列和续传状态未删除。'
}
