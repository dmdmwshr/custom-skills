function Resolve-VideoNoteResume {
    param([string]$ProgressPath, [string]$VideoUrl, $Media, [string]$ResultDirectory, [string]$EvidenceDirectory)
    if ($null -eq $Media) { throw '恢复笔记必须提供同一来源的已核验视频回执。' }
    $progress = Get-Content -LiteralPath $ProgressPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $task = [string]$progress.task_id
    if ($task -notmatch '^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$' -or
        $progress.source_url -ne $VideoUrl -or $progress.processing_platform -ne 'local') {
        throw '原笔记任务的来源、编号或处理平台未核实。'
    }
    $uploaded = [string]$progress.uploaded_path
    if ($uploaded -notmatch ('^/uploads/douyin-' + [regex]::Escape($Media.SourceId) + '-[a-f0-9]{32}\.(mp4|mkv|webm|mov)$')) {
        throw '原笔记任务的本机上传来源未核实。'
    }
    $uploadFile = Join-Path (Split-Path -Parent $ResultDirectory) ($uploaded.TrimStart('/'))
    if (-not (Test-Path -LiteralPath $uploadFile -PathType Leaf) -or
        (Get-FileHash -LiteralPath $uploadFile -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Media.Sha256) {
        throw '原上传视频与正式下载摘要不一致，未恢复笔记。'
    }
    $statusPath = Join-Path $ResultDirectory "$task.status.json"
    $state = Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($state.status -notin @('FAILED', 'SUCCESS')) { throw '原任务仍在处理或结果不明，不重复提交。' }
    $transcriptPath = Join-Path $ResultDirectory "${task}_transcript.json"
    $transcript = Get-Content -LiteralPath $transcriptPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $audioPath = Join-Path $ResultDirectory "${task}_audio.json"
    $audio = Get-Content -LiteralPath $audioPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([string]::IsNullOrWhiteSpace([string]$transcript.full_text) -or @($transcript.segments).Count -eq 0 -or
        -not (Test-Path -LiteralPath $audio.file_path -PathType Leaf)) { throw '原任务的转写或音频缓存未核实，不重新下载或转写。' }
    if (Test-Path -LiteralPath $EvidenceDirectory) { throw '恢复基线已存在，请回读原恢复结果，不重复提交。' }
    New-Item -ItemType Directory -Path $EvidenceDirectory | Out-Null
    $files = @((Get-Item -LiteralPath $ProgressPath), (Get-Item -LiteralPath $statusPath),
        (Get-Item -LiteralPath $transcriptPath), (Get-Item -LiteralPath $audioPath))
    foreach ($suffix in @('_markdown.status.json', '_markdown.gpt.checkpoint.json', '.json')) {
        $candidate = Join-Path $ResultDirectory ($task + $suffix)
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { $files += Get-Item -LiteralPath $candidate }
    }
    $manifest = @()
    foreach ($file in $files) {
        $target = Join-Path $EvidenceDirectory $file.Name
        Copy-Item -LiteralPath $file.FullName -Destination $target
        $hash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($hash -ne (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()) { throw '恢复基线复制摘要不一致。' }
        $manifest += [ordered]@{source=$file.FullName; snapshot=$target; sha256=$hash}
    }
    Write-JsonFile -Path (Join-Path $EvidenceDirectory 'manifest.json') -Value $manifest
    [pscustomobject]@{TaskId=$task; UploadedPath=$uploaded; Status=[string]$state.status;
        TranscriptSha256=(Get-FileHash -LiteralPath $transcriptPath -Algorithm SHA256).Hash.ToLowerInvariant();
        EvidenceDirectory=$EvidenceDirectory}
}
