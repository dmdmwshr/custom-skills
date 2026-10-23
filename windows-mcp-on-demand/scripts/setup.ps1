[CmdletBinding()]
param([ValidateSet('Show','Install','Remove')][string]$Action='Show')
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$taskPath='\DevProjects\CODEXENV\LIVE\'
$taskName='DEV-CODEXENV-LIVE-01-WindowsMCP'
$runtime='D:\Program_Files\Windows-MCP-Runtime\0.8.7-skill-v1'
$data='D:\12070\AppData\WindowsMCP'
$exe=Join-Path $runtime '.venv\TaskScripts\pythonw.exe'
$server=Join-Path $PSScriptRoot 'server.py'
$arguments='-B "'+$server+'"'
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$account="$env:COMPUTERNAME\12070"
$sid=([Security.Principal.NTAccount]::new($account)).Translate([Security.Principal.SecurityIdentifier])
if ($identity.User.Value -ne $sid.Value) {throw 'Unexpected account'}
$task=Get-ScheduledTask -TaskPath $taskPath -TaskName $taskName -ErrorAction SilentlyContinue
function Readback {
    $t=Get-ScheduledTask -TaskPath $taskPath -TaskName $taskName -ErrorAction SilentlyContinue
    if (!$t) {return @{exists=$false}}
    return @{exists=$true;task=($taskPath+$taskName);state=[string]$t.State;principal=$t.Principal;actions=$t.Actions;triggers=@($t.Triggers).Count;settings=$t.Settings}
}
if ($Action -eq 'Show') {Readback | ConvertTo-Json -Depth 6;exit}
if ($task -and ($task.State -eq 'Running')) {throw 'deferred_active_target'}
if ($task -and (!$task.Description.StartsWith('[DEV_TASK_V1][project=codex-env-win][id=LIVE-01][category=LIVE]'))) {throw 'Unknown existing task; no action'}
$activity=@(Get-CimInstance Win32_Process | Where-Object {$_.CommandLine -and $_.CommandLine.Contains($server)})
if ($activity.Count) {throw 'deferred_active_target'}
foreach ($path in @($data,(Join-Path $data 'runtime'),(Join-Path $data 'artifacts'),(Join-Path $data 'credentials'),(Join-Path $data 'receipts'))) {
    if (!(Test-Path -LiteralPath $path)) {New-Item -ItemType Directory -Path $path | Out-Null}
    if ((Get-Item -LiteralPath $path).Attributes -band [IO.FileAttributes]::ReparsePoint) {throw 'Reparse target refused'}
    $acl=[Security.AccessControl.DirectorySecurity]::new()
    $acl.SetOwner($sid);$acl.SetAccessRuleProtection($true,$false)
    foreach ($who in @($sid,[Security.Principal.SecurityIdentifier]::new('S-1-5-18'))) {
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($who,'FullControl','ContainerInherit,ObjectInherit','None','Allow'))
    }
    Set-Acl -LiteralPath $path -AclObject $acl
}
$runId=(Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfffZ')+'-'+[guid]::NewGuid().ToString('N')
$receipt=Join-Path $data ('receipts\'+$runId)
New-Item -ItemType Directory -Path $receipt | Out-Null
if ($task) {Export-ScheduledTask -TaskPath $taskPath -TaskName $taskName | Set-Content -LiteralPath (Join-Path $receipt 'before.xml') -Encoding UTF8}
if ($Action -eq 'Remove') {
    if ($task) {Unregister-ScheduledTask -TaskPath $taskPath -TaskName $taskName -Confirm:$false}
} else {
    if (!(Test-Path -LiteralPath $exe) -or !(Test-Path -LiteralPath $server)) {throw 'Verified launcher/server missing'}
    $pe=[IO.File]::ReadAllBytes($exe);$offset=[BitConverter]::ToInt32($pe,60)
    if ([BitConverter]::ToUInt16($pe,$offset+24+68) -ne 2) {throw 'Launcher is not Windows GUI subsystem'}
    $sameAction=@(Get-ScheduledTask | Where-Object {!(($_.TaskPath -eq $taskPath) -and ($_.TaskName -eq $taskName)) -and @($_.Actions | Where-Object {$_.Arguments -eq $arguments}).Count})
    if ($sameAction.Count) {throw 'Duplicate action found; no registration'}
    $a=New-ScheduledTaskAction -Execute $exe -Argument $arguments -WorkingDirectory $runtime
    $p=New-ScheduledTaskPrincipal -UserId $account -LogonType Interactive -RunLevel Limited
    $settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $d='[DEV_TASK_V1][project=codex-env-win][id=LIVE-01][category=LIVE] Windows-MCP on demand; no triggers; Limited Interactive; loopback authenticated; no model, login, trading or external sends. Stop after use.'
    $definition=New-ScheduledTask -Action $a -Principal $p -Settings $settings -Description $d
    Register-ScheduledTask -TaskPath $taskPath -TaskName $taskName -InputObject $definition -Force | Out-Null
    Export-ScheduledTask -TaskPath $taskPath -TaskName $taskName | Set-Content -LiteralPath (Join-Path $receipt 'after.xml') -Encoding UTF8
}
$out=@{run_id=$runId;host=$env:COMPUTERNAME;at=(Get-Date).ToUniversalTime().ToString('o');action=$Action;receipt=$receipt;readback=(Readback);service_started=$false}
$json=$out | ConvertTo-Json -Depth 8
[IO.File]::WriteAllText((Join-Path $receipt 'result.json'),$json,[Text.UTF8Encoding]::new($false))
$json
