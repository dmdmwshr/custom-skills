[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Position = 0)]
    [ValidateSet('status', 'list', 'validate', 'backup', 'add-rule', 'remove-rule')]
    [string]$Action = 'status',

    [string]$Browser,

    [string]$Profile,

    [string[]]$Value,

    [ValidateSet('url', 'window_title', 'process_name', 'process_path', 'process_description')]
    [string]$Location = 'url',

    [ValidateSet('any', 'domain', 'path')]
    [string]$Scope = 'domain',

    [switch]$Regex,

    [switch]$AppMode,

    [string]$ConfigPath = (Join-Path $env:APPDATA 'Browser Tamer\config.yml'),

    [string]$ExecutablePath,

    [switch]$AsJson
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$PreviewOnly = [bool]$WhatIfPreference
$WhatIfPreference = $false
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

function Write-Result {
    param([Parameter(Mandatory)]$InputObject)

    if ($AsJson) {
        $InputObject | ConvertTo-Json -Depth 12
    }
    else {
        $InputObject
    }
}

function ConvertFrom-BtYamlScalar {
    param([AllowEmptyString()][string]$Text)

    $trimmed = $Text.Trim()
    if ($trimmed.Length -ge 2 -and $trimmed.StartsWith('"') -and $trimmed.EndsWith('"')) {
        try {
            return ($trimmed | ConvertFrom-Json)
        }
        catch {
            throw "无法解析 Browser Tamer YAML 双引号字符串：$trimmed"
        }
    }

    if ($trimmed.Length -ge 2 -and $trimmed.StartsWith("'") -and $trimmed.EndsWith("'")) {
        return $trimmed.Substring(1, $trimmed.Length - 2).Replace("''", "'")
    }

    return $trimmed
}

function ConvertTo-BtYamlScalar {
    param([Parameter(Mandatory)][string]$Text)

    if ($Text.IndexOfAny([char[]]@("`r", "`n", [char]0)) -ge 0) {
        throw '规则值不能包含换行符或 NUL。'
    }

    $escaped = $Text.Replace('\', '\\').Replace('"', '\"').Replace("`t", '\t')
    return '"' + $escaped + '"'
}

function Read-BtConfig {
    param([Parameter(Mandatory)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "找不到 Browser Tamer v6 配置：$Path"
    }

    $resolved = (Resolve-Path -LiteralPath $Path).Path
    $bytes = [System.IO.File]::ReadAllBytes($resolved)
    $strictUtf8 = [System.Text.UTF8Encoding]::new($false, $true)
    try {
        $text = $strictUtf8.GetString($bytes)
    }
    catch {
        throw "配置文件不是有效 UTF-8，已停止修改：$resolved"
    }

    $eol = if ($text.Contains("`r`n")) { "`r`n" } else { "`n" }
    $lines = [regex]::Split($text, "\r?\n")
    $hash = (Get-FileHash -LiteralPath $resolved -Algorithm SHA256).Hash

    [PSCustomObject]@{
        Path  = $resolved
        Text  = $text
        Lines = [string[]]$lines
        Eol   = $eol
        Hash  = $hash
    }
}

function Get-ScalarField {
    param(
        [Parameter(Mandatory)][AllowEmptyString()][string[]]$Lines,
        [Parameter(Mandatory)][int]$Start,
        [Parameter(Mandatory)][int]$End,
        [Parameter(Mandatory)][string]$Pattern
    )

    for ($i = $Start; $i -lt $End; $i++) {
        if ($Lines[$i] -match $Pattern) {
            return ConvertFrom-BtYamlScalar $Matches[1]
        }
    }
    return $null
}

function Get-BtModel {
    param([Parameter(Mandatory)][AllowEmptyString()][string[]]$Lines)

    $browsersRoot = -1
    for ($i = 0; $i -lt $Lines.Count; $i++) {
        if ($Lines[$i] -match '^browsers:\s*$') {
            $browsersRoot = $i
            break
        }
    }
    if ($browsersRoot -lt 0) {
        throw '配置中缺少顶层 browsers 节点；此脚本只支持 Browser Tamer v6 生成的 YAML。'
    }

    $rootEnd = $Lines.Count
    for ($i = $browsersRoot + 1; $i -lt $Lines.Count; $i++) {
        if ($Lines[$i] -match '^\S') {
            $rootEnd = $i
            break
        }
    }

    $browserStarts = @()
    for ($i = $browsersRoot + 1; $i -lt $rootEnd; $i++) {
        if ($Lines[$i] -match '^  -\s*$') {
            $browserStarts += $i
        }
    }
    if ($browserStarts.Count -eq 0) {
        throw 'browsers 节点中没有浏览器条目。'
    }

    $browsers = @()
    for ($b = 0; $b -lt $browserStarts.Count; $b++) {
        $browserStart = $browserStarts[$b]
        $browserEnd = if ($b + 1 -lt $browserStarts.Count) { $browserStarts[$b + 1] } else { $rootEnd }

        $profilesKey = -1
        for ($i = $browserStart + 1; $i -lt $browserEnd; $i++) {
            if ($Lines[$i] -match '^    profiles:\s*$') {
                $profilesKey = $i
                break
            }
        }
        if ($profilesKey -lt 0) {
            continue
        }

        $browserName = Get-ScalarField -Lines $Lines -Start ($browserStart + 1) -End $profilesKey -Pattern '^    name:\s*(.*)$'
        $browserCommand = Get-ScalarField -Lines $Lines -Start ($browserStart + 1) -End $profilesKey -Pattern '^    cmd:\s*(.*)$'
        if ([string]::IsNullOrWhiteSpace($browserName)) {
            throw "索引 $browserStart 处的浏览器缺少 name。"
        }

        $profileStarts = @()
        for ($i = $profilesKey + 1; $i -lt $browserEnd; $i++) {
            if ($Lines[$i] -match '^      -\s*$') {
                $profileStarts += $i
            }
        }

        $profiles = @()
        for ($p = 0; $p -lt $profileStarts.Count; $p++) {
            $profileStart = $profileStarts[$p]
            $profileEnd = if ($p + 1 -lt $profileStarts.Count) { $profileStarts[$p + 1] } else { $browserEnd }
            $profileName = Get-ScalarField -Lines $Lines -Start ($profileStart + 1) -End $profileEnd -Pattern '^        name:\s*(.*)$'
            $profileArg = Get-ScalarField -Lines $Lines -Start ($profileStart + 1) -End $profileEnd -Pattern '^        arg:\s*(.*)$'
            $defaultRaw = Get-ScalarField -Lines $Lines -Start ($profileStart + 1) -End $profileEnd -Pattern '^        default:\s*(.*)$'

            if ([string]::IsNullOrWhiteSpace($profileName)) {
                throw "浏览器 '$browserName' 在索引 $profileStart 处的配置文件缺少 name。"
            }

            $rulesKey = -1
            for ($i = $profileStart + 1; $i -lt $profileEnd; $i++) {
                if ($Lines[$i] -match '^        rules:\s*$') {
                    $rulesKey = $i
                    break
                }
            }

            $rulesEnd = $profileEnd
            $rules = @()
            if ($rulesKey -ge 0) {
                for ($i = $rulesKey + 1; $i -lt $profileEnd; $i++) {
                    if ($Lines[$i] -match '^ {0,8}\S') {
                        $rulesEnd = $i
                        break
                    }
                }

                $ruleStarts = @()
                for ($i = $rulesKey + 1; $i -lt $rulesEnd; $i++) {
                    if ($Lines[$i] -match '^          -\s*$') {
                        $ruleStarts += $i
                    }
                }

                for ($r = 0; $r -lt $ruleStarts.Count; $r++) {
                    $ruleStart = $ruleStarts[$r]
                    $ruleEnd = if ($r + 1 -lt $ruleStarts.Count) { $ruleStarts[$r + 1] } else { $rulesEnd }
                    $ruleValue = Get-ScalarField -Lines $Lines -Start ($ruleStart + 1) -End $ruleEnd -Pattern '^            value:\s*(.*)$'
                    $ruleLocation = Get-ScalarField -Lines $Lines -Start ($ruleStart + 1) -End $ruleEnd -Pattern '^            loc:\s*(.*)$'
                    $ruleScope = Get-ScalarField -Lines $Lines -Start ($ruleStart + 1) -End $ruleEnd -Pattern '^            scope:\s*(.*)$'
                    $regexRaw = Get-ScalarField -Lines $Lines -Start ($ruleStart + 1) -End $ruleEnd -Pattern '^            is_regex:\s*(.*)$'
                    $appModeRaw = Get-ScalarField -Lines $Lines -Start ($ruleStart + 1) -End $ruleEnd -Pattern '^            app_mode:\s*(.*)$'

                    $rules += [PSCustomObject]@{
                        Start     = $ruleStart
                        End       = $ruleEnd
                        Value     = $ruleValue
                        Location  = if ($ruleLocation) { $ruleLocation } else { 'url' }
                        Scope     = if ($ruleScope) { $ruleScope } else { 'any' }
                        IsRegex   = $regexRaw -eq 'true'
                        AppMode   = $appModeRaw -eq 'true'
                    }
                }
            }

            $profiles += [PSCustomObject]@{
                Name       = $profileName
                Arg        = $profileArg
                IsDefault  = $defaultRaw -eq 'true'
                Start      = $profileStart
                End        = $profileEnd
                RulesKey   = $rulesKey
                RulesEnd   = $rulesEnd
                Rules      = @($rules)
            }
        }

        $browsers += [PSCustomObject]@{
            Name     = $browserName
            Command  = $browserCommand
            Start    = $browserStart
            End      = $browserEnd
            Profiles = @($profiles)
        }
    }

    [PSCustomObject]@{
        Browsers = @($browsers)
    }
}

function Find-BtTarget {
    param(
        [Parameter(Mandatory)]$Model,
        [Parameter(Mandatory)][string]$BrowserName,
        [Parameter(Mandatory)][string]$ProfileName
    )

    $browserMatches = @($Model.Browsers | Where-Object {
        [string]::Equals($_.Name, $BrowserName, [System.StringComparison]::OrdinalIgnoreCase)
    })
    if ($browserMatches.Count -ne 1) {
        $available = ($Model.Browsers.Name -join ', ')
        throw "浏览器名称必须唯一匹配。请求：'$BrowserName'；可用：$available"
    }

    $profileMatches = @($browserMatches[0].Profiles | Where-Object {
        [string]::Equals($_.Name, $ProfileName, [System.StringComparison]::OrdinalIgnoreCase)
    })
    if ($profileMatches.Count -ne 1) {
        $available = ($browserMatches[0].Profiles.Name -join ', ')
        throw "配置文件名称必须唯一匹配。请求：'$ProfileName'；可用：$available"
    }

    [PSCustomObject]@{
        Browser = $browserMatches[0]
        Profile = $profileMatches[0]
    }
}

function Add-LinesAt {
    param(
        [Parameter(Mandatory)][AllowEmptyString()][string[]]$Lines,
        [Parameter(Mandatory)][int]$Index,
        [Parameter(Mandatory)][string[]]$NewLines
    )

    $result = [System.Collections.Generic.List[string]]::new()
    for ($i = 0; $i -lt $Index; $i++) {
        $result.Add($Lines[$i])
    }
    foreach ($line in $NewLines) {
        $result.Add($line)
    }
    for ($i = $Index; $i -lt $Lines.Count; $i++) {
        $result.Add($Lines[$i])
    }
    return $result.ToArray()
}

function New-BtRuleLines {
    param([Parameter(Mandatory)][string]$RuleValue)

    $lines = [System.Collections.Generic.List[string]]::new()
    $lines.Add('          -')
    $lines.Add("            loc: $Location")
    if ($Scope -ne 'any') {
        $lines.Add("            scope: $Scope")
    }
    if ($Regex) {
        $lines.Add('            is_regex: true')
    }
    if ($AppMode) {
        $lines.Add('            app_mode: true')
    }
    $lines.Add('            value: ' + (ConvertTo-BtYamlScalar $RuleValue))
    return $lines.ToArray()
}

function Remove-LineRanges {
    param(
        [Parameter(Mandatory)][AllowEmptyString()][string[]]$Lines,
        [Parameter(Mandatory)]$Ranges
    )

    $remove = [System.Collections.Generic.HashSet[int]]::new()
    foreach ($range in $Ranges) {
        for ($i = $range.Start; $i -lt $range.End; $i++) {
            [void]$remove.Add($i)
        }
    }

    $result = [System.Collections.Generic.List[string]]::new()
    for ($i = 0; $i -lt $Lines.Count; $i++) {
        if (-not $remove.Contains($i)) {
            $result.Add($Lines[$i])
        }
    }
    return $result.ToArray()
}

function Get-BtExecutable {
    if ($ExecutablePath) {
        if (-not (Test-Path -LiteralPath $ExecutablePath -PathType Leaf)) {
            throw "指定的 Browser Tamer 可执行文件不存在：$ExecutablePath"
        }
        return (Resolve-Path -LiteralPath $ExecutablePath).Path
    }

    foreach ($process in @(Get-Process -Name bt -ErrorAction SilentlyContinue)) {
        try {
            if ($process.Path -and (Test-Path -LiteralPath $process.Path -PathType Leaf)) {
                return $process.Path
            }
        }
        catch {
        }
    }

    $commandKeys = @(
        'Registry::HKEY_CLASSES_ROOT\BrowserTamerHTM\shell\open\command',
        'HKCU:\Software\Classes\BrowserTamerHTM\shell\open\command',
        'HKLM:\Software\Classes\BrowserTamerHTM\shell\open\command'
    )
    foreach ($key in $commandKeys) {
        if (-not (Test-Path -LiteralPath $key)) {
            continue
        }
        $command = (Get-Item -LiteralPath $key).GetValue('')
        if ($command -match '^"([^"]+\.exe)"' -or $command -match '^([^"]+?\.exe)\b') {
            $candidate = $Matches[1]
            if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                return (Resolve-Path -LiteralPath $candidate).Path
            }
        }
    }

    foreach ($candidate in @(
        'D:\Program_Files\BrowserTamer\bt.exe',
        'C:\Program Files\Browser Tamer\bt.exe',
        'C:\Program Files\BrowserTamer\bt.exe'
    )) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    return $null
}

function Get-UrlHandler {
    param([Parameter(Mandatory)][ValidateSet('http', 'https')][string]$Scheme)

    $path = "HKCU:\Software\Microsoft\Windows\Shell\Associations\UrlAssociations\$Scheme\UserChoice"
    try {
        return (Get-ItemProperty -LiteralPath $path -ErrorAction Stop).ProgId
    }
    catch {
        return $null
    }
}

function New-BtBackup {
    param([Parameter(Mandatory)][string]$Path)

    $directory = Split-Path -Parent $Path
    $backupDirectory = Join-Path $directory 'backups'
    if (-not (Test-Path -LiteralPath $backupDirectory)) {
        [void](New-Item -ItemType Directory -Path $backupDirectory)
    }
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $backupPath = Join-Path $backupDirectory "config-$stamp.yml"
    Copy-Item -LiteralPath $Path -Destination $backupPath
    return $backupPath
}

function Save-BtConfig {
    param(
        [Parameter(Mandatory)]$Original,
        [Parameter(Mandatory)][AllowEmptyString()][string[]]$NewLines,
        [Parameter(Mandatory)][string]$Operation
    )

    $newText = [string]::Join($Original.Eol, $NewLines)
    if ($newText -ceq $Original.Text) {
        return [PSCustomObject]@{
            changed     = $false
            operation   = $Operation
            config_path = $Original.Path
            backup_path = $null
            verified    = $true
        }
    }

    [void](Get-BtModel -Lines $NewLines)

    if ($PreviewOnly) {
        return [PSCustomObject]@{
            changed     = $false
            planned     = $true
            operation   = $Operation
            config_path = $Original.Path
            backup_path = $null
            verified    = $true
        }
    }

    $currentHash = (Get-FileHash -LiteralPath $Original.Path -Algorithm SHA256).Hash
    if ($currentHash -ne $Original.Hash) {
        throw '配置文件在本次操作期间发生变化；为避免覆盖并发编辑，已停止写入。请重新运行。'
    }

    $backupPath = New-BtBackup -Path $Original.Path
    $directory = Split-Path -Parent $Original.Path
    $temporaryPath = Join-Path $directory ('.config-' + [guid]::NewGuid().ToString('N') + '.tmp')

    try {
        [System.IO.File]::WriteAllText(
            $temporaryPath,
            $newText,
            [System.Text.UTF8Encoding]::new($false)
        )

        $temporary = Read-BtConfig -Path $temporaryPath
        [void](Get-BtModel -Lines $temporary.Lines)

        Move-Item -LiteralPath $temporaryPath -Destination $Original.Path -Force
    }
    finally {
        if (Test-Path -LiteralPath $temporaryPath) {
            Remove-Item -LiteralPath $temporaryPath -Force
        }
    }

    if (Get-Process -Name bt -ErrorAction SilentlyContinue) {
        Start-Sleep -Milliseconds 1500
    }

    $verifiedConfig = Read-BtConfig -Path $Original.Path
    [void](Get-BtModel -Lines $verifiedConfig.Lines)

    [PSCustomObject]@{
        changed     = $true
        operation   = $Operation
        config_path = $Original.Path
        backup_path = $backupPath
        verified    = $true
        sha256      = $verifiedConfig.Hash
    }
}

function Assert-MutationParameters {
    if ([string]::IsNullOrWhiteSpace($Browser)) {
        throw '此操作需要 -Browser。'
    }
    if ([string]::IsNullOrWhiteSpace($Profile)) {
        throw '此操作需要 -Profile。'
    }
    if (-not $Value -or $Value.Count -eq 0) {
        throw '此操作至少需要一个 -Value。'
    }
    foreach ($item in $Value) {
        if ([string]::IsNullOrWhiteSpace($item)) {
            throw '规则值不能为空。'
        }
    }
    if (
        $Value.Count -eq 1 -and
        $Value[0] -match "^'[^']+'(?:,'[^']+')+$"
    ) {
        throw '收到的 -Value 看起来是被子 PowerShell 合并后的数组文本。请在当前 PowerShell 中直接调用脚本，并使用 -Value @("a","b")。'
    }
}

switch ($Action) {
    'status' {
        $executable = Get-BtExecutable
        $fileVersion = $null
        if ($executable) {
            $fileVersion = (Get-Item -LiteralPath $executable).VersionInfo.ProductVersion
        }

        $uiVersion = $null
        foreach ($process in @(Get-Process -Name bt -ErrorAction SilentlyContinue)) {
            if ($process.MainWindowTitle -match 'Browser Tamer\s+(\d+\.\d+\.\d+)') {
                $uiVersion = $Matches[1]
                break
            }
        }
        $http = Get-UrlHandler -Scheme http
        $https = Get-UrlHandler -Scheme https

        Write-Result ([PSCustomObject]@{
            executable_path  = $executable
            version          = if ($uiVersion) { $uiVersion } else { $fileVersion }
            ui_version       = $uiVersion
            file_version     = $fileVersion
            config_generation = if (Test-Path -LiteralPath $ConfigPath -PathType Leaf) { 'v6-yaml' } else { 'unknown' }
            running          = [bool](Get-Process -Name bt -ErrorAction SilentlyContinue)
            config_path      = $ConfigPath
            config_exists    = Test-Path -LiteralPath $ConfigPath -PathType Leaf
            http_handler     = $http
            https_handler    = $https
            is_default_http  = $http -eq 'BrowserTamerHTM'
            is_default_https = $https -eq 'BrowserTamerHTM'
        })
    }

    'validate' {
        $config = Read-BtConfig -Path $ConfigPath
        $model = Get-BtModel -Lines $config.Lines
        $profileCount = 0
        $ruleCount = 0
        foreach ($browserItem in $model.Browsers) {
            $profileCount += $browserItem.Profiles.Count
            foreach ($profileItem in $browserItem.Profiles) {
                $ruleCount += $profileItem.Rules.Count
            }
        }

        Write-Result ([PSCustomObject]@{
            valid         = $true
            config_path   = $config.Path
            sha256        = $config.Hash
            browser_count = $model.Browsers.Count
            profile_count = $profileCount
            rule_count    = $ruleCount
        })
    }

    'list' {
        $config = Read-BtConfig -Path $ConfigPath
        $model = Get-BtModel -Lines $config.Lines
        $result = @()
        foreach ($browserItem in $model.Browsers) {
            foreach ($profileItem in $browserItem.Profiles) {
                $result += [PSCustomObject]@{
                    browser   = $browserItem.Name
                    command   = $browserItem.Command
                    profile   = $profileItem.Name
                    arg       = $profileItem.Arg
                    default   = $profileItem.IsDefault
                    rules     = @($profileItem.Rules | ForEach-Object {
                        [PSCustomObject]@{
                            value     = $_.Value
                            location  = $_.Location
                            scope     = $_.Scope
                            is_regex  = $_.IsRegex
                            app_mode  = $_.AppMode
                        }
                    })
                }
            }
        }
        Write-Result $result
    }

    'backup' {
        $config = Read-BtConfig -Path $ConfigPath
        if ($PreviewOnly) {
            Write-Result ([PSCustomObject]@{
                changed     = $false
                planned     = $true
                config_path = $config.Path
                backup_path = $null
            })
        }
        else {
            $backupPath = New-BtBackup -Path $config.Path
            Write-Result ([PSCustomObject]@{
                changed     = $false
                config_path = $config.Path
                backup_path = $backupPath
                sha256      = (Get-FileHash -LiteralPath $backupPath -Algorithm SHA256).Hash
            })
        }
    }

    'add-rule' {
        Assert-MutationParameters
        $config = Read-BtConfig -Path $ConfigPath
        $model = Get-BtModel -Lines $config.Lines
        $target = Find-BtTarget -Model $model -BrowserName $Browser -ProfileName $Profile

        $existing = [System.Collections.Generic.HashSet[string]]::new(
            [System.StringComparer]::OrdinalIgnoreCase
        )
        foreach ($rule in $target.Profile.Rules) {
            if ($null -ne $rule.Value) {
                [void]$existing.Add([string]$rule.Value)
            }
        }

        $added = [System.Collections.Generic.List[string]]::new()
        $newRuleLines = [System.Collections.Generic.List[string]]::new()
        foreach ($item in $Value) {
            if ($existing.Add($item)) {
                foreach ($line in (New-BtRuleLines -RuleValue $item)) {
                    $newRuleLines.Add($line)
                }
                $added.Add($item)
            }
        }

        if ($added.Count -eq 0) {
            Write-Result ([PSCustomObject]@{
                changed = $false
                browser = $target.Browser.Name
                profile = $target.Profile.Name
                added   = @()
                skipped = @($Value)
                verified = $true
            })
            break
        }

        if ($target.Profile.RulesKey -lt 0) {
            $block = [System.Collections.Generic.List[string]]::new()
            $block.Add('        rules:')
            foreach ($line in $newRuleLines) {
                $block.Add($line)
            }
            $newLines = Add-LinesAt -Lines $config.Lines -Index $target.Profile.End -NewLines $block.ToArray()
        }
        else {
            $newLines = Add-LinesAt -Lines $config.Lines -Index $target.Profile.RulesEnd -NewLines $newRuleLines.ToArray()
        }

        $newModel = Get-BtModel -Lines $newLines
        $newTarget = Find-BtTarget -Model $newModel -BrowserName $Browser -ProfileName $Profile
        foreach ($item in $added) {
            if (-not @($newTarget.Profile.Rules | Where-Object {
                [string]::Equals($_.Value, $item, [System.StringComparison]::OrdinalIgnoreCase)
            })) {
                throw "写入前校验失败，未找到新增规则：$item"
            }
        }

        $save = Save-BtConfig -Original $config -NewLines $newLines -Operation "向 '$Browser -> $Profile' 新增规则"
        Write-Result ([PSCustomObject]@{
            changed     = $save.changed
            planned     = if ($save.PSObject.Properties.Name -contains 'planned') { $save.planned } else { $false }
            browser     = $target.Browser.Name
            profile     = $target.Profile.Name
            added       = @($added)
            skipped     = @($Value | Where-Object { -not $added.Contains($_) })
            location    = $Location
            scope       = $Scope
            is_regex    = [bool]$Regex
            app_mode    = [bool]$AppMode
            config_path = $save.config_path
            backup_path = $save.backup_path
            verified    = $save.verified
        })
    }

    'remove-rule' {
        Assert-MutationParameters
        $config = Read-BtConfig -Path $ConfigPath
        $model = Get-BtModel -Lines $config.Lines
        $target = Find-BtTarget -Model $model -BrowserName $Browser -ProfileName $Profile
        $requested = [System.Collections.Generic.HashSet[string]]::new(
            [System.StringComparer]::OrdinalIgnoreCase
        )
        foreach ($item in $Value) {
            [void]$requested.Add($item)
        }

        $matches = @($target.Profile.Rules | Where-Object {
            $null -ne $_.Value -and $requested.Contains([string]$_.Value)
        })
        if ($matches.Count -eq 0) {
            Write-Result ([PSCustomObject]@{
                changed  = $false
                browser  = $target.Browser.Name
                profile  = $target.Profile.Name
                removed  = @()
                missing  = @($Value)
                verified = $true
            })
            break
        }

        if ($matches.Count -eq $target.Profile.Rules.Count) {
            $ranges = @([PSCustomObject]@{
                Start = $target.Profile.RulesKey
                End   = $target.Profile.RulesEnd
            })
        }
        else {
            $ranges = @($matches | ForEach-Object {
                [PSCustomObject]@{ Start = $_.Start; End = $_.End }
            })
        }

        $newLines = Remove-LineRanges -Lines $config.Lines -Ranges $ranges
        $newModel = Get-BtModel -Lines $newLines
        $newTarget = Find-BtTarget -Model $newModel -BrowserName $Browser -ProfileName $Profile
        foreach ($rule in $newTarget.Profile.Rules) {
            if ($null -ne $rule.Value -and $requested.Contains([string]$rule.Value)) {
                throw "写入前校验失败，规则仍存在：$($rule.Value)"
            }
        }

        $save = Save-BtConfig -Original $config -NewLines $newLines -Operation "从 '$Browser -> $Profile' 删除规则"
        $removedValues = @($matches.Value | Select-Object -Unique)
        Write-Result ([PSCustomObject]@{
            changed     = $save.changed
            planned     = if ($save.PSObject.Properties.Name -contains 'planned') { $save.planned } else { $false }
            browser     = $target.Browser.Name
            profile     = $target.Profile.Name
            removed     = $removedValues
            missing     = @($Value | Where-Object { $_ -notin $removedValues })
            config_path = $save.config_path
            backup_path = $save.backup_path
            verified    = $save.verified
        })
    }
}
