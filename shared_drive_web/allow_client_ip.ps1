param(
    [string]$RuleName = "GroupControl Shared Drive Web 8080",
    [string]$ClientIp
)

$ErrorActionPreference = "Stop"

if (-not $ClientIp) {
    throw "ClientIp is required."
}

$rule = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction Stop
$filter = $rule | Get-NetFirewallAddressFilter
$current = @($filter.RemoteAddress)
$desired = @("LocalSubnet", $ClientIp) | Select-Object -Unique
$rule | Get-NetFirewallAddressFilter | Set-NetFirewallAddressFilter -RemoteAddress $desired

$updated = $rule | Get-NetFirewallAddressFilter
[PSCustomObject]@{
    RuleName = $RuleName
    PreviousRemoteAddress = $current
    RemoteAddress = $updated.RemoteAddress
} | ConvertTo-Json
