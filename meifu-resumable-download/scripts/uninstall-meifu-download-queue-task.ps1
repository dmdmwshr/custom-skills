[CmdletBinding()]
param(
    [switch]$Apply,
    [switch]$StopRunning
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

$TaskPath = '\DevProjects\MEIFU\AUTO\'
$TaskName = 'DEV-MEIFU-AUTO-01-DownloadQueue'
$QueueRoot = Join-Path $env:LOCALAPPDATA 'MeifuDownloadQueue'

$task = Get-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -ErrorAction SilentlyContinue
if (-not $task) {
    [pscustomobject]@{ Status = 'not_registered'; TaskPath = $TaskPath; TaskName = $TaskName }
    exit 0
}

if (-not $Apply) {
    [pscustomobject]@{
        Status = 'dry_run'
        TaskPath = $TaskPath
        TaskName = $TaskName
        State = $task.State
        Action = $task.Actions | Select-Object Execute, Arguments, WorkingDirectory
        Message = '未修改任务；确认卸载时使用 -Apply。'
    }
    exit 0
}

if ($task.State -eq 'Running') {
    if (-not $StopRunning) {
        throw "任务正在运行；为避免遗留下载子进程，请先暂停队列，或明确使用 -StopRunning。"
    }
    Stop-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath
    Start-Sleep -Seconds 2
    $task = Get-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath
    if ($task.State -eq 'Running') {
        throw '任务仍在运行，未执行卸载。'
    }
}

$backupDirectory = Join-Path $QueueRoot 'task-backups'
New-Item -ItemType Directory -Path $backupDirectory -Force | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = Join-Path $backupDirectory "$stamp-$TaskName-before-uninstall.xml"
Export-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath | Set-Content -LiteralPath $backup -Encoding UTF8
Unregister-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -Confirm:$false

[pscustomobject]@{
    Status = 'uninstalled'
    TaskPath = $TaskPath
    TaskName = $TaskName
    Backup = $backup
    QueueDataPreserved = $true
}
