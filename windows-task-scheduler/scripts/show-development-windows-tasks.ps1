[CmdletBinding()]
param(
    [ValidateSet("Project", "Development", "AllCustom")]
    [string]$Scope = "Project",

    [string]$ProjectKey,

    [switch]$AsJson
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

function Get-TriggerSummary {
    param(
        [AllowNull()]
        [AllowEmptyCollection()]
        [object[]]$Triggers
    )

    $summaries = foreach ($trigger in @($Triggers)) {
        if ($null -eq $trigger) {
            continue
        }
        $type = [string]$trigger.CimClass.CimClassName
        $interval = [string]$trigger.Repetition.Interval
        $start = [string]$trigger.StartBoundary
        switch ($type) {
            "MSFT_TaskLogonTrigger" {
                "Logon"
                continue
            }
            "MSFT_TaskDailyTrigger" {
                if ($start) {
                    "Daily@$start"
                }
                else {
                    "Daily"
                }
                continue
            }
            "MSFT_TaskTimeTrigger" {
                if ($interval) {
                    "Time@$start/Repeat=$interval"
                }
                else {
                    "Time@$start"
                }
                continue
            }
            default {
                if ($interval) {
                    "$type/Repeat=$interval"
                }
                else {
                    $type
                }
            }
        }
    }
    return ($summaries -join "; ")
}

function Get-PortableExecutableSubsystem {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return "missing"
    }

    $stream = $null
    $reader = $null
    try {
        $stream = [System.IO.File]::Open(
            $Path,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read,
            [System.IO.FileShare]::ReadWrite
        )
        $reader = New-Object System.IO.BinaryReader($stream)
        if ($reader.ReadUInt16() -ne 0x5A4D) {
            return "not_pe"
        }
        $stream.Position = 0x3C
        $peOffset = $reader.ReadInt32()
        if ($peOffset -lt 0 -or ($peOffset + 94) -gt $stream.Length) {
            return "invalid_pe"
        }
        $stream.Position = $peOffset
        if ($reader.ReadUInt32() -ne 0x00004550) {
            return "invalid_pe"
        }
        $stream.Position = $peOffset + 4 + 20 + 68
        $subsystem = $reader.ReadUInt16()
        switch ($subsystem) {
            2 { return "windows_gui" }
            3 { return "windows_console" }
            default { return "other_$subsystem" }
        }
    }
    catch {
        return "unreadable"
    }
    finally {
        if ($reader) {
            $reader.Dispose()
        }
        elseif ($stream) {
            $stream.Dispose()
        }
    }
}

function Get-FileSha256Safe {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    try {
        if (Test-Path -LiteralPath $Path -PathType Leaf) {
            return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash
        }
    }
    catch {
        return $null
    }
    return $null
}

function Get-BackgroundLauncherAudit {
    param(
        [Parameter(Mandatory = $true)]
        [object[]]$Actions,

        [Parameter(Mandatory = $true)]
        [bool]$TaskHidden
    )

    $pythonActions = @(
        $Actions | Where-Object {
            [IO.Path]::GetFileName(([string]$_.Execute).Trim('"')) -in @(
                "python.exe",
                "pythonw.exe"
            )
        }
    )
    if ($pythonActions.Count -eq 0) {
        return [pscustomobject]@{
            Status = "not_python"
            PeSubsystem = "not_applicable"
            BytecodeCacheDisabled = $null
            NoWindowCompliant = $null
        }
    }

    $statuses = @()
    $subsystems = @()
    $bytecodeDisabled = $true
    foreach ($action in $pythonActions) {
        $execute = ([string]$action.Execute).Trim('"')
        $arguments = [string]$action.Arguments
        if (-not ($arguments -eq "-B" -or $arguments.StartsWith("-B ", [StringComparison]::Ordinal))) {
            $bytecodeDisabled = $false
        }

        $fileName = [IO.Path]::GetFileName($execute).ToLowerInvariant()
        if ($fileName -eq "python.exe") {
            $statuses += "console_popup_risk"
            $subsystems += Get-PortableExecutableSubsystem -Path $execute
            continue
        }

        $subsystem = Get-PortableExecutableSubsystem -Path $execute
        $subsystems += $subsystem
        if ($subsystem -eq "windows_console") {
            $statuses += "console_popup_risk"
            continue
        }
        if ($subsystem -ne "windows_gui") {
            $statuses += "launcher_missing_or_unreadable"
            continue
        }

        $launcherHash = Get-FileSha256Safe -Path $execute
        $launcherDirectory = Split-Path -Parent $execute
        $launcherDirectoryName = Split-Path -Leaf $launcherDirectory
        $venvRoot = if ($launcherDirectoryName -in @("Scripts", "TaskScripts")) {
            Split-Path -Parent $launcherDirectory
        }
        else {
            $null
        }
        $hashAlias = $false
        if ($launcherHash -and $venvRoot) {
            foreach ($candidateName in @("python.exe", "pythonw.exe")) {
                $candidate = Join-Path $venvRoot ("Scripts\" + $candidateName)
                if ([string]::Equals($candidate, $execute, [StringComparison]::OrdinalIgnoreCase)) {
                    continue
                }
                $candidateHash = Get-FileSha256Safe -Path $candidate
                if ($candidateHash -and $candidateHash -eq $launcherHash) {
                    $candidateSubsystem = Get-PortableExecutableSubsystem -Path $candidate
                    if ($candidateSubsystem -eq "windows_console") {
                        $hashAlias = $true
                        break
                    }
                }
            }
        }
        if ($hashAlias) {
            $statuses += "gui_hash_alias_risk"
        }
        else {
            $statuses += "verified_gui"
        }
    }

    $status = if ($statuses -contains "console_popup_risk") {
        "console_popup_risk"
    }
    elseif ($statuses -contains "gui_hash_alias_risk") {
        "gui_hash_alias_risk"
    }
    elseif ($statuses -contains "launcher_missing_or_unreadable") {
        "launcher_missing_or_unreadable"
    }
    else {
        "verified_gui"
    }
    return [pscustomobject]@{
        Status = $status
        PeSubsystem = ($subsystems | Select-Object -Unique) -join " | "
        BytecodeCacheDisabled = $bytecodeDisabled
        NoWindowCompliant = ($status -eq "verified_gui" -and $TaskHidden)
    }
}

function Get-ActionMetadata {
    param(
        [Parameter(Mandatory = $true)]
        [object[]]$Actions
    )

    $executables = @()
    $workingDirectories = @()
    $entrypoints = @()
    foreach ($action in $Actions) {
        $execute = [string]$action.Execute
        $arguments = [string]$action.Arguments
        $workingDirectory = [string]$action.WorkingDirectory
        if ($execute) {
            $executables += $execute
        }
        if ($workingDirectory) {
            $workingDirectories += $workingDirectory
        }
        if ($arguments -match '"([^"]+\.(?:ps1|py|vbs|cmd|bat|exe))"') {
            $entrypoints += $Matches[1]
        }
    }
    return [pscustomobject]@{
        Executable = ($executables | Select-Object -Unique) -join " | "
        WorkingDirectory = (
            $workingDirectories | Select-Object -Unique
        ) -join " | "
        Entrypoint = ($entrypoints | Select-Object -Unique) -join " | "
        SearchText = (
            @($executables) +
            @($workingDirectories) +
            @($Actions | ForEach-Object { [string]$_.Arguments })
        ) -join " "
    }
}

function Resolve-TaskOwnership {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Task,

        [Parameter(Mandatory = $true)]
        [string]$ActionSearchText
    )

    $description = [string]$Task.Description
    if (
        $description -match
        '\[DEV_TASK_V1\]\[project=([^\]]+)\]\[id=([^\]]+)\]\[category=([^\]]+)\]'
    ) {
        return [pscustomobject]@{
            Classification = "managed_v1"
            Project = $Matches[1]
            CatalogId = $Matches[2]
            Category = $Matches[3]
            GovernanceStatus = "registered"
        }
    }

    if (
        [string]$Task.TaskPath -match
        '^\\DevProjects\\([^\\]+)\\([^\\]+)\\$'
    ) {
        return [pscustomobject]@{
            Classification = "managed_path_incomplete"
            Project = $Matches[1].ToLowerInvariant()
            CatalogId = ""
            Category = $Matches[2]
            GovernanceStatus = "description_tag_missing"
        }
    }

    if (
        $ActionSearchText -match
        '(?i)\\Desktop\\(?:[^\\]+\\)*项目开发\\([^\\\s"]+)'
    ) {
        return [pscustomobject]@{
            Classification = "legacy_project"
            Project = $Matches[1]
            CatalogId = ""
            Category = ""
            GovernanceStatus = "legacy_unregistered"
        }
    }

    if ($description -match '(?i)Managed by ([a-z0-9._-]+)') {
        return [pscustomobject]@{
            Classification = "legacy_project"
            Project = $Matches[1]
            CatalogId = ""
            Category = ""
            GovernanceStatus = "legacy_unregistered"
        }
    }

    if ($description -match '(?i)(?:only|solely|仅)优化\s+([a-z0-9._-]+)') {
        return [pscustomobject]@{
            Classification = "legacy_project"
            Project = $Matches[1]
            CatalogId = ""
            Category = ""
            GovernanceStatus = "legacy_unregistered"
        }
    }

    if ([string]$Task.TaskName -match '^Project-([^-]+)-') {
        return [pscustomobject]@{
            Classification = "legacy_project"
            Project = $Matches[1].ToLowerInvariant()
            CatalogId = ""
            Category = ""
            GovernanceStatus = "legacy_unregistered"
        }
    }

    if ([string]$Task.TaskName -eq "cc-connect") {
        return [pscustomobject]@{
            Classification = "developer_tool"
            Project = "shared-development-tool"
            CatalogId = ""
            Category = "SERVICE"
            GovernanceStatus = "tool_managed"
        }
    }

    return [pscustomobject]@{
        Classification = "other_custom"
        Project = ""
        CatalogId = ""
        Category = ""
        GovernanceStatus = "not_project_classified"
    }
}

$tasks = @(
    Get-ScheduledTask |
        Where-Object { $_.TaskPath -notlike "\Microsoft\*" } |
        Sort-Object TaskPath, TaskName
)

$rows = foreach ($task in $tasks) {
    $action = Get-ActionMetadata -Actions @($task.Actions)
    $owner = Resolve-TaskOwnership `
        -Task $task `
        -ActionSearchText $action.SearchText
    $include = switch ($Scope) {
        "Project" {
            $owner.Classification -in @(
                "managed_v1",
                "managed_path_incomplete",
                "legacy_project"
            )
        }
        "Development" {
            $owner.Classification -in @(
                "managed_v1",
                "managed_path_incomplete",
                "legacy_project",
                "developer_tool"
            )
        }
        "AllCustom" {
            $true
        }
    }
    if (-not $include) {
        continue
    }
    if (
        $ProjectKey -and
        $owner.Project -notlike $ProjectKey
    ) {
        continue
    }

    $info = $task | Get-ScheduledTaskInfo
    $launcherAudit = Get-BackgroundLauncherAudit `
        -Actions @($task.Actions) `
        -TaskHidden ([bool]$task.Settings.Hidden)
    [pscustomobject]@{
        Classification = $owner.Classification
        ProjectKey = $owner.Project
        CatalogId = $owner.CatalogId
        Category = $owner.Category
        GovernanceStatus = $owner.GovernanceStatus
        TaskPath = [string]$task.TaskPath
        TaskName = [string]$task.TaskName
        State = [string]$task.State
        Trigger = Get-TriggerSummary -Triggers @($task.Triggers)
        Executable = $action.Executable
        Entrypoint = $action.Entrypoint
        WorkingDirectory = $action.WorkingDirectory
        TaskHidden = [bool]$task.Settings.Hidden
        PythonLauncherStatus = $launcherAudit.Status
        PythonPeSubsystem = $launcherAudit.PeSubsystem
        PythonBytecodeCacheDisabled = $launcherAudit.BytecodeCacheDisabled
        NoWindowLauncherCompliant = $launcherAudit.NoWindowCompliant
        LastRunTime = $info.LastRunTime
        LastTaskResult = $info.LastTaskResult
        NextRunTime = $info.NextRunTime
        Description = [string]$task.Description
    }
}

if ($AsJson) {
    $rows | ConvertTo-Json -Depth 5
    exit 0
}

$rows |
    Sort-Object ProjectKey, TaskPath, TaskName |
    Format-Table `
        ProjectKey,
        State,
        GovernanceStatus,
        @{
            Label = "TaskIdentity"
            Expression = { "$($_.TaskPath)$($_.TaskName)" }
        },
        PythonLauncherStatus,
        Trigger `
        -AutoSize
