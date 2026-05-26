$ErrorActionPreference = "Stop"

$blockMatches = @()
$rules = Get-NetFirewallRule -Direction Inbound -Action Block -Enabled True
foreach ($rule in $rules) {
    $port = $rule | Get-NetFirewallPortFilter
    if ($port.LocalPort -eq "8080" -or $port.LocalPort -eq "Any") {
        $blockMatches += [PSCustomObject]@{
            DisplayName = $rule.DisplayName
            Profile = $rule.Profile
            Protocol = $port.Protocol
            LocalPort = $port.LocalPort
        }
    }
}

$profile = Get-NetFirewallProfile | Select-Object Name,Enabled,DefaultInboundAction,AllowInboundRules,NotifyOnListen
$rule = Get-NetFirewallRule -DisplayName "GroupControl Shared Drive Web 8080"
$rulePort = $rule | Get-NetFirewallPortFilter
$ruleAddress = $rule | Get-NetFirewallAddressFilter

[PSCustomObject]@{
    Profiles = $profile
    WebRule = [PSCustomObject]@{
        DisplayName = $rule.DisplayName
        Enabled = $rule.Enabled
        Profile = $rule.Profile
        Direction = $rule.Direction
        Action = $rule.Action
        Protocol = $rulePort.Protocol
        LocalPort = $rulePort.LocalPort
        RemoteAddress = $ruleAddress.RemoteAddress
    }
    BlockingMatches = $blockMatches
} | ConvertTo-Json -Depth 5
