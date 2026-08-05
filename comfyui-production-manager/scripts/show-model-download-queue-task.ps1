[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

$TaskPath = '\DevProjects\COMFY\AUTO\'
$TaskName = 'DEV-COMFY-AUTO-01-ModelDownloadQueue'
$Workspace = 'D:\12070\Documents\workspaces\Comfy-Codex-Workspace'
$VenvPython = 'C:\Users\12070\Documents\ComfyUI\.venv\Scripts\python.exe'
$QueueScript = Join-Path $PSScriptRoot 'model_download_queue.py'
$QueueFile = Join-Path $Workspace 'models\download_queue.json'
$ControlFile = Join-Path $Workspace 'models\download_queue.control.json'

$task = Get-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -ErrorAction SilentlyContinue
if (-not $task) {
    [pscustomobject]@{
        Installed = $false
        TaskPath = $TaskPath
        TaskName = $TaskName
        Message = '未找到受管模型下载队列任务。'
    }
    exit 0
}

$info = Get-ScheduledTaskInfo -TaskName $TaskName -TaskPath $TaskPath
$queueStatus = $null
if ((Test-Path -LiteralPath $VenvPython -PathType Leaf) -and (Test-Path -LiteralPath $QueueScript -PathType Leaf) -and (Test-Path -LiteralPath $QueueFile -PathType Leaf)) {
    $raw = & $VenvPython -B $QueueScript status --queue $QueueFile --control-file $ControlFile 2>&1
    try {
        $queueStatus = ($raw -join "`n") | ConvertFrom-Json
    }
    catch {
        $queueStatus = [pscustomobject]@{ ParseError = $true; Raw = ($raw -join "`n") }
    }
}

[pscustomobject]@{
    Installed = $true
    TaskPath = $TaskPath
    TaskName = $TaskName
    State = $task.State
    LastRunTime = $info.LastRunTime
    LastTaskResult = $info.LastTaskResult
    NextRunTime = $info.NextRunTime
    Action = $task.Actions | Select-Object Execute, Arguments, WorkingDirectory
    Trigger = $task.Triggers | Select-Object Enabled, StartBoundary, UserId
    Settings = [pscustomobject]@{
        MultipleInstances = $task.Settings.MultipleInstances
        ExecutionTimeLimit = $task.Settings.ExecutionTimeLimit
        RunOnlyIfNetworkAvailable = $task.Settings.RunOnlyIfNetworkAvailable
    }
    Queue = $queueStatus
}
