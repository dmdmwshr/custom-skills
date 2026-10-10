#requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Name,
    [Parameter(Mandatory)][string]$Username,
    [switch]$Info
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$credential = $null
$networkCredential = $null
try {
    Import-Module Microsoft.PowerShell.SecretManagement -ErrorAction Stop
    if ($Info) {
        $metadata = Get-SecretInfo -Name $Name -Vault LocalVault -ErrorAction Stop |
            Where-Object { $_.Name -ceq $Name }
        @{credential_present = ($null -ne $metadata -and [string]$metadata.Type -eq 'PSCredential')} |
            ConvertTo-Json -Compress
        exit 0
    }
    # Only browser_cli.py consumes this stdout through a private local pipe.
    # The caller supplies no credential value in arguments, files or parent environment.
    $credential = Get-Secret -Name $Name -Vault LocalVault -ErrorAction Stop
    if ($credential -isnot [System.Management.Automation.PSCredential] -or
        $credential.UserName -cne $Username) {
        throw 'Credential binding mismatch'
    }
    $networkCredential = $credential.GetNetworkCredential()
    if ([string]::IsNullOrWhiteSpace($networkCredential.Password)) {
        throw 'Empty extension credential'
    }
    [Console]::Out.Write($networkCredential.Password)
}
catch {
    [Console]::Error.WriteLine('Local extension credential lookup failed.')
    exit 1
}
finally {
    $networkCredential = $null
    $credential = $null
}
