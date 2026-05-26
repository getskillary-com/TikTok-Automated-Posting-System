param(
    [string]$PythonPath = "D:\Before\AISY\GPT-SoVITS-beta0221\runtime\python.exe"
)

$ErrorActionPreference = "Stop"

$target = (Resolve-Path -LiteralPath $PythonPath).Path.ToLowerInvariant()
$changed = @()
$rules = Get-NetFirewallRule -DisplayName "Python" |
    Where-Object { $_.Direction -eq "Inbound" -and $_.Action -eq "Block" -and $_.Enabled -eq "True" }

foreach ($rule in $rules) {
    $app = $rule | Get-NetFirewallApplicationFilter
    $port = $rule | Get-NetFirewallPortFilter
    $program = [string]$app.Program
    if ($program.ToLowerInvariant() -ne $target) {
        continue
    }
    if ($port.Protocol -ne "TCP") {
        continue
    }
    $changed += [PSCustomObject]@{
        Name = $rule.Name
        DisplayName = $rule.DisplayName
        Program = $program
        Protocol = $port.Protocol
        PreviousProfile = $rule.Profile
    }
    Set-NetFirewallRule -Name $rule.Name -Profile Public | Out-Null
}

$after = @()
foreach ($item in $changed) {
    $rule = Get-NetFirewallRule -Name $item.Name
    $app = $rule | Get-NetFirewallApplicationFilter
    $port = $rule | Get-NetFirewallPortFilter
    $after += [PSCustomObject]@{
        Name = $rule.Name
        DisplayName = $rule.DisplayName
        Program = $app.Program
        Protocol = $port.Protocol
        PreviousProfile = $item.PreviousProfile
        CurrentProfile = $rule.Profile
        Action = $rule.Action
        Enabled = $rule.Enabled
    }
}

[PSCustomObject]@{
    TargetPython = $target
    ChangedCount = $after.Count
    ChangedRules = $after
} | ConvertTo-Json -Depth 4
