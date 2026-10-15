function Resolve-VerifiedVideoMedia {
    param([string]$ReceiptPath, [string]$VideoUrl, [string]$Platform)
    if ($Platform -ne 'douyin') { throw '已核验媒体入口目前仅支持抖音来源。' }
    $receipt = Get-Content -LiteralPath $ReceiptPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($receipt.success -ne $true -or $receipt.status -ne 'completed' -or $receipt.action -ne 'download' -or $receipt.source_url -ne $VideoUrl) {
        throw '下载回执的模式、来源或完成状态不一致。'
    }
    if ([string]$receipt.source_id -notmatch '^\d{5,30}$' -or [string]$receipt.sha256 -notmatch '^[a-fA-F0-9]{64}$') {
        throw '下载回执缺少有效来源编号或摘要。'
    }
    $file = Get-Item -LiteralPath $receipt.file_path -ErrorAction Stop
    if ($file.PSIsContainer -or $file.Extension -notin @('.mp4','.mkv','.webm','.mov') -or $file.Length -le 0 -or $file.Length -ne $receipt.bytes) {
        throw '正式视频文件与回执不一致。'
    }
    if ($file.Name -notmatch ('\[' + [regex]::Escape([string]$receipt.source_id) + '\]\.[^.]+$')) { throw '正式视频文件名与来源编号不一致。' }
    $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($hash -ne $receipt.sha256) { throw '正式视频摘要已变化，未提交笔记任务。' }
    [pscustomobject]@{ReceiptPath=(Resolve-Path -LiteralPath $ReceiptPath).Path; FilePath=$file.FullName; Sha256=$hash; SourceId=[string]$receipt.source_id; Title=[string]$receipt.title; Extension=$file.Extension}
}

function Send-VerifiedVideoMedia {
    param($Media, [string]$BackendBaseUrl)
    $uri = [uri]$BackendBaseUrl
    if (-not $uri.IsLoopback -or $uri.Scheme -notin @('http','https')) { throw '已核验媒体仅发送给本机 BiliNote 服务。' }
    Add-Type -AssemblyName System.Net.Http | Out-Null
    $client = [Net.Http.HttpClient]::new()
    $form = [Net.Http.MultipartFormDataContent]::new()
    $stream = [IO.File]::OpenRead($Media.FilePath)
    $uploadName = 'douyin-' + $Media.SourceId + '-' + [guid]::NewGuid().ToString('N') + $Media.Extension
    try {
        $client.Timeout = [TimeSpan]::FromSeconds(180)
        $part = [Net.Http.StreamContent]::new($stream)
        $part.Headers.ContentType = [Net.Http.Headers.MediaTypeHeaderValue]::new('application/octet-stream')
        $form.Add($part,'file',$uploadName)
        $response = $client.PostAsync($BackendBaseUrl.TrimEnd('/')+'/api/upload',$form).GetAwaiter().GetResult()
        $body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult() | ConvertFrom-Json
        $expected = '/uploads/' + $uploadName
        if (-not $response.IsSuccessStatusCode -or $body.code -ne 0 -or [string]$body.data.url -ne $expected) { throw 'BiliNote 本机媒体上传未核实，保留原视频。' }
        return $expected
    } finally {
        $form.Dispose(); $stream.Dispose(); $client.Dispose()
    }
}
