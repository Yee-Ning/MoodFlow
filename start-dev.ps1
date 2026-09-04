[CmdletBinding()]
param(
    [ValidateRange(5, 300)]
    [int]$StartupTimeoutSeconds = 90,

    [switch]$ValidateOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$runtimeDirectory = Join-Path $projectRoot ".dev"
$logDirectory = Join-Path $runtimeDirectory "logs"
$statePath = Join-Path $runtimeDirectory "processes.json"

function Test-TcpPort {
    param(
        [Parameter(Mandatory)]
        [int]$Port
    )

    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $connectTask = $client.ConnectAsync("127.0.0.1", $Port)
        if (-not $connectTask.Wait(300)) {
            return $false
        }

        return $client.Connected
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

function Resolve-CommandPath {
    param(
        [Parameter(Mandatory)]
        [string]$Name,

        [Parameter(Mandatory)]
        [string]$InstallHint
    )

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $command) {
        throw "Cannot find '$Name'. $InstallHint"
    }

    return $command.Source
}

function Get-JavaMajorVersion {
    param(
        [Parameter(Mandatory)]
        [string]$JavaPath
    )

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $JavaPath
    $startInfo.Arguments = "-version"
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true

    $process = [System.Diagnostics.Process]::Start($startInfo)
    $versionOutput = $process.StandardError.ReadToEnd() + $process.StandardOutput.ReadToEnd()
    $process.WaitForExit()

    if ($process.ExitCode -ne 0 -or $versionOutput -notmatch 'version "(?:1\.)?(?<major>\d+)') {
        return 0
    }

    return [int]$Matches.major
}

function Resolve-JavaHome {
    $candidatePaths = @()

    if ($env:JAVA_HOME) {
        $candidatePaths += Join-Path $env:JAVA_HOME "bin\java.exe"
    }

    $pathJava = Get-Command "java.exe" -ErrorAction SilentlyContinue
    if ($pathJava) {
        $candidatePaths += $pathJava.Source
    }

    $javaInstallRoot = Join-Path $env:ProgramFiles "Java"
    if (Test-Path -LiteralPath $javaInstallRoot) {
        $candidatePaths += Get-ChildItem -LiteralPath $javaInstallRoot -Directory -Filter "jdk-*" |
            ForEach-Object { Join-Path $_.FullName "bin\java.exe" }
    }

    $supportedJdks = foreach ($candidatePath in ($candidatePaths | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $candidatePath)) {
            continue
        }

        $majorVersion = Get-JavaMajorVersion -JavaPath $candidatePath
        if ($majorVersion -ge 21) {
            [pscustomobject]@{
                JavaPath = $candidatePath
                JavaHome = Split-Path (Split-Path $candidatePath -Parent) -Parent
                MajorVersion = $majorVersion
            }
        }
    }

    $selectedJdk = $supportedJdks | Sort-Object MajorVersion -Descending | Select-Object -First 1
    if (-not $selectedJdk) {
        throw "Cannot find JDK 21 or newer. Install JDK 21+ or set JAVA_HOME before starting MoodFlow."
    }

    return $selectedJdk
}

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
        return [Math]::Abs(($actualStart - $expectedStart).TotalSeconds) -lt 2
    }
    catch {
        return $false
    }
}

function Read-TrackedProcesses {
    if (-not (Test-Path -LiteralPath $statePath)) {
        return @()
    }

    try {
        $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        return @($state.processes) | Where-Object { Test-ProcessIdentity -Entry $_ }
    }
    catch {
        Write-Warning "Ignoring an unreadable process state file: $statePath"
        return @()
    }
}

function Save-TrackedProcesses {
    param(
        [Parameter(Mandatory)]
        [AllowEmptyCollection()]
        [array]$Entries
    )

    New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null
    $state = [ordered]@{
        version = 1
        projectRoot = $projectRoot
        updatedAtUtc = [DateTime]::UtcNow.ToString("o")
        processes = @($Entries)
    }
    $state | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $statePath -Encoding UTF8
}

function ConvertTo-PowerShellLiteral {
    param(
        [Parameter(Mandatory)]
        [string]$Value
    )

    return "'" + $Value.Replace("'", "''") + "'"
}

function Start-LoggedProcess {
    param(
        [Parameter(Mandatory)]
        [psobject]$Service
    )

    $safeName = ([string]$Service.Key) -replace '[^a-zA-Z0-9_-]', '-'
    $stdoutPath = Join-Path $logDirectory "$safeName.out.log"
    $stderrPath = Join-Path $logDirectory "$safeName.err.log"
    $hostExecutable = (Get-Process -Id $PID).Path

    $fileLiteral = ConvertTo-PowerShellLiteral -Value ([string]$Service.FilePath)
    $argumentLiterals = @(
        @($Service.Arguments) |
            ForEach-Object { ConvertTo-PowerShellLiteral -Value ([string]$_) }
    )
    $invocation = "& $fileLiteral"
    if ($argumentLiterals.Count -gt 0) {
        $invocation += " " + ($argumentLiterals -join " ")
    }
    $invocation += "; exit `$LASTEXITCODE"

    $encodedCommand = [Convert]::ToBase64String(
        [Text.Encoding]::Unicode.GetBytes($invocation)
    )

    $process = Start-Process `
        -FilePath $hostExecutable `
        -ArgumentList @("-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", $encodedCommand) `
        -WorkingDirectory ([string]$Service.WorkingDirectory) `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -WindowStyle Hidden `
        -PassThru

    return [pscustomobject]@{
        service = $Service.Name
        key = $Service.Key
        port = $Service.Port
        pid = $process.Id
        startedAtUtc = $process.StartTime.ToUniversalTime().ToString("o")
        startedAtFileTimeUtc = $process.StartTime.ToUniversalTime().ToFileTimeUtc()
        stdout = $stdoutPath
        stderr = $stderrPath
    }
}

function Wait-ServicePort {
    param(
        [Parameter(Mandatory)]
        [psobject]$Entry,

        [Parameter(Mandatory)]
        [int]$TimeoutSeconds
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        if (Test-TcpPort -Port ([int]$Entry.port)) {
            return $true
        }

        if (-not (Test-ProcessIdentity -Entry $Entry)) {
            return $false
        }

        Start-Sleep -Milliseconds 500
    }

    return $false
}

function Resolve-ListenerProcessEntry {
    param(
        [Parameter(Mandatory)]
        [psobject]$LauncherEntry
    )

    $listener = Get-NetTCPConnection `
        -State Listen `
        -LocalPort ([int]$LauncherEntry.port) `
        -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $listener) {
        throw "Port $($LauncherEntry.port) became unavailable before its listener could be recorded."
    }

    $listenerProcess = Get-Process -Id ([int]$listener.OwningProcess) -ErrorAction SilentlyContinue
    if (-not $listenerProcess) {
        throw "The listener process for port $($LauncherEntry.port) exited before it could be recorded."
    }

    return [pscustomobject]@{
        service = $LauncherEntry.service
        key = $LauncherEntry.key
        port = $LauncherEntry.port
        pid = $listenerProcess.Id
        startedAtUtc = $listenerProcess.StartTime.ToUniversalTime().ToString("o")
        startedAtFileTimeUtc = $listenerProcess.StartTime.ToUniversalTime().ToFileTimeUtc()
        launcherPid = $LauncherEntry.pid
        stdout = $LauncherEntry.stdout
        stderr = $LauncherEntry.stderr
    }
}

function Show-StartupFailure {
    param(
        [Parameter(Mandatory)]
        [psobject]$Entry
    )

    Write-Host ""
    Write-Host "Recent error output for $($Entry.service):" -ForegroundColor Red
    if (Test-Path -LiteralPath $Entry.stderr) {
        Get-Content -LiteralPath $Entry.stderr -Tail 20
    }
    Write-Host "Logs: $($Entry.stdout) and $($Entry.stderr)"
}

$npmPath = Resolve-CommandPath -Name "npm.cmd" -InstallHint "Install Node.js and npm, then try again."
$mavenWrapper = Join-Path $projectRoot "music-backend\mvnw.cmd"
$mavenPath = if (Test-Path -LiteralPath $mavenWrapper) {
    $mavenWrapper
}
else {
    Resolve-CommandPath -Name "mvn.cmd" -InstallHint "Install Maven or add mvnw.cmd to music-backend."
}

$jdk = Resolve-JavaHome
$env:JAVA_HOME = $jdk.JavaHome
$env:Path = "$(Join-Path $jdk.JavaHome 'bin');$env:Path"

$pythonPath = Join-Path $projectRoot "ai-service\.venv\Scripts\python.exe"
$aiEnvironmentPath = Join-Path $projectRoot "ai-service\.env"
$neteaseModulesPath = Join-Path $projectRoot "netease-music-api\node_modules"
$frontendModulesPath = Join-Path $projectRoot "soul-music-room\node_modules"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Python virtual environment is missing. Create ai-service\.venv and install ai-service\requirements.txt."
}
if (-not (Test-Path -LiteralPath $aiEnvironmentPath)) {
    throw "ai-service\.env is missing. Copy ai-service\.env.example and configure the LLM settings."
}
if (-not (Test-Path -LiteralPath $neteaseModulesPath)) {
    throw "netease-music-api\node_modules is missing. Run 'npm install' in netease-music-api."
}
if (-not (Test-Path -LiteralPath $frontendModulesPath)) {
    throw "soul-music-room\node_modules is missing. Run 'npm install' in soul-music-room."
}

$services = @(
    [pscustomobject]@{
        Name = "NetEase Music API"
        Key = "netease"
        Port = 3000
        WorkingDirectory = Join-Path $projectRoot "netease-music-api"
        FilePath = $npmPath
        Arguments = @("start")
    },
    [pscustomobject]@{
        Name = "Spring Boot backend"
        Key = "spring-boot"
        Port = 8080
        WorkingDirectory = Join-Path $projectRoot "music-backend"
        FilePath = $mavenPath
        Arguments = @("spring-boot:run")
    },
    [pscustomobject]@{
        Name = "Python AI agent"
        Key = "ai-service"
        Port = 8001
        WorkingDirectory = Join-Path $projectRoot "ai-service"
        FilePath = $pythonPath
        Arguments = @("-m", "uvicorn", "app.main:app", "--reload", "--host", "127.0.0.1", "--port", "8001")
    },
    [pscustomobject]@{
        Name = "React frontend"
        Key = "frontend"
        Port = 5173
        WorkingDirectory = Join-Path $projectRoot "soul-music-room"
        FilePath = $npmPath
        Arguments = @("run", "dev", "--", "--host", "127.0.0.1", "--port", "5173", "--strictPort")
    }
)

Write-Host "MoodFlow development environment"
Write-Host "Using JDK $($jdk.MajorVersion): $($jdk.JavaHome)"

if ($ValidateOnly) {
    foreach ($service in $services) {
        $portState = if (Test-TcpPort -Port $service.Port) { "already listening" } else { "available" }
        Write-Host "[OK] $($service.Name): port $($service.Port) is $portState"
    }
    Write-Host "Validation completed. No services were started."
    return
}

New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$trackedProcesses = @(Read-TrackedProcesses)

foreach ($service in $services) {
    if (Test-TcpPort -Port $service.Port) {
        $trackedService = $trackedProcesses | Where-Object { $_.key -eq $service.Key } | Select-Object -First 1
        if ($trackedService) {
            Write-Host "[RUNNING] $($service.Name) on port $($service.Port) (PID $($trackedService.pid))"
        }
        else {
            Write-Host "[SKIPPED] $($service.Name): port $($service.Port) is already used by an external process"
        }
        continue
    }

    Write-Host "[STARTING] $($service.Name) on port $($service.Port)..."
    $launcherEntry = Start-LoggedProcess -Service $service
    $trackedProcesses += $launcherEntry
    Save-TrackedProcesses -Entries $trackedProcesses

    if (-not (Wait-ServicePort -Entry $launcherEntry -TimeoutSeconds $StartupTimeoutSeconds)) {
        Show-StartupFailure -Entry $launcherEntry
        throw "$($service.Name) did not start on port $($service.Port) within $StartupTimeoutSeconds seconds."
    }

    $listenerEntry = Resolve-ListenerProcessEntry -LauncherEntry $launcherEntry
    $trackedProcesses = @(
        $trackedProcesses | Where-Object { $_.key -ne $service.Key }
    )
    $trackedProcesses += $listenerEntry
    Save-TrackedProcesses -Entries $trackedProcesses

    Write-Host "[READY] $($service.Name) on port $($service.Port) (PID $($listenerEntry.pid))" -ForegroundColor Green
}

Save-TrackedProcesses -Entries $trackedProcesses
Write-Host ""
Write-Host "MoodFlow is ready:" -ForegroundColor Green
Write-Host "  Website:  http://localhost:5173"
Write-Host "  AI docs:  http://127.0.0.1:8001/docs"
Write-Host "  AI health: http://127.0.0.1:8001/health"
Write-Host "  Logs:      $logDirectory"
Write-Host ""
Write-Host "Run .\stop-dev.ps1 to stop services started by this script."
