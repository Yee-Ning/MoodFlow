[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path $PSScriptRoot -Parent
$startScript = Join-Path $projectRoot "start-dev.ps1"
$stopScript = Join-Path $projectRoot "stop-dev.ps1"
$scripts = @($startScript, $stopScript)

foreach ($script in $scripts) {
    $tokens = $null
    $parseErrors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile(
        $script,
        [ref]$tokens,
        [ref]$parseErrors
    )

    if ($parseErrors.Count -gt 0) {
        $messages = $parseErrors | ForEach-Object { $_.Message }
        throw "PowerShell syntax errors in $script`n$($messages -join [Environment]::NewLine)"
    }
}

$startContent = Get-Content -LiteralPath $startScript -Raw
foreach ($requiredText in @(
    "NetEase Music API",
    "Spring Boot backend",
    "Python AI agent",
    "React frontend",
    "3000",
    "8080",
    "8001",
    "5173",
    "processes.json",
    "Resolve-ListenerProcessEntry",
    "Get-NetTCPConnection"
)) {
    if (-not $startContent.Contains($requiredText)) {
        throw "start-dev.ps1 is missing required configuration: $requiredText"
    }
}

$stopContent = Get-Content -LiteralPath $stopScript -Raw
foreach ($requiredSafetyText in @("Test-ProcessIdentity", "startedAtFileTimeUtc", "/PID", "/T")) {
    if (-not $stopContent.Contains($requiredSafetyText)) {
        throw "stop-dev.ps1 is missing process-safety behavior: $requiredSafetyText"
    }
}

& $startScript -ValidateOnly

Write-Host "Development script tests passed." -ForegroundColor Green
