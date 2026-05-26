param(
    [string]$ShareRoot = "D:\Automation\group_control_system\shared_drive",
    [string]$ShareName = "GroupControlSharedDrive",
    [string]$SubmitterGroup = "GroupControlSubmitters"
)

$ErrorActionPreference = "Stop"

function Ensure-Directory {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
}

function Grant-ShareAccessSafe {
    param(
        [string]$Name,
        [string]$AccountName,
        [string]$AccessRight
    )
    $existing = Get-SmbShareAccess -Name $Name -ErrorAction Stop |
        Where-Object { $_.AccountName -eq $AccountName }
    if ($existing) {
        return
    }
    Grant-SmbShareAccess -Name $Name -AccountName $AccountName -AccessRight $AccessRight -Force | Out-Null
}

$ShareRoot = (Resolve-Path -LiteralPath $ShareRoot).Path
$LogDir = Join-Path $ShareRoot "_system\logs"
Ensure-Directory $LogDir

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$AclBackup = Join-Path $LogDir "acl-backup-$timestamp.txt"
$ShareBackup = Join-Path $LogDir "smb-share-backup-$timestamp.txt"

icacls $ShareRoot /save $AclBackup /t /c | Out-Null
Get-SmbShare -Name $ShareName -ErrorAction SilentlyContinue |
    Select-Object Name,Path,Description |
    Format-List |
    Out-File -FilePath $ShareBackup -Encoding utf8
Get-SmbShareAccess -Name $ShareName -ErrorAction SilentlyContinue |
    Select-Object Name,AccountName,AccessControlType,AccessRight |
    Format-Table -AutoSize |
    Out-File -FilePath $ShareBackup -Encoding utf8 -Append

$group = Get-LocalGroup -Name $SubmitterGroup -ErrorAction SilentlyContinue
if (-not $group) {
    New-LocalGroup -Name $SubmitterGroup -Description "Group-control submitters" | Out-Null
}

foreach ($relative in @(
    "templates",
    "inbox",
    "processing",
    "accepted",
    "rejected",
    "status",
    "archive",
    "_system\logs"
)) {
    Ensure-Directory (Join-Path $ShareRoot $relative)
}

$existingShare = Get-SmbShare -Name $ShareName -ErrorAction SilentlyContinue
if ($existingShare -and $existingShare.Path -ne $ShareRoot) {
    throw "SMB share '$ShareName' already points to '$($existingShare.Path)', expected '$ShareRoot'. Refusing to overwrite."
}
if (-not $existingShare) {
    New-SmbShare -Name $ShareName -Path $ShareRoot -Description "Group control shared-drive task intake" -ChangeAccess $SubmitterGroup -FullAccess "BUILTIN\Administrators" | Out-Null
} else {
    Grant-ShareAccessSafe -Name $ShareName -AccountName $SubmitterGroup -AccessRight "Change"
    Grant-ShareAccessSafe -Name $ShareName -AccountName "BUILTIN\Administrators" -AccessRight "Full"
}

foreach ($broadAccount in @("Everyone", "BUILTIN\Users", "NT AUTHORITY\Authenticated Users")) {
    Revoke-SmbShareAccess -Name $ShareName -AccountName $broadAccount -Force -ErrorAction SilentlyContinue | Out-Null
}

icacls $ShareRoot /inheritance:d /t /c | Out-Null
icacls $ShareRoot /remove:g "Everyone" "BUILTIN\Users" "NT AUTHORITY\Authenticated Users" /t /c | Out-Null

$groupIdentity = "$env:COMPUTERNAME\$SubmitterGroup"
icacls $ShareRoot /grant "${groupIdentity}:(RX)" /c | Out-Null
icacls (Join-Path $ShareRoot "templates") /grant "${groupIdentity}:(OI)(CI)(RX)" /t /c | Out-Null
icacls (Join-Path $ShareRoot "status") /grant "${groupIdentity}:(OI)(CI)(RX)" /t /c | Out-Null
icacls (Join-Path $ShareRoot "inbox") /grant "${groupIdentity}:(OI)(CI)(M)" /t /c | Out-Null

$share = Get-SmbShare -Name $ShareName
$access = Get-SmbShareAccess -Name $ShareName |
    Select-Object AccountName,AccessControlType,AccessRight

[PSCustomObject]@{
    ShareName = $share.Name
    SharePath = $share.Path
    SubmitterGroup = $SubmitterGroup
    AclBackup = $AclBackup
    ShareBackup = $ShareBackup
    Access = $access
} | ConvertTo-Json -Depth 4
