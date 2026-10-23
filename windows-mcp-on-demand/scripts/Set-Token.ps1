[CmdletBinding()]
param()
$ErrorActionPreference='Stop'
$path='D:\12070\AppData\WindowsMCP\credentials\auth.key'
$dir=Split-Path $path
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$sid=([Security.Principal.NTAccount]::new("$env:COMPUTERNAME\12070")).Translate([Security.Principal.SecurityIdentifier])
if ($identity.User.Value -ne $sid.Value) {throw 'Unexpected account'}
if (![Environment]::UserInteractive -or [Console]::IsInputRedirected) {throw 'Run locally in an interactive terminal; no piped token'}
if (!(Test-Path -LiteralPath $dir)) {throw 'Run the reviewed setup first'}
if (Test-Path -LiteralPath $path) {throw 'Credential exists; preserved. Rotation requires a separate explicit action.'}
if ((Get-Item -LiteralPath $dir).Attributes -band [IO.FileAttributes]::ReparsePoint) {throw 'Reparse target refused'}
$acl=Get-Acl -LiteralPath $dir
$allowed=@($sid.Value,'S-1-5-18')
if (!$acl.AreAccessRulesProtected) {throw 'Credential directory inheritance is not restricted'}
foreach ($rule in $acl.Access) {
    if ($rule.AccessControlType -eq 'Allow' -and $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -notin $allowed) {throw 'Credential directory has unexpected access'}
}
$first=$null;$second=$null;$ptr1=[IntPtr]::Zero;$ptr2=[IntPtr]::Zero;$a=$null;$b=$null;$bytes=$null
try {
    $first=Read-Host 'Enter your token (at least 32 characters, no spaces)' -AsSecureString
    $second=Read-Host 'Enter the same token again' -AsSecureString
    $ptr1=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($first)
    $ptr2=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($second)
    $a=[Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr1)
    $b=[Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr2)
    if ($a.Length -lt 32 -or $a -match '\s' -or $a -cne $b) {throw 'Token validation failed; nothing saved'}
    $bytes=[Text.UTF8Encoding]::new($false).GetBytes($a)
    $stream=[IO.File]::Open($path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try {$stream.Write($bytes,0,$bytes.Length)} finally {$stream.Dispose()}
    Write-Host 'Token saved in the restricted local file. Value was not displayed. Service remains stopped.'
} finally {
    if ($ptr1 -ne [IntPtr]::Zero) {[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr1)}
    if ($ptr2 -ne [IntPtr]::Zero) {[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr2)}
    if ($bytes) {[Array]::Clear($bytes,0,$bytes.Length)}
    if ($first) {$first.Dispose()};if ($second) {$second.Dispose()}
    $a=$null;$b=$null
}
