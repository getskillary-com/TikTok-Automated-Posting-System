param(
    [string]$RuleName = "GroupControl Shared Drive Web 8080",
    [int]$Port = 8080,
    [string[]]$RemoteAddress = @("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)

$ErrorActionPreference = "Stop"

$existing = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
if ($existing) {
    Set-NetFirewallRule -DisplayName $RuleName -Enabled True -Profile Domain,Private -Direction Inbound -Action Allow | Out-Null
    $existing | Get-NetFirewallPortFilter | Set-NetFirewallPortFilter -Protocol TCP -LocalPort $Port | Out-Null
    $existing | Get-NetFirewallAddressFilter | Set-NetFirewallAddressFilter -RemoteAddress $RemoteAddress | Out-Null
} else {
    New-NetFirewallRule `
        -DisplayName $RuleName `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort $Port `
        -Profile Domain,Private `
        -RemoteAddress $RemoteAddress | Out-Null
}

$rule = Get-NetFirewallRule -DisplayName $RuleName
$portFilter = $rule | Get-NetFirewallPortFilter
$addressFilter = $rule | Get-NetFirewallAddressFilter

[PSCustomObject]@{
    DisplayName = $rule.DisplayName
    Enabled = $rule.Enabled
    Profile = $rule.Profile
    Direction = $rule.Direction
    Action = $rule.Action
    Protocol = $portFilter.Protocol
    LocalPort = $portFilter.LocalPort
    RemoteAddress = $addressFilter.RemoteAddress
} | ConvertTo-Json
