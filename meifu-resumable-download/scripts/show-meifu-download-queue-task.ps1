[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

$TaskPath = '\DevProjects\MEIFU\AUTO\'
$TaskName = 'DEV-MEIFU-AUTO-01-DownloadQueue'
$QueueRoot = Join-Path $env:LOCALAPPDATA 'MeifuDownloadQueue'
$QueueFile = Join-Path $QueueRoot 'queue.json'
$ControlFile = Join-Path $QueueRoot 'queue.control.json'
$QueueScript = Join-Path $PSScriptRoot 'meifu_download_queue.py'
$Python = 'C:\Users\12070\AppData\Local\Programs\Python\Python312\python.exe'

$task = Get-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -ErrorAction SilentlyContinue
$info = if ($task) { Get-ScheduledTaskInfo -TaskName $TaskName -TaskPath $TaskPath } else { $null }
$queue = $null
if (Test-Path -LiteralPath $QueueFile -PathType Leaf) {
    try { $queue = Get-Content -LiteralPath $QueueFile -Encoding UTF8 -Raw | ConvertFrom-Json } catch { $queue = 'invalid_json' }
}
$control = $null
if (Test-Path -LiteralPath $ControlFile -PathType Leaf) {
    try { $control = Get-Content -LiteralPath $ControlFile -Encoding UTF8 -Raw | ConvertFrom-Json } catch { $control = 'invalid_json' }
}

[pscustomobject]@{
    TaskPath = $TaskPath
    TaskName = $TaskName
    TaskState = if ($task) { $task.State } else { 'NotRegistered' }
    LastTaskResult = if ($info) { $info.LastTaskResult } else { $null }
    NextRunTime = if ($info) { $info.NextRunTime } else { $null }
    Action = if ($task) { $task.Actions | Select-Object Execute, Arguments, WorkingDirectory } else { $null }
    QueueFile = $QueueFile
    QueueState = if ($queue -is [string]) { $queue } elseif ($queue) { $queue.state } else { 'not_initialized' }
    QueueCounts = if ($queue -is [string]) { $null } elseif ($queue) { @($queue.entries | Group-Object status | ForEach-Object { "{0}={1}" -f $_.Name, $_.Count }) -join ', ' } else { $null }
    Control = $control
    QueueScript = $QueueScript
    Python = $Python
}
