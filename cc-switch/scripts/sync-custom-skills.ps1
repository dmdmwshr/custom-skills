[CmdletBinding(SupportsShouldProcess = $true)]
param(
    # 必须显式指定干净的发布镜像（WSL 源仓 main 的 Windows 检出）；不再默认旧的 自建skills 脏检出，机器人仍读取该旧路径
    [Parameter(Mandatory = $true)]
    [string]$SourceRoot,
    [string]$InstallRoot = 'C:\Users\12070\.cc-switch\skills',
    [string]$CodexSkillsRoot = 'C:\Users\12070\.codex\skills',
    [string]$GeminiSkillsRoot = '',
    [string]$DatabasePath = 'C:\Users\12070\.cc-switch\cc-switch.db',
    [string]$PythonPath = 'C:\Users\12070\AppData\Local\Programs\Python\Python312\python.exe',
    [string]$Remote = 'origin',
    [string]$Branch = 'main',
    [string]$Owner = 'dmdmwshr',
    [string]$Repository = 'custom-skills',
    [string[]]$Skill = @(),
    [switch]$SkipRemotePull,
    [switch]$SkipCodexClientSync
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

function Resolve-FullPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if ([string]::IsNullOrWhiteSpace($Path)) {
        throw '路径不能为空。'
    }

    return [System.IO.Path]::GetFullPath($Path)
}

function Test-ReparsePoint {
    param(
        [Parameter(Mandatory = $true)]
        [System.IO.FileSystemInfo]$Item
    )

    return (($Item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)
}

function Assert-NoReparsePoints {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $rootItem = Get-Item -LiteralPath $Path -Force
    if (Test-ReparsePoint $rootItem) {
        throw "拒绝处理重解析点目录：$Path"
    }

    foreach ($item in @(Get-ChildItem -LiteralPath $Path -Force -Recurse -ErrorAction Stop)) {
        if (Test-ReparsePoint $item) {
            throw "拒绝处理包含重解析点的路径：$($item.FullName)"
        }
    }
}

function Assert-Directory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        throw "$Label 不存在或不是目录：$Path"
    }

    $item = Get-Item -LiteralPath $Path -Force
    if (Test-ReparsePoint $item) {
        throw "$Label 是重解析点，拒绝继续：$Path"
    }
}

function Assert-File {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label 不存在或不是文件：$Path"
    }

    $item = Get-Item -LiteralPath $Path -Force
    if (Test-ReparsePoint $item) {
        throw "$Label 是重解析点，拒绝继续：$Path"
    }
}

function Assert-PathUnder {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Child,
        [Parameter(Mandatory = $true)]
        [string]$Parent
    )

    $parentFull = Resolve-FullPath $Parent
    $directorySeparator = [string][System.IO.Path]::DirectorySeparatorChar
    $alternateSeparator = [string][System.IO.Path]::AltDirectorySeparatorChar
    while ($parentFull.EndsWith($directorySeparator) -or $parentFull.EndsWith($alternateSeparator)) {
        $parentFull = $parentFull.Substring(0, $parentFull.Length - 1)
    }
    $parentFull = $parentFull + $directorySeparator
    $childFull = Resolve-FullPath $Child
    if (-not $childFull.StartsWith($parentFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "路径越界：$childFull 不在 $parentFull 下。"
    }
}

function Invoke-Git {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& $script:GitPath -C $script:SourceRoot @Arguments 2>&1)
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        $details = ($output | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine
        throw ("Git 操作失败（退出码 $exitCode）：git -C " + $script:SourceRoot + " " + ($Arguments -join ' ') + [Environment]::NewLine + $details)
    }

    return $output
}

function Invoke-PythonCode {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Code,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $encodedCode = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($Code))
    $runnerCode = 'import base64,sys; code=base64.b64decode(sys.argv[1]); sys.argv=sys.argv[:1]+sys.argv[2:]; exec(compile(code, ''<embedded>'', ''exec''))'
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& $script:PythonPath -X utf8 -c $runnerCode $encodedCode @Arguments 2>&1)
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        $details = ($output | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine
        throw ("Python 操作失败（退出码 $exitCode）：" + [Environment]::NewLine + $details)
    }

    return $output
}

function Get-SourceSkillDirectories {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Root
    )

    $result = @()
    foreach ($dir in @(Get-ChildItem -LiteralPath $Root -Directory -Force | Sort-Object Name)) {
        if (Test-ReparsePoint $dir) {
            throw "源仓库 skill 目录是重解析点，拒绝处理：$($dir.FullName)"
        }

        $skillFile = Join-Path $dir.FullName 'SKILL.md'
        if (-not (Test-Path -LiteralPath $skillFile -PathType Leaf)) {
            continue
        }

        if ($dir.Name -notmatch '^[a-z0-9][a-z0-9-]{0,63}$') {
            throw "skill 目录名不符合规范（只允许小写字母、数字和连字符）：$($dir.Name)"
        }

        Assert-NoReparsePoints $dir.FullName
        Assert-File $skillFile 'skill 元数据文件'
        $result += $dir
    }

    if ($result.Count -eq 0) {
        throw "源仓库中没有找到包含 SKILL.md 的 skill 目录：$Root"
    }

    return $result
}

function Copy-DirectoryChildren {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Source,
        [Parameter(Mandatory = $true)]
        [string]$Destination
    )

    Assert-Directory $Source '复制源目录'
    Assert-NoReparsePoints $Source
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null

    foreach ($child in @(Get-ChildItem -LiteralPath $Source -Force)) {
        if (Test-ReparsePoint $child) {
            throw "复制时发现重解析点，拒绝继续：$($child.FullName)"
        }

        $destinationChild = Join-Path $Destination $child.Name
        if ($child.PSIsContainer) {
            Copy-Item -LiteralPath $child.FullName -Destination $destinationChild -Recurse -Force
        } else {
            Copy-Item -LiteralPath $child.FullName -Destination $destinationChild -Force
        }
    }
}

function Copy-GitTrackedSkill {
    param(
        [Parameter(Mandatory = $true)]
        [System.IO.DirectoryInfo]$SkillDirectory,
        [Parameter(Mandatory = $true)]
        [string]$Destination
    )

    $skillName = $SkillDirectory.Name
    $prefix = "$skillName/"
    $trackedPaths = @(
        Invoke-Git @('-c', 'core.quotePath=false', 'ls-files', '--', $skillName) |
            ForEach-Object { $_.ToString().Trim() } |
            Where-Object { $_ }
    )
    if ($trackedPaths.Count -eq 0) {
        throw "Git 中没有已跟踪的 skill 文件：$skillName"
    }

    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    foreach ($repositoryPath in $trackedPaths) {
        $normalizedPath = $repositoryPath.Replace('\', '/')
        if (-not $normalizedPath.StartsWith($prefix, [System.StringComparison]::Ordinal)) {
            throw "Git 返回了超出 skill 目录的路径：$repositoryPath"
        }

        $relativePath = $normalizedPath.Substring($prefix.Length)
        if ([string]::IsNullOrWhiteSpace($relativePath)) {
            continue
        }

        $sourcePath = Resolve-FullPath (Join-Path $SourceRoot ($normalizedPath.Replace('/', [System.IO.Path]::DirectorySeparatorChar)))
        Assert-PathUnder $sourcePath $SkillDirectory.FullName
        Assert-File $sourcePath 'Git 已跟踪的 skill 文件'

        $destinationPath = Resolve-FullPath (Join-Path $Destination ($relativePath.Replace('/', [System.IO.Path]::DirectorySeparatorChar)))
        Assert-PathUnder $destinationPath $Destination
        $destinationParent = Split-Path -Parent $destinationPath
        New-Item -ItemType Directory -Path $destinationParent -Force | Out-Null
        Copy-Item -LiteralPath $sourcePath -Destination $destinationPath -Force
    }

    if (-not (Test-Path -LiteralPath (Join-Path $Destination 'SKILL.md') -PathType Leaf)) {
        throw "Git 暂存副本缺少 SKILL.md：$Destination"
    }
}

function New-BackupRoot {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Database
    )

    $backupParent = Join-Path ([System.IO.Path]::GetDirectoryName($Database)) 'skill-backups'
    if (Test-Path -LiteralPath $backupParent) {
        $backupParentItem = Get-Item -LiteralPath $backupParent -Force
        if (Test-ReparsePoint $backupParentItem) {
            throw "备份目录是重解析点，拒绝写入：$backupParent"
        }
    } else {
        New-Item -ItemType Directory -Path $backupParent -Force | Out-Null
    }

    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $backupRoot = Join-Path $backupParent "$stamp-custom-skills-before-sync"
    $suffix = 1
    while (Test-Path -LiteralPath $backupRoot) {
        $backupRoot = Join-Path $backupParent "$stamp-custom-skills-before-sync-$suffix"
        $suffix++
    }

    New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
    foreach ($childName in @(
        'installed-copy',
        'previous-live',
        'failed-install-new',
        'staging',
        'codex-previous-live',
        'codex-staging',
        'codex-failed-new'
    )) {
        New-Item -ItemType Directory -Path (Join-Path $backupRoot $childName) -Force | Out-Null
    }

    return $backupRoot
}

$SourceRoot = Resolve-FullPath $SourceRoot
$InstallRoot = Resolve-FullPath $InstallRoot
$CodexSkillsRoot = Resolve-FullPath $CodexSkillsRoot
$GeminiArgument = '-'
if (-not [string]::IsNullOrWhiteSpace($GeminiSkillsRoot)) { $GeminiArgument = Resolve-FullPath $GeminiSkillsRoot }
$DatabasePath = Resolve-FullPath $DatabasePath
$PythonPath = Resolve-FullPath $PythonPath

if ([System.StringComparer]::OrdinalIgnoreCase.Equals($SourceRoot, $InstallRoot)) {
    throw '源仓库和安装目录不能相同。'
}

Assert-Directory $SourceRoot '自建 skill 源仓库'
Assert-Directory $InstallRoot 'skill 安装根目录'
if (-not $SkipCodexClientSync) {
    Assert-Directory $CodexSkillsRoot 'Codex skill 根目录'
}
Assert-File $DatabasePath 'CC Switch 数据库'
Assert-File $PythonPath '固定 Python 解释器'

$databaseItem = Get-Item -LiteralPath $DatabasePath -Force
$databaseParent = Get-Item -LiteralPath (Split-Path -Parent $DatabasePath) -Force
if (Test-ReparsePoint $databaseParent) {
    throw "数据库父目录是重解析点，拒绝继续：$($databaseParent.FullName)"
}

$gitCommands = @(Get-Command -All git.exe -ErrorAction Stop | Where-Object { $_.CommandType -eq 'Application' })
if ($gitCommands.Count -eq 0) {
    throw 'PATH 中没有找到 git.exe。'
}
$script:GitPath = $gitCommands[0].Source
$script:SourceRoot = $SourceRoot
$script:PythonPath = $PythonPath

$gitTop = ((@(Invoke-Git @('rev-parse', '--show-toplevel')) | ForEach-Object { $_.ToString() }) -join '').Trim()
if (-not $gitTop) {
    throw "无法确认源仓库 Git 根目录：$SourceRoot"
}
$gitTop = Resolve-FullPath $gitTop
if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals($gitTop, $SourceRoot)) {
    throw "源仓库路径与 Git 根目录不一致：$SourceRoot / $gitTop"
}

$beforeStatus = @(Invoke-Git @('status', '--porcelain', '--untracked-files=all'))
if ($beforeStatus.Count -gt 0) {
    $details = ($beforeStatus | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine
    throw ("源仓库工作区不干净，请先审查、测试、提交并推送后再同步：" + [Environment]::NewLine + $details)
}

$currentBranch = ((@(Invoke-Git @('symbolic-ref', '--short', 'HEAD')) | ForEach-Object { $_.ToString() }) -join '').Trim()
if (-not [System.StringComparer]::Ordinal.Equals($currentBranch, $Branch)) {
    throw "当前分支不是要求的 $Branch，而是 $currentBranch。"
}

if (-not $SkipRemotePull -and -not $WhatIfPreference) {
    Invoke-Git @('fetch', '--prune', '--quiet', $Remote) | Out-Null
    Invoke-Git @('pull', '--ff-only', '--quiet', $Remote, $Branch) | Out-Null
}

$afterPullStatus = @(Invoke-Git @('status', '--porcelain', '--untracked-files=all'))
if ($afterPullStatus.Count -gt 0) {
    $details = ($afterPullStatus | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine
    throw ("拉取后源仓库出现未提交变化，拒绝同步：" + [Environment]::NewLine + $details)
}

$head = ((@(Invoke-Git @('rev-parse', 'HEAD')) | ForEach-Object { $_.ToString() }) -join '').Trim()
$remoteRef = "$Remote/$Branch"
$remoteHead = ((@(Invoke-Git @('rev-parse', $remoteRef)) | ForEach-Object { $_.ToString() }) -join '').Trim()
if (-not [System.StringComparer]::Ordinal.Equals($head, $remoteHead)) {
    throw "源仓库未与 $remoteRef 对齐：HEAD=$head，远端=$remoteHead。请先提交并推送，或解决分支差异。"
}

$skillDirectories = @(Get-SourceSkillDirectories $SourceRoot)
if ($Skill.Count -gt 0) {
    foreach ($name in $Skill) {
        if ($name -notmatch '^[a-z0-9][a-z0-9-]*$' -or $name -notin @($skillDirectories | ForEach-Object { $_.Name })) {
            throw "指定的 skill 不在当前受管源仓中：$name"
        }
    }
    $skillDirectories = @($skillDirectories | Where-Object { $_.Name -in $Skill })
}
$selectedSkills = ($skillDirectories | ForEach-Object { $_.Name }) -join ','
foreach ($skillDirectory in $skillDirectories) {
    $targetDirectory = Resolve-FullPath (Join-Path $InstallRoot $skillDirectory.Name)
    Assert-PathUnder $targetDirectory $InstallRoot

    if (Test-Path -LiteralPath $targetDirectory) {
        $targetItem = Get-Item -LiteralPath $targetDirectory -Force
        if (-not $targetItem.PSIsContainer) {
            throw "安装目标不是目录：$targetDirectory"
        }
        if (Test-ReparsePoint $targetItem) {
            throw "安装目标是重解析点，拒绝覆盖：$targetDirectory"
        }
        Assert-NoReparsePoints $targetDirectory
    }
}

$registrationCode = @'
import hashlib
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

try:
    import yaml
except Exception as exc:
    raise SystemExit(f"PyYAML 不可用：{exc}")

mode, source_root_text, database_text, owner, repository, branch, selected_text = sys.argv[1:8]
install_root = Path(sys.argv[8]).resolve() if len(sys.argv) > 8 else None
compat_path = Path(sys.argv[9]) if len(sys.argv) > 9 else None
selected = set(selected_text.split(','))


def dir_hash(root: Path) -> str:
    """CC Switch 上游 compute_dir_hash（Desktop 4.0.18 已与本机 50 个自建安装副本逐项核对）：
    递归所有非点开头文件/目录，按路径组件字节序排序，逐文件 feed 相对路径(/)+NUL+原始字节+NUL。"""
    files = []

    def walk(current: Path) -> None:
        for entry in os.scandir(current):
            if entry.name.startswith("."):
                continue
            if entry.is_dir():
                walk(Path(entry.path))
            else:
                files.append(Path(entry.path))

    walk(root)
    files.sort(key=lambda p: [part.encode("utf-8") for part in p.relative_to(root).parts])
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


source_root = Path(source_root_text).resolve()
database_path = Path(database_text).resolve()
frontmatter_pattern = re.compile(
    r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)",
    re.DOTALL,
)

skills = []
for skill_path in sorted(source_root.iterdir(), key=lambda item: item.name.lower()):
    if skill_path.name not in selected:
        continue
    if not skill_path.is_dir() or skill_path.is_symlink():
        continue

    skill_file = skill_path / "SKILL.md"
    if not skill_file.is_file():
        continue

    text = skill_file.read_text(encoding="utf-8-sig")
    match = frontmatter_pattern.match(text)
    if match is None:
        raise SystemExit(f"SKILL.md 缺少有效 YAML frontmatter：{skill_file}")

    metadata = yaml.safe_load(match.group(1)) or {}
    if not isinstance(metadata, dict):
        raise SystemExit(f"SKILL.md frontmatter 不是对象：{skill_file}")

    name = metadata.get("name")
    if name != skill_path.name:
        raise SystemExit(
            f"skill name 与目录名不一致：{skill_file} name={name!r} directory={skill_path.name!r}"
        )

    description = metadata.get("description") or ""
    if not isinstance(description, str):
        description = str(description)

    skills.append((name, description.strip()))

if not skills:
    raise SystemExit(f"没有找到可登记的 skill：{source_root}")

connection = sqlite3.connect(str(database_path))
try:
    repository_row = connection.execute(
        "select branch, enabled from skill_repos where owner=? and name=?",
        (owner, repository),
    ).fetchone()
    if repository_row is None:
        raise SystemExit(f"skill_repos 未登记：{owner}/{repository}")
    if repository_row[0] != branch or int(repository_row[1]) != 1:
        raise SystemExit(
            f"skill_repos 未启用或分支不匹配：{owner}/{repository} "
            f"branch={repository_row[0]!r} enabled={repository_row[1]!r}"
        )

    if mode == "validate":
        print(f"validated={len(skills)}")
    elif mode == "register":
        now = int(__import__("time").time())
        # content_hash 只在已核验的结构版本下按上游算法写入；其余版本保持旧行为（保留已有值、新记录留空）并明确提示
        schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
        has_hash_column = any(row[1] == "content_hash" for row in connection.execute("PRAGMA table_info(skills)"))
        verified_schemas = []
        if compat_path is not None and compat_path.is_file():
            verified_schemas = json.loads(compat_path.read_text(encoding="utf-8")).get("content_hash_verified_schemas", [])
        write_hash = install_root is not None and has_hash_column and schema_version in verified_schemas
        if not write_hash:
            print(f"content_hash_not_written schema={schema_version} verified={verified_schemas}")
        connection.execute("begin immediate")
        try:
            for name, description in skills:
                skill_id = f"{owner}/{repository}:{name}"
                readme_url = (
                    f"https://github.com/{owner}/{repository}/blob/{branch}/{name}/SKILL.md"
                )
                existing = connection.execute(
                    "select 1 from skills where id=?",
                    (skill_id,),
                ).fetchone()

                if existing is not None:
                    connection.execute(
                        """
                        update skills
                        set name=?, description=?, directory=?, repo_owner=?,
                            repo_name=?, repo_branch=?, readme_url=?,
                            installed_at=?, updated_at=?
                        where id=?
                        """,
                        (
                            name,
                            description,
                            name,
                            owner,
                            repository,
                            branch,
                            readme_url,
                            now,
                            now,
                            skill_id,
                        ),
                    )
                else:
                    connection.execute(
                        """
                        insert into skills (
                            id, name, description, directory, repo_owner, repo_name,
                            repo_branch, readme_url, enabled_claude, enabled_codex,
                            enabled_gemini, enabled_opencode, installed_at, updated_at,
                            enabled_hermes, enabled_grokbuild
                        )
                        values (?, ?, ?, ?, ?, ?, ?, ?, 0, 1, 0, 0, ?, ?, 0, 0)
                        """,
                        (
                            skill_id,
                            name,
                            description,
                            name,
                            owner,
                            repository,
                            branch,
                            readme_url,
                            now,
                            now,
                        ),
                    )

                if write_hash:
                    connection.execute(
                        "update skills set content_hash=? where id=?",
                        (dir_hash(install_root / name), skill_id),
                    )

            connection.commit()
        except Exception:
            connection.rollback()
            raise

        print(f"registered={len(skills)} content_hash_written={int(write_hash)}")
    else:
        raise SystemExit(f"未知模式：{mode}")
finally:
    connection.close()
'@

$codexSyncCode = [System.IO.File]::ReadAllText((Join-Path $PSScriptRoot 'sync_custom_clients.py'), [System.Text.Encoding]::UTF8)

Invoke-PythonCode -Code $registrationCode -Arguments @('validate', $SourceRoot, $DatabasePath, $Owner, $Repository, $Branch, $selectedSkills) | Out-Null
if (-not $SkipCodexClientSync) {
    $codexValidation = Invoke-PythonCode -Code $codexSyncCode -Arguments @(
        'validate',
        $SourceRoot,
        $InstallRoot,
        $CodexSkillsRoot,
        $DatabasePath,
        '-',
        $Owner,
        $Repository,
        $selectedSkills,
        $GeminiArgument
    )
    $codexValidation | ForEach-Object { Write-Output $_ }
}

Write-Output "源仓库检查通过：skills=$($skillDirectories.Count) HEAD=$head 远端=$remoteRef"
if ($WhatIfPreference) {
    Write-Output 'Dry-run：不会创建备份、复制文件或写入数据库。'
    Write-Output "安装根目录：$InstallRoot"
    if (-not $SkipCodexClientSync) {
        Write-Output "Codex skill 根目录：$CodexSkillsRoot"
    }
    Write-Output "数据库：$DatabasePath"
    exit 0
}

$backupRoot = New-BackupRoot $DatabasePath
$databaseBackup = Join-Path $backupRoot 'cc-switch.db'
$installedBackupRoot = Join-Path $backupRoot 'installed-copy'
$previousLiveRoot = Join-Path $backupRoot 'previous-live'
$failedInstallRoot = Join-Path $backupRoot 'failed-install-new'
$stagingRoot = Join-Path $backupRoot 'staging'

$databaseBackupCode = @'
import sqlite3
import sys
from pathlib import Path

source = Path(sys.argv[1]).resolve()
target = Path(sys.argv[2]).resolve()
source_connection = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
target_connection = sqlite3.connect(str(target))
try:
    source_connection.backup(target_connection)
finally:
    target_connection.close()
    source_connection.close()
print(target)
'@

Invoke-PythonCode -Code $databaseBackupCode -Arguments @($DatabasePath, $databaseBackup) | Out-Null

$existingCount = 0
$initiallyExisting = @{}
$movedPrevious = @{}
$publishedInstalls = @{}
$codexSyncResult = @()

try {
    foreach ($skillDirectory in $skillDirectories) {
        $skillName = $skillDirectory.Name
        $targetDirectory = Resolve-FullPath (Join-Path $InstallRoot $skillName)
        $exists = Test-Path -LiteralPath $targetDirectory
        $initiallyExisting[$skillName] = $exists
        if ($exists) {
            $backupDirectory = Join-Path $installedBackupRoot $skillName
            Copy-DirectoryChildren -Source $targetDirectory -Destination $backupDirectory
            $existingCount++
        }
    }

    foreach ($skillDirectory in $skillDirectories) {
        $skillName = $skillDirectory.Name
        $stagingDirectory = Join-Path $stagingRoot $skillName
        Copy-GitTrackedSkill -SkillDirectory $skillDirectory -Destination $stagingDirectory
    }

    foreach ($skillDirectory in $skillDirectories) {
        $skillName = $skillDirectory.Name
        $targetDirectory = Resolve-FullPath (Join-Path $InstallRoot $skillName)
        $previousDirectory = Join-Path $previousLiveRoot $skillName
        $stagingDirectory = Join-Path $stagingRoot $skillName

        if ($initiallyExisting[$skillName]) {
            Move-Item -LiteralPath $targetDirectory -Destination $previousDirectory
            $movedPrevious[$skillName] = $true
        }
        Move-Item -LiteralPath $stagingDirectory -Destination $targetDirectory
        $publishedInstalls[$skillName] = $true
    }

    foreach ($skillDirectory in $skillDirectories) {
        $targetDirectory = Resolve-FullPath (Join-Path $InstallRoot $skillDirectory.Name)
        if (-not (Test-Path -LiteralPath (Join-Path $targetDirectory 'SKILL.md') -PathType Leaf)) {
            throw "同步后安装副本缺少 SKILL.md：$targetDirectory"
        }
        Assert-NoReparsePoints $targetDirectory
    }

    Invoke-PythonCode -Code $registrationCode -Arguments @('register', $SourceRoot, $DatabasePath, $Owner, $Repository, $Branch, $selectedSkills, $InstallRoot, (Join-Path $PSScriptRoot 'cc-switch-compat.json')) | ForEach-Object { Write-Output $_ }

    if (-not $SkipCodexClientSync) {
        $codexSyncResult = @(
            Invoke-PythonCode -Code $codexSyncCode -Arguments @(
                'sync',
                $SourceRoot,
                $InstallRoot,
                $CodexSkillsRoot,
                $DatabasePath,
                $backupRoot,
                $Owner,
                $Repository,
                $selectedSkills,
        $GeminiArgument
            )
        )
    }
} catch {
    $syncFailure = $_.Exception.Message
    $rollbackErrors = @()
    $rollbackNames = @($skillDirectories | ForEach-Object { $_.Name })
    [array]::Reverse($rollbackNames)

    foreach ($skillName in $rollbackNames) {
        $targetDirectory = Resolve-FullPath (Join-Path $InstallRoot $skillName)
        $previousDirectory = Join-Path $previousLiveRoot $skillName
        $failedDirectory = Join-Path $failedInstallRoot $skillName
        try {
            if ($publishedInstalls.ContainsKey($skillName) -and (Test-Path -LiteralPath $targetDirectory)) {
                Move-Item -LiteralPath $targetDirectory -Destination $failedDirectory
            }
            if ($movedPrevious.ContainsKey($skillName) -and (Test-Path -LiteralPath $previousDirectory)) {
                Move-Item -LiteralPath $previousDirectory -Destination $targetDirectory
            }

            $existsAfterRollback = Test-Path -LiteralPath $targetDirectory
            if ($initiallyExisting.ContainsKey($skillName) -and $initiallyExisting[$skillName] -ne $existsAfterRollback) {
                throw '安装副本未恢复到同步前的存在状态。'
            }
        } catch {
            $rollbackErrors += "$skillName：$($_.Exception.Message)"
        }
    }

    try {
        Invoke-PythonCode -Code $databaseBackupCode -Arguments @($databaseBackup, $DatabasePath) | Out-Null
    } catch {
        $rollbackErrors += "数据库：$($_.Exception.Message)"
    }

    if ($rollbackErrors.Count -gt 0) {
        throw "同步失败且恢复不完整：$syncFailure；$($rollbackErrors -join '；')；备份位置：$backupRoot"
    }
    throw "同步失败，安装副本和数据库已恢复：$syncFailure；备份位置：$backupRoot"
}

Write-Output "同步完成：skills=$($skillDirectories.Count)，已有安装副本备份=$existingCount。"
if ($codexSyncResult.Count -gt 0) {
    $codexSyncResult | ForEach-Object { Write-Output $_ }
}
Write-Output "备份位置：$backupRoot"
Write-Output "数据库备份：$databaseBackup"
Write-Output "源仓库 HEAD：$head"
