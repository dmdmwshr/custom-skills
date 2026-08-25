[CmdletBinding()]
param(
    [switch]$StartNow,
    [switch]$ReplaceLauncher
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

$TaskPath = '\DevProjects\MEIFU\AUTO\'
$TaskName = 'DEV-MEIFU-AUTO-01-DownloadQueue'
$ProjectKey = 'meifu-resumable-download'
$StableId = 'AUTO-01'
$Python = 'C:\Users\12070\AppData\Local\Programs\Python\Python312\python.exe'
$QueueRoot = Join-Path $env:LOCALAPPDATA 'MeifuDownloadQueue'
$TaskPython = Join-Path $QueueRoot 'TaskScripts\pythonw.exe'
$TaskPythonConfig = Join-Path $QueueRoot 'pyvenv.cfg'
$SourceQueueScript = Join-Path $PSScriptRoot 'meifu_download_queue.py'
$SourceDownloader = Join-Path $PSScriptRoot 'download_via_meifu.py'
$RuntimeRoot = Join-Path $QueueRoot 'Runtime'
$RuntimeManifest = Join-Path $QueueRoot 'runtime.json'
$QueueFile = Join-Path $QueueRoot 'queue.json'
$ControlFile = Join-Path $QueueRoot 'queue.control.json'
$QueueLog = Join-Path $QueueRoot 'queue.log'
$AuditLog = Join-Path $QueueRoot 'queue.audit.jsonl'
$Description = '[DEV_TASK_V1][project=meifu-resumable-download][id=AUTO-01][category=AUTO] 通用 Meifu 下载队列：登录后及每 30 分钟以单工作者、单个美服分块处理已批准的无签名 HTTPS 下载；仅直连 meifu主机(192.129.128.54:22)，拒绝代理跳板；Codex 只入队、启动和查状态，不承载传输；临时断网保留本地和美服断点并自动退避续传；完成后清理单任务缓存，残留缓存仍受 72 小时/8GiB 清理器约束；不读取凭据、不执行下载文件。'

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
    if ($magic -notin @(0x10B, 0x20B)) {
        throw "未知 PE 可选头格式：0x$('{0:X}' -f $magic)"
    }
    return [System.BitConverter]::ToUInt16($bytes, $optionalOffset + 68)
}

function Assert-PlainFile {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Description)

    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if ($item.PSIsContainer -or (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)) {
        throw "$Description 不是可用的普通文件：$Path"
    }
}

function Assert-PlainDirectory {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Description)

    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if (-not $item.PSIsContainer -or (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)) {
        throw "$Description 不是可用的普通目录：$Path"
    }
}

function Write-AtomicUtf8File {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Content)

    $directory = Split-Path -Parent $Path
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        Assert-PlainFile -Path $Path -Description '运行时清单'
    }
    elseif (Test-Path -LiteralPath $Path) {
        throw "运行时清单目标不是普通文件：$Path"
    }
    $temporary = Join-Path $directory ('.{0}.{1}.tmp' -f (Split-Path -Leaf $Path), [guid]::NewGuid().ToString('N'))
    [System.IO.File]::WriteAllText($temporary, $Content, [System.Text.UTF8Encoding]::new($false))
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        [System.IO.File]::Replace($temporary, $Path, $null)
    }
    else {
        [System.IO.File]::Move($temporary, $Path)
    }
}

function Ensure-QueueRuntime {
    foreach ($required in @($SourceQueueScript, $SourceDownloader)) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
            throw "安装前缺少必需脚本：$required"
        }
        Assert-PlainFile -Path $required -Description '技能源脚本'
    }

    New-Item -ItemType Directory -Path $RuntimeRoot -Force | Out-Null
    Assert-PlainDirectory -Path $RuntimeRoot -Description '下载队列运行时根目录'
    $queueHash = ((Get-FileHash -Algorithm SHA256 -LiteralPath $SourceQueueScript).Hash).ToLowerInvariant()
    $downloaderHash = ((Get-FileHash -Algorithm SHA256 -LiteralPath $SourceDownloader).Hash).ToLowerInvariant()
    $runtimeVersion = 'v1-{0}-{1}' -f $queueHash.Substring(0, 12), $downloaderHash.Substring(0, 12)
    $runtimeDirectory = Join-Path $RuntimeRoot $runtimeVersion
    $runtimeQueueScript = Join-Path $runtimeDirectory 'meifu_download_queue.py'
    $runtimeDownloader = Join-Path $runtimeDirectory 'download_via_meifu.py'

    if (Test-Path -LiteralPath $runtimeDirectory) {
        Assert-PlainDirectory -Path $runtimeDirectory -Description '既有下载队列运行时目录'
        foreach ($runtimeFile in @($runtimeQueueScript, $runtimeDownloader)) {
            if (-not (Test-Path -LiteralPath $runtimeFile -PathType Leaf)) {
                throw "既有运行时目录不完整，拒绝覆盖：$runtimeDirectory"
            }
            Assert-PlainFile -Path $runtimeFile -Description '既有运行时脚本'
        }
        if (((Get-FileHash -Algorithm SHA256 -LiteralPath $runtimeQueueScript).Hash).ToLowerInvariant() -ne $queueHash -or
            ((Get-FileHash -Algorithm SHA256 -LiteralPath $runtimeDownloader).Hash).ToLowerInvariant() -ne $downloaderHash) {
            throw "既有运行时版本与当前技能源哈希不一致，拒绝覆盖：$runtimeDirectory"
        }
    }
    else {
        $stagingDirectory = Join-Path $RuntimeRoot ('.staging-{0}' -f [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $stagingDirectory -ErrorAction Stop | Out-Null
        try {
            Copy-Item -LiteralPath $SourceQueueScript -Destination (Join-Path $stagingDirectory 'meifu_download_queue.py') -ErrorAction Stop
            Copy-Item -LiteralPath $SourceDownloader -Destination (Join-Path $stagingDirectory 'download_via_meifu.py') -ErrorAction Stop
            foreach ($stagedFile in @((Join-Path $stagingDirectory 'meifu_download_queue.py'), (Join-Path $stagingDirectory 'download_via_meifu.py'))) {
                Assert-PlainFile -Path $stagedFile -Description '暂存运行时脚本'
            }
            if (((Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $stagingDirectory 'meifu_download_queue.py')).Hash).ToLowerInvariant() -ne $queueHash -or
                ((Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $stagingDirectory 'download_via_meifu.py')).Hash).ToLowerInvariant() -ne $downloaderHash) {
                throw '暂存运行时哈希与技能源不一致。'
            }
            Move-Item -LiteralPath $stagingDirectory -Destination $runtimeDirectory -ErrorAction Stop
        }
        catch {
            if (Test-Path -LiteralPath $stagingDirectory) {
                Remove-Item -LiteralPath $stagingDirectory -Recurse -Force -ErrorAction SilentlyContinue
            }
            throw
        }
    }

    $manifest = [ordered]@{
        schema = 'MeifuDownloadRuntimeV1'
        installed_at = (Get-Date).ToUniversalTime().ToString('o')
        runtime_dir = $runtimeDirectory
        queue_script = $runtimeQueueScript
        downloader = $runtimeDownloader
        queue_script_sha256 = $queueHash
        downloader_sha256 = $downloaderHash
    }
    Write-AtomicUtf8File -Path $RuntimeManifest -Content (($manifest | ConvertTo-Json -Depth 4) + "`n")
    return [pscustomobject]@{
        Version = $runtimeVersion
        Directory = $runtimeDirectory
        QueueScript = $runtimeQueueScript
        Downloader = $runtimeDownloader
        Manifest = $RuntimeManifest
        QueueScriptSha256 = $queueHash
        DownloaderSha256 = $downloaderHash
    }
}

function Ensure-TaskPython {
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        throw "找不到固定 Python 3.12：$Python"
    }
    $basePrefix = (& $Python -B -c "import sys; print(sys.base_prefix)").Trim()
    if (-not $basePrefix) {
        throw '无法读取 Python 基础目录。'
    }
    $sourcePythonW = Join-Path $basePrefix 'Lib\venv\scripts\nt\pythonw.exe'
    if (-not (Test-Path -LiteralPath $sourcePythonW -PathType Leaf)) {
        throw "基础 Python 缺少标准 GUI 启动器：$sourcePythonW"
    }
    if ((Get-PeSubsystem -Path $sourcePythonW) -ne 2) {
        throw "基础启动器不是 Windows GUI 子系统：$sourcePythonW"
    }
    $directory = Split-Path -Parent $TaskPython
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $sourceHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $sourcePythonW).Hash
    if (Test-Path -LiteralPath $TaskPython -PathType Leaf) {
        $targetHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $TaskPython).Hash
        if ($targetHash -ne $sourceHash) {
            if (-not $ReplaceLauncher) {
                throw "受管 GUI 启动器与固定 Python 不一致：$TaskPython；确认后用 -ReplaceLauncher。"
            }
            Copy-Item -LiteralPath $sourcePythonW -Destination $TaskPython -Force
        }
    }
    else {
        Copy-Item -LiteralPath $sourcePythonW -Destination $TaskPython
    }
    if ((Get-PeSubsystem -Path $TaskPython) -ne 2) {
        throw "受管启动器不是 Windows GUI 子系统：$TaskPython"
    }
    $taskHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $TaskPython).Hash
    $consoleHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Python).Hash
    if ($taskHash -eq $consoleHash) {
        throw '受管 GUI 启动器与控制台 Python 相同，拒绝创建可能闪窗的任务。'
    }

    # A copied Windows launcher is not self-contained.  Keep its venv-style
    # home record directly above TaskScripts so Python can find the verified
    # base standard library instead of opening a hidden initialization error.
    $config = "home = $basePrefix`r`ninclude-system-site-packages = false`r`n"
    $writeConfig = $true
    if (Test-Path -LiteralPath $TaskPythonConfig -PathType Leaf) {
        try {
            $writeConfig = ([System.IO.File]::ReadAllText($TaskPythonConfig, [System.Text.UTF8Encoding]::new($false)) -ne $config)
        }
        catch {
            $writeConfig = $true
        }
    }
    if ($writeConfig) {
        [System.IO.File]::WriteAllText($TaskPythonConfig, $config, [System.Text.UTF8Encoding]::new($false))
    }

    # Validate the exact copied GUI launcher before it becomes a Scheduled
    # Task action.  The bounded probe never touches the queue or Meifu.
    $probe = Start-Process -FilePath $TaskPython `
        -ArgumentList '-B -c "import encodings; raise SystemExit(0)"' `
        -PassThru
    if (-not $probe.WaitForExit(15000)) {
        Stop-Process -Id $probe.Id -Force -ErrorAction SilentlyContinue
        throw "受管 GUI 启动器未能在 15 秒内完成本地标准库探测：$TaskPython"
    }
    if ($probe.ExitCode -ne 0) {
        throw "受管 GUI 启动器无法加载标准库，退出码：$($probe.ExitCode)"
    }
    $configHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $TaskPythonConfig).Hash
    return [pscustomobject]@{
        Path = $TaskPython
        Sha256 = $taskHash
        Subsystem = 2
        ConfigPath = $TaskPythonConfig
        ConfigSha256 = $configHash
    }
}

$launcher = Ensure-TaskPython
$runtime = Ensure-QueueRuntime
$existing = Get-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -ErrorAction SilentlyContinue
if ($existing) {
    $backupDirectory = Join-Path $QueueRoot 'task-backups'
    New-Item -ItemType Directory -Path $backupDirectory -Force | Out-Null
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $backup = Join-Path $backupDirectory "$stamp-$TaskName.xml"
    Export-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath | Set-Content -LiteralPath $backup -Encoding UTF8
}

$arguments = @(
    '-B', ('"{0}"' -f $runtime.QueueScript), 'scheduled-run',
    '--queue', ('"{0}"' -f $QueueFile),
    '--control-file', ('"{0}"' -f $ControlFile),
    '--log-file', ('"{0}"' -f $QueueLog),
    '--audit-log', ('"{0}"' -f $AuditLog),
    '--runtime-manifest', ('"{0}"' -f $runtime.Manifest),
    '--downloader', ('"{0}"' -f $runtime.Downloader),
    '--python', ('"{0}"' -f $Python),
    '--host', 'meifu主机',
    '--expected-hostname', '192.129.128.54',
    '--chunk-gib', '2',
    '--remote-reserve-gib', '8',
    '--local-reserve-gib', '4',
    '--retry-delay-seconds', '900'
) -join ' '

$action = New-ScheduledTaskAction -Execute $launcher.Path -Argument $arguments -WorkingDirectory $runtime.Directory
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$retryStart = (Get-Date).AddMinutes(5)
$networkTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At $retryStart `
    -RepetitionInterval (New-TimeSpan -Minutes 30) `
    -RepetitionDuration (New-TimeSpan -Days 365)
$settings = New-ScheduledTaskSettingsSet `
    -Hidden `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RunOnlyIfNetworkAvailable `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Days 365) `
    -RestartCount 0
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -TaskPath $TaskPath `
    -Action $action `
    -Trigger @($logonTrigger, $networkTrigger) `
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
    ProjectKey = $ProjectKey
    StableId = $StableId
    TaskPath = $TaskPath
    TaskName = $TaskName
    State = $task.State
    LastTaskResult = $info.LastTaskResult
    NextRunTime = $info.NextRunTime
    Action = $task.Actions | Select-Object Execute, Arguments, WorkingDirectory
    Launcher = $launcher
    Runtime = $runtime
}
