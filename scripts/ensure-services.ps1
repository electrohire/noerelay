# NoeRelay Service Orchestration — Ensure All Services on All Machines
# =============================================================================
# This script probes, starts, and health-checks every service in the NoeRelay
# deployment topology across localhost, Docker, and the remote GPU.
#
# Usage:
#   .\scripts\ensure-services.ps1                  # Check + start all services
#   .\scripts\ensure-services.ps1 -WhatIf          # Dry-run (show what would be done)
#   .\scripts\ensure-services.ps1 -SkipRemote      # Skip remote machine services
#   .\scripts\ensure-services.ps1 -SkipDocker      # Skip Docker services
#   .\scripts\ensure-services.ps1 -Json            # Output results as JSON
#   .\scripts\ensure-services.ps1 -Continuous      # Run continuously, re-checking
# =============================================================================

param(
    [switch]$WhatIf,
    [switch]$SkipRemote,
    [switch]$SkipDocker,
    [switch]$Json,
    [switch]$Continuous,
    [int]$ContinuousIntervalSeconds = 60,
    [int]$TimeoutSeconds = 10,
    [string]$DockerComposeDir = $PSScriptRoot + "\..",
    [string]$RemoteGpuHost = $env:REMOTE_GPU_HOST
)

$ErrorActionPreference = "Continue"
$script:StartTime = Get-Date
if ([string]::IsNullOrWhiteSpace($RemoteGpuHost)) {
    $RemoteGpuHost = "remote-gpu.example.internal"
}

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

function Write-Status { param([string]$Icon, [string]$Service, [string]$Status, [string]$Color = "White")
    $timestamp = (Get-Date).ToString("HH:mm:ss")
    Write-Host "[$timestamp] $Icon $Service : $Status" -ForegroundColor $Color
}

function Write-Ok   { param([string]$S, [string]$M) Write-Status "[OK]" $S $M "Green" }
function Write-Fail { param([string]$S, [string]$M) Write-Status "[FAIL]" $S $M "Red" }
function Write-Warn { param([string]$S, [string]$M) Write-Status "[WARN]" $S $M "Yellow" }
function Write-Info { param([string]$S, [string]$M) Write-Status "[..]" $S $M "Cyan" }
function Write-Act  { param([string]$S, [string]$M) Write-Status "[>>]" $S $M "Magenta" }

# ---------------------------------------------------------------------------
# Probe functions
# ---------------------------------------------------------------------------

function Test-TcpPort {
    param([string]$HostName, [int]$Port, [int]$TimeoutMs = 3000)
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $result = $client.BeginConnect($HostName, $Port, $null, $null)
        $success = $result.AsyncWaitHandle.WaitOne($TimeoutMs)
        if ($success) {
            $client.EndConnect($result)
            $client.Close()
            return $true
        }
        $client.Close()
        return $false
    } catch {
        return $false
    }
}

function Test-HttpEndpoint {
    param([string]$Url, [int]$TimeoutSec = 5)
    try {
        $resp = Invoke-WebRequest -Uri $Url -Method Get -TimeoutSec $TimeoutSec -SkipHttpErrorCheck -UseBasicParsing
        return @{ Reachable = $true; StatusCode = $resp.StatusCode; Content = $resp.Content }
    } catch {
        return @{ Reachable = $false; StatusCode = 0; Content = $_.Exception.Message }
    }
}

function Get-OllamaModels {
    try {
        $resp = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -Method Get -TimeoutSec 5
        return $resp.models | ForEach-Object { $_.name }
    } catch {
        return @()
    }
}

function Get-LiteLLMModels {
    try {
        $resp = Invoke-RestMethod -Uri "http://127.0.0.1:4000/v1/models" -Method Get -TimeoutSec 5
        return $resp.data | ForEach-Object { $_.id }
    } catch {
        return @()
    }
}

function Test-DockerContainer {
    param([string]$ContainerName)
    try {
        $status = docker inspect -f '{{.State.Status}}' $ContainerName 2>$null
        return ($status -eq "running")
    } catch {
        return $false
    }
}

function Test-SshTunnel {
    param([int]$LocalPort)
    try {
        $sshProc = Get-Process -Name "ssh" -ErrorAction SilentlyContinue
        if (-not $sshProc) { return $false }
        # Check if any SSH process has our port forwarded
        $netstat = netstat -ano 2>$null | Select-String ":$LocalPort.*LISTENING"
        return ($null -ne $netstat)
    } catch {
        return $false
    }
}

function Test-RemoteHost {
    param([string]$HostName, [int]$TimeoutMs = 2000)
    try {
        $ping = New-Object System.Net.NetworkInformation.Ping
        $reply = $ping.Send($HostName, $TimeoutMs)
        return ($reply.Status -eq "Success")
    } catch {
        return $false
    }
}

# ---------------------------------------------------------------------------
# Service definitions
# ---------------------------------------------------------------------------

$Services = @(
    # --- Localhost ---
    @{
        Name = "Ollama"
        Machine = "localhost"
        Kind = "inference"
        Host = "127.0.0.1"
        Port = 11434
        HealthUrl = "http://127.0.0.1:11434/"
        IsWindowsService = $true
        ServiceName = "Ollama"
        StartCommand = { Start-Process "ollama" -ArgumentList "serve" -WindowStyle Hidden }
        VerifyCommand = { Test-TcpPort "127.0.0.1" 11434 }
        ModelsCommand = { Get-OllamaModels }
        ExpectedModels = @("qwen3:8b", "qwen3-coder:30b", "Muse-Glimmer-30B", "llama3.2:3b")
    },
    @{
        Name = "LiteLLM Proxy"
        Machine = "localhost"
        Kind = "proxy"
        Host = "127.0.0.1"
        Port = 4000
        HealthUrl = "http://127.0.0.1:4000/health"
        IsWindowsService = $false
        ServiceName = $null
        StartCommand = {
            $env:PYTHONIOENCODING = "utf-8"
            Start-Process powershell -ArgumentList "-NoExit", "-Command",
                "set PYTHONIOENCODING=utf-8; litellm --port 4000 --host 127.0.0.1 --model ollama/qwen3:8b --model ollama/qwen3-coder:30b" `
                -WindowStyle Minimized
        }
        VerifyCommand = { Test-TcpPort "127.0.0.1" 4000 }
        ModelsCommand = { Get-LiteLLMModels }
        ExpectedModels = @("ollama/qwen3:8b", "ollama/qwen3-coder:30b")
    },
    @{
        Name = "NoeRelay Gateway (native)"
        Machine = "localhost"
        Kind = "gateway"
        Host = "127.0.0.1"
        Port = 8080
        HealthUrl = "http://127.0.0.1:8080/health"
        IsWindowsService = $false
        ServiceName = $null
        StartCommand = $null  # Started via Docker or cargo
        VerifyCommand = { Test-TcpPort "127.0.0.1" 8080 }
        ModelsCommand = $null
        ExpectedModels = @()
    },
    @{
        Name = "PostgreSQL"
        Machine = "localhost"
        Kind = "database"
        Host = "127.0.0.1"
        Port = 5432
        HealthUrl = $null
        IsWindowsService = $false
        ServiceName = $null
        StartCommand = $null  # Started via Docker
        VerifyCommand = { Test-TcpPort "127.0.0.1" 5432 }
        ModelsCommand = $null
        ExpectedModels = @()
    },

    # --- Docker ---
    @{
        Name = "Docker: noerelay-gateway"
        Machine = "docker"
        Kind = "docker"
        Host = "127.0.0.1"
        Port = 8080
        HealthUrl = "http://127.0.0.1:8080/health"
        ContainerName = "noerelay-noerelay-1"
        ComposeService = "noerelay"
        IsWindowsService = $false
        StartCommand = {
            Push-Location $using:DockerComposeDir
            docker compose up -d noerelay postgres
            Pop-Location
        }
        VerifyCommand = { Test-DockerContainer "noerelay-noerelay-1" }
    },
    @{
        Name = "Docker: postgres"
        Machine = "docker"
        Kind = "docker"
        Host = "127.0.0.1"
        Port = 5432
        HealthUrl = $null
        ContainerName = "noerelay-postgres-1"
        ComposeService = "postgres"
        IsWindowsService = $false
        StartCommand = {
            Push-Location $using:DockerComposeDir
            docker compose up -d postgres
            Pop-Location
        }
        VerifyCommand = { Test-DockerContainer "noerelay-postgres-1" }
    },

    # --- Remote GPU ---
    @{
        Name = "Remote GPU"
        Machine = "remote-gpu"
        Kind = "remote"
        Host = $RemoteGpuHost
        Port = 22
        HealthUrl = $null
        IsWindowsService = $false
        StartCommand = $null
        VerifyCommand = { Test-RemoteHost $RemoteGpuHost }
    },
    @{
        Name = "Remote GPU SSH Tunnel"
        Machine = "remote-gpu"
        Kind = "remote"
        Host = "127.0.0.1"
        Port = 4000
        HealthUrl = "http://127.0.0.1:4000/health"
        IsWindowsService = $false
        StartCommand = {
            ssh -f -N -L 4000:127.0.0.1:4000 "actor@$RemoteGpuHost"
        }
        VerifyCommand = { Test-SshTunnel 4000 }
    },
    @{
        Name = "Remote GPU inference endpoint"
        Machine = "remote-gpu"
        Kind = "inference"
        Host = "127.0.0.1"
        Port = 4000
        HealthUrl = "http://127.0.0.1:4000/health"
        IsWindowsService = $false
        StartCommand = $null  # Started on remote machine
        VerifyCommand = { Test-TcpPort "127.0.0.1" 4000 }
    },
    @{
        Name = "Remote GPU network endpoint"
        Machine = "remote-gpu"
        Kind = "remote"
        Host = $RemoteGpuHost
        Port = 4000
        HealthUrl = "https://${RemoteGpuHost}:4000/health"
        IsWindowsService = $false
        StartCommand = $null
        VerifyCommand = { Test-RemoteHost $RemoteGpuHost }
    }
)

# ---------------------------------------------------------------------------
# Service actions
# ---------------------------------------------------------------------------

function Start-WindowsService {
    param($ServiceName)
    try {
        $svc = Get-Service -Name $ServiceName -ErrorAction Stop
        if ($svc.Status -ne "Running") {
            Write-Act $ServiceName "Starting Windows service..."
            if (-not $WhatIf) {
                Start-Service -Name $ServiceName
                Start-Sleep -Seconds 3
            }
        }
        $svc.Refresh()
        return ($svc.Status -eq "Running")
    } catch {
        Write-Warn $ServiceName "Windows service not found: $_"
        return $false
    }
}

function Start-DockerComposeService {
    param($ComposeService)
    Write-Act $ComposeService "Starting via docker compose..."
    if (-not $WhatIf) {
        Push-Location $DockerComposeDir
        docker compose up -d $ComposeService 2>&1 | Out-Null
        Pop-Location
        Start-Sleep -Seconds 5
    }
}

function Start-CustomCommand {
    param($ServiceName, $Command)
    Write-Act $ServiceName "Starting via custom command..."
    if (-not $WhatIf) {
        try {
            & $Command
            Start-Sleep -Seconds 3
        } catch {
            Write-Fail $ServiceName "Start command failed: $_"
        }
    }
}

# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

function Ensure-AllServices {
    param([bool]$IncludeRemote = $true, [bool]$IncludeDocker = $true)

    Write-Host ""
    Write-Host "=" * 70
    Write-Host "  NoeRelay Service Orchestration"
    Write-Host "  Started: $($script:StartTime.ToString('yyyy-MM-dd HH:mm:ss'))"
    Write-Host "  WhatIf: $WhatIf"
    Write-Host "=" * 70
    Write-Host ""

    $results = @()
    $allHealthy = $true

    foreach ($svc in $Services) {
        # Skip filters
        if (-not $IncludeRemote -and $svc.Machine -eq "remote-gpu") { continue }
        if (-not $IncludeDocker -and $svc.Kind -eq "docker") { continue }

        $name = $svc.Name
        $machine = $svc.Machine
        $kind = $svc.Kind

        Write-Host "--- $name ($machine) ---"

        # --- Probe ---
        $healthy = $false
        $verifyCmd = $svc.VerifyCommand
        if ($verifyCmd) {
            $healthy = & $verifyCmd
        }

        # HTTP health check for extra detail
        $healthDetail = ""
        if ($svc.HealthUrl) {
            $httpResult = Test-HttpEndpoint $svc.HealthUrl
            if ($httpResult.Reachable) {
                $healthDetail = "HTTP $($httpResult.StatusCode)"
                $healthy = $true
            }
        }

        if ($healthy) {
            Write-Ok $name "Healthy ($healthDetail)"
        } else {
            Write-Fail $name "Not reachable"
            $allHealthy = $false

            # --- Attempt recovery ---
            if ($svc.IsWindowsService -and $svc.ServiceName) {
                $started = Start-WindowsService $svc.ServiceName
                if ($started) { Write-Ok $name "Windows service started" }
            }
            elseif ($svc.ComposeService) {
                Start-DockerComposeService $svc.ComposeService
                # Re-verify
                Start-Sleep -Seconds 3
                if (& $svc.VerifyCommand) {
                    Write-Ok $name "Docker service started"
                    $healthy = $true
                }
            }
            elseif ($svc.StartCommand) {
                Start-CustomCommand $name $svc.StartCommand
                if (& $svc.VerifyCommand) {
                    Write-Ok $name "Started successfully"
                    $healthy = $true
                }
            }
            else {
                Write-Warn $name "No automated start procedure — manual intervention required"
            }
        }

        # --- Model verification ---
        $models = @()
        if ($healthy -and $svc.ModelsCommand) {
            try {
                $models = & $svc.ModelsCommand
                Write-Info $name "Models: $($models.Count) loaded"
                if ($svc.ExpectedModels) {
                    foreach ($expected in $svc.ExpectedModels) {
                        $found = $models | Where-Object { $_ -like "*$expected*" }
                        if ($found) {
                            Write-Ok $name "Expected model found: $expected"
                        } else {
                            Write-Warn $name "Expected model MISSING: $expected"
                        }
                    }
                }
            } catch {
                Write-Warn $name "Could not fetch model list"
            }
        }

        $results += @{
            Name = $name
            Machine = $machine
            Kind = $kind
            Healthy = $healthy
            Detail = $healthDetail
            Models = $models -join ", "
        }

        Write-Host ""
    }

    # --- Summary ---
    $healthyCount = ($results | Where-Object { $_.Healthy }).Count
    $totalCount = $results.Count

    Write-Host "=" * 70
    Write-Host "  SUMMARY: $healthyCount / $totalCount services healthy"
    Write-Host "  Duration: $(((Get-Date) - $script:StartTime).TotalSeconds.ToString('F1'))s"
    if (-not $allHealthy) {
        Write-Host "  WARNING: Not all services are healthy!" -ForegroundColor Red
    } else {
        Write-Host "  All services healthy." -ForegroundColor Green
    }
    Write-Host "=" * 70
    Write-Host ""

    return @{
        Timestamp = (Get-Date).ToString("o")
        AllHealthy = ($healthyCount -eq $totalCount)
        HealthyCount = $healthyCount
        TotalCount = $totalCount
        Services = $results
    }
}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if ($Continuous) {
    Write-Host "Continuous service orchestration mode (interval: ${ContinuousIntervalSeconds}s)"
    Write-Host "Press Ctrl+C to stop."
    $iteration = 0
    while ($true) {
        $iteration++
        Write-Host "`n=== Iteration $iteration at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ==="
        $result = Ensure-AllServices -IncludeRemote:(-not $SkipRemote) -IncludeDocker:(-not $SkipDocker)
        if ($Json) {
            $result | ConvertTo-Json -Depth 4
        }
        Write-Host "Sleeping ${ContinuousIntervalSeconds}s..."
        Start-Sleep -Seconds $ContinuousIntervalSeconds
    }
} else {
    $result = Ensure-AllServices -IncludeRemote:(-not $SkipRemote) -IncludeDocker:(-not $SkipDocker)
    if ($Json) {
        $result | ConvertTo-Json -Depth 4
    }
    # Exit code: 0 if all healthy, 1 otherwise
    if (-not $result.AllHealthy) {
        exit 1
    }
    exit 0
}
