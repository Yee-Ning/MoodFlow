[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$runtimeDirectory = Join-Path $PSScriptRoot ".dev"
$statePath = Join-Path $runtimeDirectory "processes.json"

function Test-ProcessIdentity {
    param(
        [Parameter(Mandatory)]
        [psobject]$Entry
    )

    $process = Get-Process -Id ([int]$Entry.pid) -ErrorAction SilentlyContinue
    if (-not $process) {
        return $false
    }

    try {
        $expectedStart = [DateTime]::FromFileTimeUtc([long]$Entry.startedAtFileTimeUtc)
        $actualStart = $process.StartTime.ToUniversalTime()
        $startTimeDifference = [Math]::Abs(($actualStart - $expectedStart).TotalSeconds)
        if ($startTimeDifference -ge 2) {
            Write-Verbose "PID $($Entry.pid) start time differs from the recorded process by $startTimeDifference seconds."
            return $false
        }

        return $true
    }
    catch {
        Write-Verbose "Could not validate PID $($Entry.pid): $($_.Exception.Message)"
        return $false
    }
}

if (-not (Test-Path -LiteralPath $statePath)) {
    Write-Host "No MoodFlow processes started by start-dev.ps1 were found."
    return
}

try {
    $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    $trackedProcesses = @($state.processes)
}
catch {
    throw "Cannot read process state file '$statePath'. Refusing to stop any process."
}

$remainingProcesses = @()
$taskKillPath = Join-Path $env:SystemRoot "System32\taskkill.exe"

[array]::Reverse($trackedProcesses)
foreach ($entry in $trackedProcesses) {
    if (-not (Test-ProcessIdentity -Entry $entry)) {
        Write-Host "[SKIPPED] $($entry.service): recorded process is no longer running"
        continue
    }

    Write-Host "[STOPPING] $($entry.service) (PID $($entry.pid))..."
    & $taskKillPath /PID ([string]$entry.pid) /T /F | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Could not stop $($entry.service)."
        $remainingProcesses += $entry
        continue
    }

    Write-Host "[STOPPED] $($entry.service)" -ForegroundColor Green
}

if ($remainingProcesses.Count -gt 0) {
    $state.processes = $remainingProcesses
    $state.updatedAtUtc = [DateTime]::UtcNow.ToString("o")
    $state | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $statePath -Encoding UTF8
    throw "Some MoodFlow processes could not be stopped. Their entries remain in $statePath."
}

Remove-Item -LiteralPath $statePath -Force
Write-Host "All services started by start-dev.ps1 have been stopped."
