param(
    [string]$AccountType = "all",
    [string]$Timezone = "America/Sao_Paulo",
    [string]$OverdueAction = "reschedule",
    [int]$OverdueRescheduleMinutes = 30,
    [int]$IntervalSeconds = 60,
    [switch]$NoAllowPublish,
    [switch]$StopBeforeFinalPublish
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = "D:\Before\AISY\GPT-SoVITS-beta0221\runtime\python.exe"
$Launcher = Join-Path $Root "execution_queue_launcher.py"
$LogDir = Join-Path $Root "run_log\supervisor"
$PidFile = Join-Path $LogDir "supervisor.pid"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
[Environment]::SetEnvironmentVariable("PATH", $null, "Process")
$env:PYTHONIOENCODING = "utf-8"

if (Test-Path -LiteralPath $PidFile) {
    $existingPidText = Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    $existingPid = 0
    if ([int]::TryParse($existingPidText, [ref]$existingPid)) {
        $existing = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
        if ($existing) {
            Write-Host "execution supervisor already running pid=$existingPid"
            exit 0
        }
    }
}

$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$OutLog = Join-Path $LogDir "$Stamp-supervisor.out.log"
$ErrLog = Join-Path $LogDir "$Stamp-supervisor.err.log"

function Quote-ProcessArgument {
    param([string]$Value)
    if ($null -eq $Value) {
        return '""'
    }
    if ($Value -notmatch '[\s"]') {
        return $Value
    }
    $Escaped = $Value -replace '"', '\"'
    return '"' + $Escaped + '"'
}

$Arguments = @(
    "-u",
    $Launcher,
    "supervisor",
    "--account-type", $AccountType,
    "--timezone", $Timezone,
    "--interval-seconds", [string]$IntervalSeconds,
    "--overdue-action", $OverdueAction,
    "--overdue-reschedule-minutes", [string]$OverdueRescheduleMinutes
)

if ($StopBeforeFinalPublish -and -not $NoAllowPublish) {
    Write-Host "StopBeforeFinalPublish requires NoAllowPublish to avoid mixed publish modes."
    exit 1
}

if (-not $NoAllowPublish) {
    $Arguments += "--allow-publish"
}

if ($StopBeforeFinalPublish) {
    $Arguments += "--stop-before-final-publish"
}

$ArgumentLine = ($Arguments | ForEach-Object { Quote-ProcessArgument ([string]$_) }) -join " "

$Process = Start-Process `
    -FilePath $Python `
    -ArgumentList $ArgumentLine `
    -WorkingDirectory $Root `
    -WindowStyle Hidden `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog `
    -PassThru

Set-Content -LiteralPath $PidFile -Value ([string]$Process.Id) -Encoding ASCII
Write-Host "execution supervisor started pid=$($Process.Id)"
Write-Host "stdout=$OutLog"
Write-Host "stderr=$ErrLog"
