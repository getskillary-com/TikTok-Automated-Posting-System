$ErrorActionPreference = "Stop"

$items = @()
$rules = Get-NetFirewallRule -DisplayName "Python" |
    Where-Object { $_.Direction -eq "Inbound" -and $_.Action -eq "Block" -and $_.Enabled -eq "True" }
foreach ($rule in $rules) {
    $app = $rule | Get-NetFirewallApplicationFilter
    $port = $rule | Get-NetFirewallPortFilter
    $items += [PSCustomObject]@{
        Name = $rule.Name
        DisplayName = $rule.DisplayName
        Profile = $rule.Profile
        Direction = $rule.Direction
        Action = $rule.Action
        Program = $app.Program
        Protocol = $port.Protocol
        LocalPort = $port.LocalPort
    }
}

$items | ConvertTo-Json
