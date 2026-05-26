param(
    [string]$HostAddress = "0.0.0.0",
    [int]$Port = 8080,
    [string]$Timezone = "America/Sao_Paulo",
    [string]$ShareRoot = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = "D:\Before\AISY\GPT-SoVITS-beta0221\runtime\python.exe"
$Launcher = Join-Path $Root "shared_drive_web_launcher.py"
$LogDir = Join-Path $Root "run_log\shared_drive_web"
$PidFile = Join-Path $LogDir "web.pid"

if ([string]::IsNullOrWhiteSpace($ShareRoot)) {
    $ShareRoot = Join-Path $Root "shared_drive"
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
[Environment]::SetEnvironmentVariable("PATH", $null, "Process")
$env:PYTHONIOENCODING = "utf-8"

if (Test-Path -LiteralPath $PidFile) {
    $existingPidText = Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    $existingPid = 0
    if ([int]::TryParse($existingPidText, [ref]$existingPid)) {
        $existing = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
        if ($existing) {
            Write-Host "shared drive web already running pid=$existingPid"
            exit 0
        }
    }
}

$ListeningPid = 0
$PortPattern = "^\s*TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(\d+)\s*$"
foreach ($Line in & "$env:SystemRoot\System32\netstat.exe" -ano -p tcp) {
    if ($Line -match $PortPattern) {
        $ListeningPid = [int]$Matches[1]
        break
    }
}

if ($ListeningPid -gt 0) {
    Write-Host "shared drive web port already listening port=$Port pid=$ListeningPid"
    Set-Content -LiteralPath $PidFile -Value ([string]$ListeningPid) -Encoding ASCII
    exit 0
}

$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$OutLog = Join-Path $LogDir "$Stamp-web.out.log"
$ErrLog = Join-Path $LogDir "$Stamp-web.err.log"

$Arguments = @(
    "-u",
    $Launcher,
    "--host", $HostAddress,
    "--port", [string]$Port,
    "--share-root", $ShareRoot,
    "--timezone", $Timezone
)

$Process = Start-Process `
    -FilePath $Python `
    -ArgumentList $Arguments `
    -WorkingDirectory $Root `
    -WindowStyle Hidden `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog `
    -PassThru

Set-Content -LiteralPath $PidFile -Value ([string]$Process.Id) -Encoding ASCII
Write-Host "shared drive web started pid=$($Process.Id)"
Write-Host "stdout=$OutLog"
Write-Host "stderr=$ErrLog"
