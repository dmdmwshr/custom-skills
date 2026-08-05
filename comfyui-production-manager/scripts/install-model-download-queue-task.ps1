[CmdletBinding()]
param(
    [switch]$StartNow,
    [switch]$ReplaceLauncher
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

$TaskPath = '\DevProjects\COMFY\AUTO\'
$TaskName = 'DEV-COMFY-AUTO-01-ModelDownloadQueue'
$ProjectKey = 'comfyui-production-manager'
$StableId = 'AUTO-01'
$Workspace = 'D:\12070\Documents\workspaces\Comfy-Codex-Workspace'
$SkillScripts = $PSScriptRoot
$Venv = 'C:\Users\12070\Documents\ComfyUI\.venv'
$VenvPython = Join-Path $Venv 'Scripts\python.exe'
$TaskPython = Join-Path $Venv 'TaskScripts\pythonw.exe'
$QueueScript = Join-Path $SkillScripts 'model_download_queue.py'
$QueueFile = Join-Path $Workspace 'models\download_queue.json'
$ControlFile = Join-Path $Workspace 'models\download_queue.control.json'
$QueueLog = Join-Path $Workspace 'models\logs\model_download_queue.log'
$Catalog = Join-Path $Workspace 'models\catalog.json'
$Description = '[DEV_TASK_V1][project=comfyui-production-manager][id=AUTO-01][category=AUTO] ComfyUI 模板缺失模型队列：登录后以单工作者、单个美服分块执行；仅直连 meifu主机(192.129.128.54:22)，拒绝 ProxyJump/ProxyCommand；不读取凭据，不覆盖已有模型，支持安全暂停、断点续传和精确缓存清理。'

function Get-PeSubsystem {
    param([Parameter(Mandatory)][string]$Path)

    $bytes = [System.IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -lt 256 -or $bytes[0] -ne 0x4D -or $bytes[1] -ne 0x5A) {
        throw "不是有效 PE 文件：$Path"
    }
    $peOffset = [System.BitConverter]::ToInt32($bytes, 0x3C)
    if ($peOffset -lt 0 -or $peOffset + 120 -ge $bytes.Length) {
        throw "PE 头无效：$Path"
    }
    if ($bytes[$peOffset] -ne 0x50 -or $bytes[$peOffset + 1] -ne 0x45) {
        throw "缺少 PE 签名：$Path"
    }
    $optionalOffset = $peOffset + 24
    $magic = [System.BitConverter]::ToUInt16($bytes, $optionalOffset)
    # PE32 and PE32+ have different ImageBase widths, but their Subsystem
    # member lands at the same optional-header offset (68).
    if ($magic -notin @(0x10B, 0x20B)) {
        throw "未知 PE 可选头格式：0x$('{0:X}' -f $magic)"
    }
    $subsystemOffset = $optionalOffset + 68
    return [System.BitConverter]::ToUInt16($bytes, $subsystemOffset)
}

function Ensure-TaskPython {
    if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
        throw "找不到 ComfyUI 虚拟环境解释器：$VenvPython"
    }
    $basePrefix = (& $VenvPython -B -c "import sys; print(sys.base_prefix)").Trim()
    if (-not $basePrefix) {
        throw '无法从 ComfyUI 虚拟环境读取基础 Python 路径。'
    }
    $sourcePythonW = Join-Path $basePrefix 'Lib\venv\scripts\nt\pythonw.exe'
    if (-not (Test-Path -LiteralPath $sourcePythonW -PathType Leaf)) {
        throw "基础 Python 缺少标准 GUI 启动器：$sourcePythonW"
    }
    if ((Get-PeSubsystem -Path $sourcePythonW) -ne 2) {
        throw "基础启动器不是 Windows GUI 子系统：$sourcePythonW"
    }
    $taskPythonDirectory = Split-Path -Parent $TaskPython
    New-Item -ItemType Directory -Path $taskPythonDirectory -Force | Out-Null
    $sourceHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $sourcePythonW).Hash
    if (Test-Path -LiteralPath $TaskPython -PathType Leaf) {
        $targetHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $TaskPython).Hash
        if ($targetHash -ne $sourceHash) {
            if (-not $ReplaceLauncher) {
                throw "受管 GUI 启动器已存在但来源不一致：$TaskPython；如确认可替换，请显式传入 -ReplaceLauncher。"
            }
            Copy-Item -LiteralPath $sourcePythonW -Destination $TaskPython -Force
        }
    }
    else {
        Copy-Item -LiteralPath $sourcePythonW -Destination $TaskPython
    }
    $taskSubsystem = Get-PeSubsystem -Path $TaskPython
    if ($taskSubsystem -ne 2) {
        throw "受管启动器不是 Windows GUI 子系统：$TaskPython"
    }
    $taskHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $TaskPython).Hash
    $consoleHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $VenvPython).Hash
    if ($taskHash -eq $consoleHash) {
        throw '受管 GUI 启动器与虚拟环境 python.exe 哈希相同，拒绝创建后台任务。'
    }
    $venvPythonW = Join-Path $Venv 'Scripts\pythonw.exe'
    if (Test-Path -LiteralPath $venvPythonW -PathType Leaf) {
        $venvPythonWSubsystem = Get-PeSubsystem -Path $venvPythonW
        $venvPythonWHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $venvPythonW).Hash
        if ($venvPythonWSubsystem -ne 2 -and $taskHash -eq $venvPythonWHash) {
            throw '受管 GUI 启动器与虚拟环境的控制台重定向 pythonw.exe 相同，拒绝创建后台任务。'
        }
    }
    return [pscustomobject]@{
        Path = $TaskPython
        Sha256 = $taskHash
        Subsystem = $taskSubsystem
        BasePython = $basePrefix
    }
}

foreach ($required in @($QueueScript, $QueueFile)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "安装前缺少必需文件：$required"
    }
}

$launcher = Ensure-TaskPython
$existing = Get-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -ErrorAction SilentlyContinue
if ($existing) {
    $backupDirectory = Join-Path $Workspace 'docs\task-backups'
    New-Item -ItemType Directory -Path $backupDirectory -Force | Out-Null
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $backupFile = Join-Path $backupDirectory "$stamp-$TaskName.xml"
    Export-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath | Set-Content -LiteralPath $backupFile -Encoding UTF8
}

$argumentList = @(
    '-B', ('"{0}"' -f $QueueScript), 'run',
    '--queue', ('"{0}"' -f $QueueFile),
    '--control-file', ('"{0}"' -f $ControlFile),
    '--log-file', ('"{0}"' -f $QueueLog),
    '--catalog', ('"{0}"' -f $Catalog),
    '--host', 'meifu主机',
    '--expected-hostname', '192.129.128.54',
    '--remote-cache', '/root/.cache/comfyui-models',
    '--chunk-gib', '2',
    '--recover-stale-lock'
) -join ' '

$action = New-ScheduledTaskAction -Execute $launcher.Path -Argument $argumentList -WorkingDirectory $SkillScripts
$trigger = New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RunOnlyIfNetworkAvailable `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Days 365) `
    -RestartCount 0
$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -TaskPath $TaskPath `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description $Description `
    -Force | Out-Null

if ($StartNow) {
    Start-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath
}

$task = Get-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath
$info = Get-ScheduledTaskInfo -TaskName $TaskName -TaskPath $TaskPath
[pscustomobject]@{
    TaskPath = $TaskPath
    TaskName = $TaskName
    State = $task.State
    LastTaskResult = $info.LastTaskResult
    NextRunTime = $info.NextRunTime
    Action = $task.Actions | Select-Object Execute, Arguments, WorkingDirectory
    Trigger = $task.Triggers | Select-Object Enabled, StartBoundary, UserId
    Launcher = $launcher
    TaskDescription = $Description
}
