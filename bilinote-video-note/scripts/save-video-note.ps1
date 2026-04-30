[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$VideoUrl,

    [Parameter(Mandatory = $false)]
    [string]$Platform,

    [Parameter(Mandatory = $false)]
    [string]$ProviderId,

    [Parameter(Mandatory = $false)]
    [string]$ModelName
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

$script:SkillName = "bilinote-video-note"
$script:SkillRoot = Split-Path -Parent $PSScriptRoot
$script:ConfigPath = Join-Path $script:SkillRoot "references\local-config.json"
$script:TemplatePath = Join-Path $script:SkillRoot "references\video-note-template.md"

function Read-JsonFile {
    param([string]$Path)
    return (Get-Content -Encoding UTF8 -Raw -LiteralPath $Path | ConvertFrom-Json)
}

function Write-JsonFile {
    param(
        [string]$Path,
        [Parameter(Mandatory = $true)]
        $Value
    )
    $dir = Split-Path -Parent $Path
    if (-not [string]::IsNullOrWhiteSpace($dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    $Value | ConvertTo-Json -Depth 32 | Set-Content -Encoding UTF8 -LiteralPath $Path
}

function Invoke-JsonApi {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Uri,

        [ValidateSet("GET", "POST")]
        [string]$Method = "GET",

        $Body = $null
    )

    Add-Type -AssemblyName System.Net.Http | Out-Null
    $handler = New-Object System.Net.Http.HttpClientHandler
    $client = New-Object System.Net.Http.HttpClient($handler)
    $content = $null
    try {
        $client.Timeout = [TimeSpan]::FromSeconds(120)
        $client.DefaultRequestHeaders.Accept.Clear()
        $client.DefaultRequestHeaders.Accept.Add(
            [System.Net.Http.Headers.MediaTypeWithQualityHeaderValue]::new("application/json")
        )

        if ($Method -eq "POST") {
            $json = if ($null -ne $Body) { $Body | ConvertTo-Json -Depth 20 } else { "{}" }
            $content = New-Object System.Net.Http.StringContent($json, [System.Text.Encoding]::UTF8, "application/json")
            $response = $client.PostAsync($Uri, $content).GetAwaiter().GetResult()
        } else {
            $response = $client.GetAsync($Uri).GetAwaiter().GetResult()
        }

        $bytes = $response.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult()
        $text = [System.Text.Encoding]::UTF8.GetString($bytes)
        $parsed = $null
        if (-not [string]::IsNullOrWhiteSpace($text)) {
            $parsed = $text | ConvertFrom-Json
        }

        if (-not $response.IsSuccessStatusCode) {
            if ($parsed -and $parsed.msg) {
                throw "API 调用失败 [$Method $Uri]：$($parsed.msg)"
            }
            throw "API 调用失败 [$Method $Uri]：HTTP $([int]$response.StatusCode)"
        }

        return $parsed
    } catch {
        $message = $_.Exception.Message
        throw "API 调用失败 [$Method $Uri]：$message"
    } finally {
        if ($content) {
            $content.Dispose()
        }
        $client.Dispose()
        $handler.Dispose()
    }
}

function Get-CanonicalUrl {
    param([string]$Url)

    $match = [System.Text.RegularExpressions.Regex]::Match($Url, 'https?://[^\s]+')
    $trimmed = if ($match.Success) { $match.Value.Trim() } else { $Url.Trim() }
    try {
        $uri = [System.Uri]$trimmed
        $builder = [System.UriBuilder]::new($uri)
        $builder.Fragment = ""
        return $builder.Uri.AbsoluteUri.TrimEnd("/")
    } catch {
        return $trimmed
    }
}

function Detect-Platform {
    param([string]$Url)

    $value = $Url.ToLowerInvariant()
    if ($value -match 'bilibili\.com|b23\.tv') {
        return "bilibili"
    }
    if ($value -match 'douyin\.com|iesdouyin\.com') {
        return "douyin"
    }
    throw "无法从 URL 识别平台，只支持 bilibili 和 douyin。"
}

function Get-UrlHash {
    param([string]$Url)

    $canonical = Get-CanonicalUrl -Url $Url
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($canonical)
        $hashBytes = $sha.ComputeHash($bytes)
        $hex = [System.BitConverter]::ToString($hashBytes).Replace("-", "").ToLowerInvariant()
        return $hex.Substring(0, 16)
    } finally {
        $sha.Dispose()
    }
}

function Get-SourceId {
    param(
        $AudioMeta,
        [string]$Url
    )

    if ($null -ne $AudioMeta) {
        if ($AudioMeta.video_id) {
            return [string]$AudioMeta.video_id
        }
        if ($AudioMeta.raw_info -and $AudioMeta.raw_info.id) {
            return [string]$AudioMeta.raw_info.id
        }
    }

    return Get-UrlHash -Url $Url
}

function Get-CanonicalVideoUrl {
    param(
        [string]$Platform,
        [string]$SourceId,
        [string]$OriginalUrl
    )

    switch ($Platform) {
        "bilibili" { return "https://www.bilibili.com/video/$SourceId" }
        "douyin"   { return "https://www.douyin.com/video/$SourceId" }
        default    { return (Get-CanonicalUrl -Url $OriginalUrl) }
    }
}

function ConvertTo-SafeFileName {
    param([string]$Text)

    $name = $Text
    if ([string]::IsNullOrWhiteSpace($name)) {
        $name = "video-note"
    }

    $name = $name.Trim()
    $name = $name -replace '[\r\n\t]+', ' '
    $name = $name -replace '\s+', ' '
    foreach ($char in [System.IO.Path]::GetInvalidFileNameChars()) {
        $name = $name.Replace($char, '-')
    }
    $name = $name.Trim('. ').Trim()
    if ([string]::IsNullOrWhiteSpace($name)) {
        $name = "video-note"
    }
    return $name
}

function Normalize-Title {
    param([string]$Title, [string]$Fallback)

    $value = if ([string]::IsNullOrWhiteSpace($Title)) { $Fallback } else { $Title }
    $value = $value.Trim()
    $value = $value.Trim('“', '”', '"', "'")
    if ([string]::IsNullOrWhiteSpace($value)) {
        return $Fallback
    }
    return $value
}

function Get-RelativeMarkdownPath {
    param(
        [string]$FromFile,
        [string]$ToPath
    )

    $fromDir = Split-Path -Parent $FromFile
    $fromUri = [System.Uri]((Resolve-Path -LiteralPath $fromDir).Path + [System.IO.Path]::DirectorySeparatorChar)
    $toUri = [System.Uri](Resolve-Path -LiteralPath $ToPath).Path
    $relative = $fromUri.MakeRelativeUri($toUri).ToString()
    return [System.Uri]::UnescapeDataString($relative)
}

function ConvertTo-YamlList {
    param(
        [string[]]$Values,
        [int]$Indent = 0
    )

    $prefix = " " * $Indent
    return (($Values | ForEach-Object { "$prefix- '$_'" }) -join [Environment]::NewLine)
}

function Escape-TemplateValue {
    param([string]$Value)

    if ($null -eq $Value) {
        return ""
    }
    return $Value
}

function Apply-Replacements {
    param(
        [string]$Template,
        [hashtable]$Replacements
    )

    $result = $Template
    foreach ($key in $Replacements.Keys) {
        $result = $result.Replace($key, (Escape-TemplateValue -Value ([string]$Replacements[$key])))
    }
    return $result
}

function Normalize-BiliNoteMarkdown {
    param([string]$Markdown)

    if ([string]::IsNullOrWhiteSpace($Markdown)) {
        return "## BiliNote 提取结果`n`nBiliNote 没有返回 Markdown 内容。"
    }

    $text = $Markdown.Trim()
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.AddRange([string[]]($text -split "\r?\n"))

    while ($lines.Count -gt 0 -and [string]::IsNullOrWhiteSpace($lines[0])) {
        $lines.RemoveAt(0)
    }

    if ($lines.Count -gt 0 -and $lines[0] -match '^>\s*来源链接：') {
        $lines.RemoveAt(0)
        while ($lines.Count -gt 0 -and [string]::IsNullOrWhiteSpace($lines[0])) {
            $lines.RemoveAt(0)
        }
    }

    if ($lines.Count -eq 0) {
        return "## BiliNote 提取结果`n`nBiliNote 没有返回 Markdown 内容。"
    }

    if ($lines[0] -match '^#\s+') {
        $lines[0] = "### 原始 BiliNote Markdown"
    } else {
        $lines.Insert(0, "### 原始 BiliNote Markdown")
    }

    return (($lines -join "`r`n").Trim())
}

function Get-ExistingNotePaths {
    param(
        [string]$Root,
        [string]$SourceId
    )

    if (-not (Test-Path -LiteralPath $Root)) {
        return @()
    }

    $matches = New-Object System.Collections.Generic.List[string]
    $files = Get-ChildItem -LiteralPath $Root -Recurse -File -Filter '*.md' -ErrorAction SilentlyContinue
    $pattern = 'source_id:\s*"?{0}"?' -f [System.Text.RegularExpressions.Regex]::Escape($SourceId)
    foreach ($file in $files) {
        $match = Select-String -LiteralPath $file.FullName -Pattern $pattern -ErrorAction SilentlyContinue
        if ($match) {
            $matches.Add($file.FullName)
        }
    }
    return [string[]]$matches
}

try {
    $config = Read-JsonFile -Path $script:ConfigPath
    $template = Get-Content -Encoding UTF8 -Raw -LiteralPath $script:TemplatePath

    $effectivePlatform = if ([string]::IsNullOrWhiteSpace($Platform)) {
        Detect-Platform -Url $VideoUrl
    } else {
        $Platform.Trim().ToLowerInvariant()
    }

    if ($effectivePlatform -notin $config.supported_platforms) {
        throw "不支持的平台：$effectivePlatform。当前只支持 bilibili 和 douyin。"
    }

    $effectiveProvider = if ([string]::IsNullOrWhiteSpace($ProviderId)) {
        [string]$config.default_request.provider_id
    } else {
        $ProviderId.Trim()
    }

    $effectiveModel = if ([string]::IsNullOrWhiteSpace($ModelName)) {
        [string]$config.default_request.model_name
    } else {
        $ModelName.Trim()
    }

    $backendBaseUrl = [string]$config.backend_base_url
    $health = Invoke-JsonApi -Uri "$backendBaseUrl/api/sys_health" -Method "GET"
    if ($health.code -ne 0) {
        throw "BiliNote 后端健康检查失败：$($health.msg)"
    }

    $payload = [ordered]@{
        video_url           = $VideoUrl
        platform            = $effectivePlatform
        quality             = [string]$config.default_request.quality
        screenshot          = [bool]$config.default_request.screenshot
        link                = [bool]$config.default_request.link
        model_name          = $effectiveModel
        provider_id         = $effectiveProvider
        format              = @($config.default_request.format)
        style               = [string]$config.default_request.style
        video_understanding = [bool]$config.default_request.video_understanding
    }

    $createResponse = Invoke-JsonApi -Uri "$backendBaseUrl/api/generate_note" -Method "POST" -Body $payload
    if ($createResponse.code -ne 0 -or -not $createResponse.data.task_id) {
        throw "发起任务失败：$($createResponse.msg)"
    }

    $taskId = [string]$createResponse.data.task_id
    $timeoutSeconds = [int]$config.timeout_seconds
    $pollIntervalSeconds = [int]$config.poll_interval_seconds
    $deadline = (Get-Date).AddSeconds($timeoutSeconds)
    $finalStatus = $null

    do {
        $statusResponse = Invoke-JsonApi -Uri "$backendBaseUrl/api/task_status/$taskId" -Method "GET"
        if ($statusResponse.code -ne 0) {
            throw "任务执行失败：$($statusResponse.msg)"
        }

        $finalStatus = $statusResponse
        $taskState = [string]$statusResponse.data.status
        if ($taskState -eq "SUCCESS") {
            break
        }

        Start-Sleep -Seconds $pollIntervalSeconds
    } while ((Get-Date) -lt $deadline)

    if ($null -eq $finalStatus -or [string]$finalStatus.data.status -ne "SUCCESS") {
        throw "等待任务超时，task_id=$taskId"
    }

    $result = $finalStatus.data.result
    if ($null -eq $result) {
        throw "任务完成，但未返回 result。"
    }

    $audioMeta = $result.audio_meta
    $sourceId = Get-SourceId -AudioMeta $audioMeta -Url $VideoUrl
    $title = Normalize-Title -Title ([string]$audioMeta.title) -Fallback $sourceId
    $safeTitle = ConvertTo-SafeFileName -Text $title

    $noteRoot = [string]$config.note_roots.$effectivePlatform
    $attachmentRoot = [string]$config.attachment_roots.$effectivePlatform
    $attachmentsDir = Join-Path $attachmentRoot $sourceId

    New-Item -ItemType Directory -Force -Path $noteRoot | Out-Null
    New-Item -ItemType Directory -Force -Path $attachmentsDir | Out-Null

    $backendResultPath = Join-Path ([string]$config.backend_result_dir) "$taskId.json"
    $backendStatusPath = Join-Path ([string]$config.backend_result_dir) "$taskId.status.json"
    $rawMarkdownPath = Join-Path $attachmentsDir "markdown-source.md"
    $resultJsonPath = Join-Path $attachmentsDir "bilinote-result.json"
    $taskJsonPath = Join-Path $attachmentsDir "task-result.json"
    $transcriptJsonPath = Join-Path $attachmentsDir "transcript.json"
    $transcriptTextPath = Join-Path $attachmentsDir "transcript.txt"
    $contextJsonPath = Join-Path $attachmentsDir "context.json"
    $backendExportCopyPath = Join-Path $attachmentsDir "backend-result.json"
    $backendStatusCopyPath = Join-Path $attachmentsDir "backend-status.json"

    $result.markdown | Set-Content -Encoding UTF8 -LiteralPath $rawMarkdownPath
    Write-JsonFile -Path $resultJsonPath -Value $result
    Write-JsonFile -Path $taskJsonPath -Value $finalStatus
    Write-JsonFile -Path $transcriptJsonPath -Value $result.transcript
    $result.transcript.full_text | Set-Content -Encoding UTF8 -LiteralPath $transcriptTextPath

    $canonicalUrl = Get-CanonicalVideoUrl -Platform $effectivePlatform -SourceId $sourceId -OriginalUrl $VideoUrl

    $context = [ordered]@{
        skill_name          = $script:SkillName
        generated_at        = (Get-Date).ToString("s")
        source_url          = $canonicalUrl
        source_platform     = $effectivePlatform
        source_id           = $sourceId
        provider_id         = $effectiveProvider
        model_name          = $effectiveModel
        task_id             = $taskId
        note_root           = $noteRoot
        attachments_dir     = $attachmentsDir
        backend_result_path = $backendResultPath
        backend_status_path = $backendStatusPath
    }
    Write-JsonFile -Path $contextJsonPath -Value $context

    if (Test-Path -LiteralPath $backendResultPath) {
        Copy-Item -LiteralPath $backendResultPath -Destination $backendExportCopyPath -Force
    }
    if (Test-Path -LiteralPath $backendStatusPath) {
        Copy-Item -LiteralPath $backendStatusPath -Destination $backendStatusCopyPath -Force
    }

    $existingNotePaths = @(Get-ExistingNotePaths -Root $noteRoot -SourceId $sourceId)
    $canonicalNotePath = Join-Path $noteRoot "$sourceId - $safeTitle.md"
    if ($existingNotePaths.Count -gt 0) {
        $preferredExisting = if ($existingNotePaths -contains $canonicalNotePath) {
            $canonicalNotePath
        } else {
            $existingNotePaths[0]
        }

        if ($preferredExisting -ne $canonicalNotePath) {
            Move-Item -LiteralPath $preferredExisting -Destination $canonicalNotePath -Force
            $notePath = $canonicalNotePath
        } else {
            $notePath = $preferredExisting
        }
    } else {
        $notePath = $canonicalNotePath
    }

    $attachmentLinks = @(
        "- [BiliNote 原始 Markdown](<$(Get-RelativeMarkdownPath -FromFile $notePath -ToPath $rawMarkdownPath)>)",
        "- [转写全文](<$(Get-RelativeMarkdownPath -FromFile $notePath -ToPath $transcriptTextPath)>)",
        "- [转写 JSON](<$(Get-RelativeMarkdownPath -FromFile $notePath -ToPath $transcriptJsonPath)>)",
        "- [任务结果 JSON](<$(Get-RelativeMarkdownPath -FromFile $notePath -ToPath $taskJsonPath)>)",
        "- [上下文 JSON](<$(Get-RelativeMarkdownPath -FromFile $notePath -ToPath $contextJsonPath)>)"
    )
    if (Test-Path -LiteralPath $backendExportCopyPath) {
        $attachmentLinks += "- [后端导出 JSON](<$(Get-RelativeMarkdownPath -FromFile $notePath -ToPath $backendExportCopyPath)>)"
    }
    if (Test-Path -LiteralPath $backendStatusCopyPath) {
        $attachmentLinks += "- [后端状态 JSON](<$(Get-RelativeMarkdownPath -FromFile $notePath -ToPath $backendStatusCopyPath)>)"
    }

    $tags = @([string[]]$config.frontmatter_defaults.tags) + $effectivePlatform
    $related = [string[]]$config.frontmatter_defaults.related_links
    $bodyMarkdown = Normalize-BiliNoteMarkdown -Markdown ([string]$result.markdown)
    $generatedAt = (Get-Date).ToString("s")

    $rendered = Apply-Replacements -Template $template -Replacements @{
        "{{type}}"               = [string]$config.frontmatter_defaults.type
        "{{status}}"             = [string]$config.frontmatter_defaults.status
        "{{area_link}}"          = [string]$config.frontmatter_defaults.area_link
        "{{tags_block}}"         = (ConvertTo-YamlList -Values $tags -Indent 0)
        "{{related_block}}"      = (ConvertTo-YamlList -Values $related -Indent 0)
        "{{skill_name}}"         = $script:SkillName
        "{{source_platform}}"    = $effectivePlatform
        "{{source_url}}"         = $canonicalUrl
        "{{source_id}}"          = $sourceId
        "{{provider_id}}"        = $effectiveProvider
        "{{model_name}}"         = $effectiveModel
        "{{task_id}}"            = $taskId
        "{{attachments_dir}}"    = $attachmentsDir
        "{{generated_at}}"       = $generatedAt
        "{{title}}"              = $title
        "{{source_url_markdown}}"= "[原始视频链接](<$canonicalUrl>)"
        "{{body_markdown}}"      = $bodyMarkdown
        "{{attachments_links}}"  = ($attachmentLinks -join [Environment]::NewLine)
    }

    $rendered | Set-Content -Encoding UTF8 -LiteralPath $notePath

    foreach ($duplicatePath in $existingNotePaths) {
        if ($duplicatePath -ne $notePath -and (Test-Path -LiteralPath $duplicatePath)) {
            $duplicateContent = Get-Content -Raw -Encoding UTF8 -LiteralPath $duplicatePath
            if ($duplicateContent -match 'skill:\s+bilinote-video-note') {
                Remove-Item -LiteralPath $duplicatePath -Force
            }
        }
    }

    $output = [ordered]@{
        success             = $true
        platform            = $effectivePlatform
        source_id           = $sourceId
        task_id             = $taskId
        note_path           = $notePath
        attachments_dir     = $attachmentsDir
        backend_result_path = $backendResultPath
        message             = "BiliNote 视频笔记已写入 Obsidian。"
    }
    $output | ConvertTo-Json -Depth 10
} catch {
    $errorOutput = [ordered]@{
        success             = $false
        platform            = if ([string]::IsNullOrWhiteSpace($Platform)) { $null } else { $Platform }
        source_id           = $null
        task_id             = $null
        note_path           = $null
        attachments_dir     = $null
        backend_result_path = $null
        message             = $_.Exception.Message
    }
    $errorOutput | ConvertTo-Json -Depth 10
    exit 1
}
