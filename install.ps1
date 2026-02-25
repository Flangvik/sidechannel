#Requires -Version 5.1
<#
.SYNOPSIS
  sidechannel installer - Signal + Claude AI Bot (Windows)

.DESCRIPTION
  Sets up Python venv, config, Signal bridge (Docker), and optional scheduled task.
  Usage: .\install.ps1 [-SkipSignal] [-SkipService] [-Uninstall] [-Restart] [-Help]
#>

param(
    [switch]$SkipSignal,
    [switch]$SkipService,
    [switch]$Uninstall,
    [switch]$Restart,
    [switch]$Help
)

$ErrorActionPreference = "Stop"
$Script:VERSION = "1.5.0"

# -----------------------------------------------------------------------------
# Paths (default to script directory)
# -----------------------------------------------------------------------------
$SCRIPT_DIR = if ($PSCommandPath) { Split-Path -Parent $PSCommandPath } else { Get-Location }
$INSTALL_DIR = if ($env:SIDECHANNEL_DIR) { $env:SIDECHANNEL_DIR } else { $SCRIPT_DIR }
$VENV_DIR = Join-Path $INSTALL_DIR "venv"
$CONFIG_DIR = Join-Path $INSTALL_DIR "config"
$DATA_DIR = Join-Path $INSTALL_DIR "data"
$LOGS_DIR = Join-Path $INSTALL_DIR "logs"
$SIGNAL_DATA_DIR = Join-Path $INSTALL_DIR "signal-data"
$TASK_NAME = "sidechannel"

# -----------------------------------------------------------------------------
# Helpers: colored output
# -----------------------------------------------------------------------------
function Write-Step { param([string]$Message) Write-Host $Message -ForegroundColor Blue }
function Write-Success { param([string]$Message) Write-Host "  $([char]0x2713) $Message" -ForegroundColor Green }
function Write-Warn { param([string]$Message) Write-Host "  ! $Message" -ForegroundColor Yellow }
function Write-Err { param([string]$Message) Write-Host $Message -ForegroundColor Red }

# -----------------------------------------------------------------------------
# Test if a command exists (executable in PATH or as path)
# -----------------------------------------------------------------------------
function Test-CommandExists {
    param([string]$Name, [string]$Path = $null)
    if ($Path -and (Test-Path -LiteralPath $Path -PathType Leaf)) { return $true }
    $exe = Get-Command $Name -ErrorAction SilentlyContinue
    return ($null -ne $exe)
}

# -----------------------------------------------------------------------------
# Wait for Signal bridge QR code endpoint to return image (PNG)
# -----------------------------------------------------------------------------
function Wait-ForQRCode {
    param([int]$MaxWaitSeconds = 90)
    $qrUrl = "http://127.0.0.1:8080/v1/qrcodelink?device_name=sidechannel"
    $elapsed = 0
    Write-Host "  Waiting for Signal bridge to initialize" -NoNewline
    while ($elapsed -lt $MaxWaitSeconds) {
        try {
            $containers = docker ps --format "{{.Names}}" 2>$null
            if ($containers -notmatch "signal-api") { Write-Host ""; return $false }
            $resp = Invoke-WebRequest -Uri $qrUrl -Method Get -UseBasicParsing -TimeoutSec 5 -ErrorAction SilentlyContinue
            $ctype = $resp.Headers["Content-Type"]
            if ($ctype -and $ctype -match "image") {
                Write-Host ""
                return $true
            }
        } catch { }
        Start-Sleep -Seconds 3
        $elapsed += 3
        Write-Host "." -NoNewline
    }
    Write-Host ""
    return $false
}

# -----------------------------------------------------------------------------
# Show help and exit
# -----------------------------------------------------------------------------
if ($Help) {
    Write-Host "Usage: .\install.ps1 [options]"
    Write-Host ""
    Write-Host "Options:"
    Write-Host "  -SkipSignal   Skip Signal pairing (configure later)"
    Write-Host "  -SkipService  Skip scheduled task installation"
    Write-Host "  -Uninstall    Remove sidechannel task and containers"
    Write-Host "  -Restart      Restart the sidechannel scheduled task"
    Write-Host "  -Help         Show this help message"
    exit 0
}

# =============================================================================
# UNINSTALL MODE
# =============================================================================
if ($Uninstall) {
    Write-Host ""
    Write-Host "sidechannel uninstaller" -ForegroundColor Cyan
    Write-Host ""
    $removed = $false

    # Scheduled Task
    $task = Get-ScheduledTask -TaskName $TASK_NAME -ErrorAction SilentlyContinue
    if ($task) {
        Write-Step "Removing scheduled task..."
        Unregister-ScheduledTask -TaskName $TASK_NAME -Confirm:$false
        Write-Success "Task removed"
        $removed = $true
    }

    # Docker containers
    if (Test-CommandExists "docker") {
        foreach ($name in @("signal-api", "sidechannel")) {
            $exists = docker ps -a --format "{{.Names}}" 2>$null | Select-String -Pattern "^\Q$name\E$" -Quiet
            if ($exists) {
                Write-Step "Stopping Docker container: $name..."
                docker stop $name 2>$null
                docker rm $name 2>$null
                Write-Success "Container $name removed"
                $removed = $true
            }
        }
    }

    # Optional: remove install directory
    if (Test-Path $INSTALL_DIR) {
        Write-Host ""
        Write-Warn "The install directory contains your configuration and data:"
        Write-Host "  $INSTALL_DIR" -ForegroundColor Cyan
        Write-Host "  (settings.yaml, .env, Signal data, plugins)"
        Write-Host ""
        $ans = Read-Host "Remove install directory? [y/N]"
        if ($ans -match "^[Yy]$") {
            Remove-Item -Path $INSTALL_DIR -Recurse -Force
            Write-Success "Removed $INSTALL_DIR"
            $removed = $true
        } else {
            Write-Host "  Kept $INSTALL_DIR"
        }
    }

    if ($removed) {
        Write-Host ""
        Write-Host "sidechannel has been uninstalled." -ForegroundColor Green
    } else {
        Write-Warn "Nothing to uninstall. No task, containers, or install directory found."
        Write-Host "  Expected install dir: $INSTALL_DIR"
        Write-Host "  Set SIDECHANNEL_DIR if installed elsewhere."
    }
    Write-Host ""
    exit 0
}

# =============================================================================
# RESTART MODE
# =============================================================================
if ($Restart) {
    Write-Host ""
    Write-Host "Restarting sidechannel..." -ForegroundColor Cyan
    Write-Host ""
    $task = Get-ScheduledTask -TaskName $TASK_NAME -ErrorAction SilentlyContinue
    if ($task) {
        Stop-ScheduledTask -TaskName $TASK_NAME -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
        Start-ScheduledTask -TaskName $TASK_NAME
        Start-Sleep -Seconds 2
        $info = Get-ScheduledTask -TaskName $TASK_NAME
        if ($info.State -eq "Running") {
            Write-Success "sidechannel restarted (scheduled task)"
        } else {
            Write-Warn "Restart issued but task not running yet. Check Task Scheduler."
        }
    } else {
        Write-Warn "No scheduled task found. Start manually:"
        Write-Host "  $INSTALL_DIR\run.ps1" -ForegroundColor Cyan
    }
    Write-Host ""
    exit 0
}

# =============================================================================
# BANNER
# =============================================================================
Write-Host ""
Write-Host @"
     _     _           _                            _
 ___(_) __| | ___  ___| |__   __ _ _ __  _ __   ___| |
/ __| |/ _` |/ _ \/ __| '_ \ / _` | '_ \| '_ \ / _ \ |
\__ \ | (_| |  __/ (__| | | | (_| | | | | | | |  __/ |
|___/_|\__,_|\___|\___|_| |_|\__,_|_| |_|_| |_|\___|_|

"@ -ForegroundColor Cyan
Write-Host "  Signal + Claude AI Bot - v$Script:VERSION" -ForegroundColor Green
Write-Host "  By hackingdave - https://github.com/hackingdave/sidechannel" -ForegroundColor Cyan
Write-Host ""

# -----------------------------------------------------------------------------
# Prerequisite checks
# -----------------------------------------------------------------------------
Write-Step "Checking prerequisites..."

# Python 3.9+
$pythonExe = $null
foreach ($py in @("python", "python3")) {
    $c = Get-Command $py -ErrorAction SilentlyContinue
    if ($c) { $pythonExe = $c.Source; break }
}
if (-not $pythonExe) {
    Write-Err "Error: Python 3 not found. Install Python 3.9+ and ensure it is in PATH."
    exit 1
}
try {
    $pyVer = & $pythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
    $parts = $pyVer -split '\.'
    $major = [int]$parts[0]; $minor = [int]$parts[1]
    if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 9)) {
        Write-Err "Error: Python 3.9+ required (found $pyVer)"
        exit 1
    }
    Write-Success "Python $pyVer"
} catch {
    Write-Err "Error: Could not get Python version."
    exit 1
}

# Claude CLI
$claudePath = $null
if (Test-CommandExists "claude") { $claudePath = "PATH" }
if (-not $claudePath -and $env:LOCALAPPDATA) {
    $localClaude = Join-Path $env:LOCALAPPDATA "Programs\claude\claude.exe"
    if (Test-Path $localClaude) { $claudePath = $localClaude }
}
if ($claudePath) {
    Write-Success "Claude CLI"
} else {
    Write-Warn "Claude CLI not found"
    Write-Host "    sidechannel needs Claude CLI for /ask, /do, /complex."
    Write-Host "    Install: https://docs.anthropic.com/en/docs/claude-code"
    $ans = Read-Host "    Continue anyway? [y/N]"
    if ($ans -notmatch "^[Yy]$") { exit 1 }
}

# curl / Invoke-WebRequest (built-in on Windows)
Write-Success "curl / Invoke-WebRequest"

# Docker
$DockerOk = $false
if ($SkipSignal) {
    $DockerOk = $true
} elseif (Test-CommandExists "docker") {
    try {
        docker info 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Success "Docker"
            $DockerOk = $true
        }
    } catch { }
    if (-not $DockerOk) {
        Write-Warn "Docker installed but not running"
        Write-Host "    Start Docker Desktop, then re-run the installer or choose to skip Signal."
        Write-Host "    1) Wait - I'll start Docker now"
        Write-Host "    2) Skip Signal setup for now"
        $choice = Read-Host "    >"
        if ($choice -eq "2") {
            $SkipSignal = $true
            $DockerOk = $true
        } else {
            Read-Host "    Press Enter when Docker is running"
            $tries = 0
            while ($tries -lt 30) {
                try { docker info 2>$null | Out-Null; if ($LASTEXITCODE -eq 0) { break } } catch { }
                Start-Sleep -Seconds 2
                $tries++
            }
            try { docker info 2>$null | Out-Null; if ($LASTEXITCODE -eq 0) { Write-Success "Docker is running"; $DockerOk = $true } } catch { }
            if (-not $DockerOk) { Write-Warn "Docker still not ready. Skipping Signal setup."; $SkipSignal = $true; $DockerOk = $true }
        }
    }
} else {
    Write-Warn "Docker not found"
    Write-Host "    sidechannel needs one Docker container for the Signal bridge."
    Write-Host "    Install Docker Desktop: https://docs.docker.com/desktop/install/windows-install/"
    $ans = Read-Host "    Skip Signal setup and continue? [y/N]"
    if ($ans -match "^[Yy]$") { $SkipSignal = $true; $DockerOk = $true } else { Write-Host "  Install Docker, then re-run this installer."; exit 1 }
}

Write-Host ""

# -----------------------------------------------------------------------------
# Directories and config templates
# -----------------------------------------------------------------------------
Write-Step "Setting up directories..."

$sidechannelPkg = Join-Path $INSTALL_DIR "sidechannel"
if (-not (Test-Path $sidechannelPkg -PathType Container)) {
    Write-Err "Error: sidechannel package not found in $INSTALL_DIR. Run this script from the repo directory."
    exit 1
}

foreach ($dir in @($CONFIG_DIR, $DATA_DIR, $LOGS_DIR, $SIGNAL_DATA_DIR)) {
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
}

$configDirSrc = Join-Path $INSTALL_DIR "config"
if (Test-Path $configDirSrc) {
    Get-ChildItem -Path $configDirSrc -Filter "*.example" -File | ForEach-Object {
        $dest = Join-Path $CONFIG_DIR $_.Name.Replace(".example", "")
        if (-not (Test-Path $dest)) { Copy-Item $_.FullName $dest }
    }
    $claudeMd = Join-Path $configDirSrc "CLAUDE.md"
    if (Test-Path $claudeMd) {
        $destMd = Join-Path $CONFIG_DIR "CLAUDE.md"
        if (-not (Test-Path $destMd)) { Copy-Item $claudeMd $destMd }
    }
}

Write-Success "Ready ($INSTALL_DIR)"
Write-Host ""

# -----------------------------------------------------------------------------
# Python venv and dependencies
# -----------------------------------------------------------------------------
Write-Step "Setting up Python environment..."

if (-not (Test-Path $VENV_DIR)) {
    & $pythonExe -m venv $VENV_DIR
    Write-Success "Virtual environment created"
}

$venvPython = Join-Path $VENV_DIR "Scripts\python.exe"
$venvPip = Join-Path $VENV_DIR "Scripts\pip.exe"
$pipFreeze = & $venvPip freeze 2>$null
if ($pipFreeze -match "aiohttp") {
    Write-Success "Dependencies already installed"
} else {
    & $venvPip install --upgrade pip -q
    & $venvPip install -r (Join-Path $INSTALL_DIR "requirements.txt") -q
    Write-Success "Dependencies installed"
}

Write-Host ""

# -----------------------------------------------------------------------------
# Interactive configuration
# -----------------------------------------------------------------------------
Write-Step "Configuration"
Write-Host ""

$SETTINGS_FILE = Join-Path $CONFIG_DIR "settings.yaml"
if (-not (Test-Path $SETTINGS_FILE)) {
    $example = Join-Path $CONFIG_DIR "settings.yaml.example"
    if (Test-Path $example) {
        Copy-Item $example $SETTINGS_FILE
    } else {
        @"
# sidechannel configuration
allowed_numbers:
  - "+XXXXXXXXXXX"
signal_api_url: "http://127.0.0.1:8080"
memory:
  session_timeout: 30
  max_context_tokens: 1500
autonomous:
  enabled: true
  poll_interval: 30
  quality_gates: true
sidechannel_assistant:
  enabled: false
"@ | Set-Content -Path $SETTINGS_FILE -Encoding UTF8
    }
}

Write-Host "  Enter your phone number in E.164 format (+ country code + number):"
Write-Host "  Examples: +12025551234 (US/CA)  +447911123456 (UK)  +4915212345678 (DE)" -ForegroundColor Cyan
Write-Host "            +33612345678 (FR)    +61412345678 (AU)   +819012345678 (JP)" -ForegroundColor Cyan
$PHONE_NUMBER = Read-Host "  >"
$PHONE_NUMBER = $PHONE_NUMBER.Trim()

if ($PHONE_NUMBER) {
    if ($PHONE_NUMBER -notmatch '^\+[1-9][0-9]{6,14}$') {
        Write-Warn "Doesn't look like E.164 format (e.g. +12025551234 or +447911123456)"
        $ans = Read-Host "  Continue anyway? [y/N]"
        if ($ans -notmatch "^[Yy]$") { Write-Host "  Re-run the installer with a valid phone number."; exit 1 }
    }
    $content = Get-Content $SETTINGS_FILE -Raw
    $content = $content -replace '\+XXXXXXXXXXX', $PHONE_NUMBER
    Set-Content -Path $SETTINGS_FILE -Value $content -Encoding UTF8 -NoNewline
    Write-Success "Phone number set"
}

$ENV_FILE = Join-Path $CONFIG_DIR ".env"
if (-not (Test-Path $ENV_FILE)) {
    @"
# sidechannel environment variables
# OPENAI_API_KEY=
# GROK_API_KEY=
"@ | Set-Content -Path $ENV_FILE -Encoding UTF8
}

Write-Host ""
Write-Host "  Optional: sidechannel can use OpenAI or Grok for general questions." -ForegroundColor Gray
Write-Host "  Not required - Claude handles /ask, /do, /complex."
Write-Host ""
$enableAssistant = Read-Host "  Enable optional AI assistant? [y/N]"
if ($enableAssistant -match "^[Yy]$") {
    $content = Get-Content $SETTINGS_FILE -Raw
    $content = $content -replace "enabled: false", "enabled: true"
    Set-Content -Path $SETTINGS_FILE -Value $content -Encoding UTF8 -NoNewline
    Write-Host "    Which provider? (1) OpenAI  (2) Grok"
    $providerChoice = Read-Host "    >"
    if ($providerChoice -eq "1") {
        $secureKey = Read-Host "  Enter your OpenAI API key" -AsSecureString
        $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
        try { $OPENAI_KEY = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr) } finally { [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
        if ($OPENAI_KEY) {
            $envContent = Get-Content $ENV_FILE -Raw
            $envContent = $envContent -replace "(?m)^#?\s*OPENAI_API_KEY=.*", "OPENAI_API_KEY=$OPENAI_KEY"
            Set-Content -Path $ENV_FILE -Value $envContent -Encoding UTF8 -NoNewline
            Write-Success "OpenAI configured"
        }
    } else {
        $secureKey = Read-Host "  Enter your Grok API key" -AsSecureString
        $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
        try { $GROK_KEY = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr) } finally { [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
        if ($GROK_KEY) {
            $envContent = Get-Content $ENV_FILE -Raw
            $envContent = $envContent -replace "(?m)^#?\s*GROK_API_KEY=.*", "GROK_API_KEY=$GROK_KEY"
            Set-Content -Path $ENV_FILE -Value $envContent -Encoding UTF8 -NoNewline
            Write-Success "Grok configured"
        }
    }
}

# Projects directory
Write-Host ""
Write-Host "  Projects directory: Where your code projects live."
$DEFAULT_PROJECTS = Join-Path $env:USERPROFILE "projects"
$PROJECTS_PATH = Read-Host "  Projects path [$DEFAULT_PROJECTS]"
if ([string]::IsNullOrWhiteSpace($PROJECTS_PATH)) { $PROJECTS_PATH = $DEFAULT_PROJECTS }
$PROJECTS_PATH = $PROJECTS_PATH.Trim().Replace("~", $env:USERPROFILE)

if (Test-Path $PROJECTS_PATH -PathType Container) {
    $content = Get-Content $SETTINGS_FILE -Raw
    $content = $content -replace "(?m)^#?\s*projects_base_path:.*", "projects_base_path: `"$($PROJECTS_PATH -replace '\\','/')`""
    if ($content -notmatch "projects_base_path:") {
        $content += "`nprojects_base_path: `"$($PROJECTS_PATH -replace '\\','/')`"`n"
    }
    Set-Content -Path $SETTINGS_FILE -Value $content -Encoding UTF8 -NoNewline
    Write-Success "Projects path set: $PROJECTS_PATH"

    $subdirs = @(Get-ChildItem -Path $PROJECTS_PATH -Directory -ErrorAction SilentlyContinue | Sort-Object Name)
    if ($subdirs.Count -gt 0) {
        Write-Host ""
        Write-Host "  Found $($subdirs.Count) project(s) in $PROJECTS_PATH"
        foreach ($d in $subdirs) { Write-Host "    - $($d.Name)" }
        Write-Host ""
        $register = Read-Host "  Auto-register all as projects? [Y/n]"
        if ($register -notmatch "^[Nn]$") {
            $projYaml = Join-Path $CONFIG_DIR "projects.yaml"
            $sb = [System.Text.StringBuilder]::new()
            [void]$sb.AppendLine("# sidechannel Projects Registry - auto-generated by installer")
            [void]$sb.AppendLine("projects:")
            foreach ($d in $subdirs) {
                $fullPath = Join-Path $PROJECTS_PATH $d.Name
                $pathNorm = $fullPath -replace '\\', '/'
                [void]$sb.AppendLine("  - name: `"$($d.Name)`"")
                [void]$sb.AppendLine("    path: `"$pathNorm`"")
            }
            Set-Content -Path $projYaml -Value $sb.ToString() -Encoding UTF8
            Write-Success "Registered $($subdirs.Count) project(s)"
        }
    } else {
        Write-Host "  No subdirectories found - add projects later with /add"
    }
} else {
    Write-Warn "Directory not found: $PROJECTS_PATH. Set projects_base_path later in config/settings.yaml"
}

# -----------------------------------------------------------------------------
# Signal pairing
# -----------------------------------------------------------------------------
$SIGNAL_PAIRED = $false
$LINKED_NUMBER = $null

if (-not $SkipSignal -and $DockerOk) {
    Write-Host ""
    Write-Step "Signal Pairing"
    Write-Host ""

    if (-not (Test-Path $SIGNAL_DATA_DIR)) { New-Item -ItemType Directory -Path $SIGNAL_DATA_DIR -Force | Out-Null }

    Write-Host "  Will you scan the QR code from another device (e.g., remote desktop)? [y/N]"
    $remoteMode = Read-Host "  >"
    $signalBind = "127.0.0.1"
    if ($remoteMode -match "^[Yy]$") { $signalBind = "0.0.0.0" }

    Write-Host "  Starting Signal bridge..."
    docker rm -f signal-api 2>$null
    Start-Sleep -Seconds 1

    $volPath = $SIGNAL_DATA_DIR -replace '\\', '/'
    if ($signalBind -eq "0.0.0.0") { $publish = "8080:8080" } else { $publish = "${signalBind}:8080:8080" }
    docker run -d --name signal-api --restart unless-stopped -p $publish -v "${volPath}:/home/.local/share/signal-cli" -e MODE=native bbernhard/signal-cli-rest-api:latest 2>$null

    $running = docker ps --format "{{.Names}}" 2>$null | Select-String -Pattern "signal-api" -Quiet
    if (-not $running) {
        Write-Err "  Signal bridge failed to start"
        docker logs signal-api 2>&1 | Select-Object -Last 5
    } elseif (Wait-ForQRCode -MaxWaitSeconds 90) {
        Write-Host ""
        Write-Success "Signal bridge ready"
        Write-Host ""
        Write-Host "  Link your phone to sidechannel:"
        Write-Host "    1. Open this URL in your browser:"
        if ($signalBind -eq "0.0.0.0") {
            $ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -notmatch "Loopback" } | Select-Object -First 1).IPAddress
            if (-not $ip) { $ip = "<your-ip>" }
            Write-Host "       http://${ip}:8080/v1/qrcodelink?device_name=sidechannel" -ForegroundColor Cyan
        } else {
            Write-Host "       http://127.0.0.1:8080/v1/qrcodelink?device_name=sidechannel" -ForegroundColor Cyan
        }
        Write-Host "    2. Signal app > Settings > Linked Devices > Link New Device"
        Write-Host "    3. Scan the QR code from your browser"
        Write-Host ""
        Read-Host "  Press Enter after scanning the QR code"

        Write-Host ""
        Write-Host "  Verifying link..."
        Start-Sleep -Seconds 3

        try {
            $accountsStr = (Invoke-WebRequest -Uri "http://127.0.0.1:8080/v1/accounts" -UseBasicParsing -ErrorAction Stop).Content
            if ($accountsStr -match '(\+[0-9]+)') {
                $LINKED_NUMBER = $Matches[1]
                Write-Success "Device linked: $LINKED_NUMBER"
                if ($LINKED_NUMBER -and $LINKED_NUMBER -ne $PHONE_NUMBER) {
                    $content = Get-Content $SETTINGS_FILE -Raw
                    $content = $content -replace [regex]::Escape($PHONE_NUMBER), $LINKED_NUMBER
                    Set-Content -Path $SETTINGS_FILE -Value $content -Encoding UTF8 -NoNewline
                }
                $SIGNAL_PAIRED = $true
            }
        } catch { }

        if (-not $SIGNAL_PAIRED) {
            Write-Warn "Could not verify link. Check http://127.0.0.1:8080/v1/accounts"
            $retry = Read-Host "  Retry verification? [Y/n]"
            if ($retry -notmatch "^[Nn]$") {
                Start-Sleep -Seconds 3
                try {
                    $accountsStr = (Invoke-WebRequest -Uri "http://127.0.0.1:8080/v1/accounts" -UseBasicParsing -ErrorAction Stop).Content
                    if ($accountsStr -match '(\+[0-9]+)') {
                        $LINKED_NUMBER = $Matches[1]
                        Write-Success "Device linked: $LINKED_NUMBER"
                        $SIGNAL_PAIRED = $true
                    }
                } catch { }
                if (-not $SIGNAL_PAIRED) { Write-Host "  Still not verified. Pair later via the qrcodelink URL above." }
            }
        }
    } else {
        Write-Host ""
        Write-Warn "Signal bridge is taking too long to initialize."
        Write-Host "  Check: docker logs signal-api"
        Write-Host "  Then open: http://127.0.0.1:8080/v1/qrcodelink?device_name=sidechannel"
    }
}

# -----------------------------------------------------------------------------
# Signal bridge in json-rpc mode (required for bot)
# -----------------------------------------------------------------------------
if ((Test-CommandExists "docker")) {
    try {
        docker info 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host ""
            Write-Step "Starting Signal bridge..."
            if (-not (Test-Path $SIGNAL_DATA_DIR)) { New-Item -ItemType Directory -Path $SIGNAL_DATA_DIR -Force | Out-Null }
            docker rm -f signal-api 2>$null
            Start-Sleep -Seconds 1
            $volPath = $SIGNAL_DATA_DIR -replace '\\', '/'
            docker run -d --name signal-api --restart unless-stopped -p "127.0.0.1:8080:8080" -v "${volPath}:/home/.local/share/signal-cli" -e MODE=json-rpc bbernhard/signal-cli-rest-api:latest 2>$null
            Start-Sleep -Seconds 3
            $running = docker ps --format "{{.Names}}" 2>$null | Select-String -Pattern "signal-api" -Quiet
            if ($running) { Write-Success "Signal bridge running (json-rpc mode)" } else { Write-Warn "Signal bridge did not start. Check: docker logs signal-api" }
        }
    } catch { }
}

# -----------------------------------------------------------------------------
# run.ps1 and scheduled task
# -----------------------------------------------------------------------------
$RunScriptPath = Join-Path $INSTALL_DIR "run.ps1"
$runPs1Content = @"
# sidechannel launcher (generated by install.ps1)
Set-Location -LiteralPath '$INSTALL_DIR'
if (Test-Path '$ENV_FILE') {
    Get-Content '$ENV_FILE' | ForEach-Object {
        if (`$_ -match '^\s*([^#][^=]+)=(.*)$') {
            Set-Item -Path "Env:`$(`$Matches[1].Trim())" -Value `$Matches[2].Trim()
        }
    }
}
& '$venvPython' -m sidechannel
"@
# Expand paths in generated script (here-string was single-quote style; expand explicitly)
$runPs1Content = $runPs1Content.Replace('$INSTALL_DIR', $INSTALL_DIR).Replace('$ENV_FILE', $ENV_FILE)
$runPs1Content = $runPs1Content.Replace('$venvPython', $venvPython)
Set-Content -Path $RunScriptPath -Value $runPs1Content -Encoding UTF8

$INSTALLED_SERVICE = $false
$STARTED_SERVICE = $false

if (-not $SkipService) {
    Write-Host ""
    $installTask = Read-Host "Start sidechannel as a scheduled task (run at logon)? [Y/n]"
    if ($installTask -notmatch "^[Nn]$") {
        $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-ExecutionPolicy Bypass -NoProfile -File `"$RunScriptPath`"" -WorkingDirectory $INSTALL_DIR
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
        try {
            Register-ScheduledTask -TaskName $TASK_NAME -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
            Write-Success "Scheduled task installed and enabled"
            $INSTALLED_SERVICE = $true
            Start-ScheduledTask -TaskName $TASK_NAME
            Start-Sleep -Seconds 2
            $taskInfo = Get-ScheduledTask -TaskName $TASK_NAME
            if ($taskInfo.State -eq "Running") {
                Write-Success "sidechannel is running!"
                $STARTED_SERVICE = $true
            } else {
                Write-Warn "Task installed but not running. Start with: Start-ScheduledTask -TaskName '$TASK_NAME'"
            }
        } catch {
            Write-Warn "Could not register scheduled task: $_"
            Write-Host "  Run manually: $RunScriptPath" -ForegroundColor Cyan
        }
    }
}

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
Write-Host ""
if ($SIGNAL_PAIRED -and $STARTED_SERVICE) {
    Write-Host "╔════════════════════════════════════════════════════════════════╗" -ForegroundColor Green
    Write-Host "║                  sidechannel is ready!                         ║" -ForegroundColor Green
    Write-Host "╚════════════════════════════════════════════════════════════════╝" -ForegroundColor Green
    Write-Host ""
    $numDisplay = if ($LINKED_NUMBER) { $LINKED_NUMBER } else { "your Signal number" }
    Write-Host "  Send a message to $numDisplay to test! Try: /help" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  View/stop/restart: Task Scheduler, Task Scheduler Library, $TASK_NAME"
    Write-Host "  Or: Get-ScheduledTask -TaskName '$TASK_NAME' | Start-ScheduledTask"
} else {
    Write-Host "╔════════════════════════════════════════════════════════════════╗" -ForegroundColor Green
    Write-Host "║              sidechannel installation complete!                  ║" -ForegroundColor Green
    Write-Host "╚════════════════════════════════════════════════════════════════╝" -ForegroundColor Green
    Write-Host ""
    Write-Host "  Install dir: $INSTALL_DIR" -ForegroundColor Cyan
    Write-Host "  Config:      $CONFIG_DIR\settings.yaml" -ForegroundColor Cyan
    $step = 1
    if (-not $claudePath) { Write-Host "  $step. Install Claude CLI: https://docs.anthropic.com/en/docs/claude-code"; $step++ }
    if (-not $SIGNAL_PAIRED -and $SkipSignal) { Write-Host "  $step. Set up Signal: re-run install.ps1 without -SkipSignal"; $step++ }
    if (-not $STARTED_SERVICE) {
        if ($INSTALLED_SERVICE) { Write-Host "  $step. Start task: Start-ScheduledTask -TaskName '$TASK_NAME'" -ForegroundColor Cyan }
        else { Write-Host "  $step. Start sidechannel: $RunScriptPath" -ForegroundColor Cyan }
    }
    Write-Host ""
    Write-Host "  Send a test message on Signal: /help"
}

Write-Host ""
Write-Host "  Config:  $CONFIG_DIR\settings.yaml" -ForegroundColor Cyan
Write-Host "  Docs:    https://github.com/hackingdave/sidechannel" -ForegroundColor Cyan
Write-Host ""
