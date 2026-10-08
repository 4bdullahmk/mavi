<#
.SYNOPSIS
    Set up this extracted Mavi release for the current Windows user.
.DESCRIPTION
    Installs only missing Python/Ollama dependencies through their exact
    official winget package IDs, creates a per-user venv, asks before pulling
    a standard local chat model, verifies a real local model response, then
    starts this release on loopback. Existing installations and Mavi data are
    preserved. Nothing is run as administrator and no execution policy changes.
.PARAMETER NonInteractive
    Never prompt. Pair with InstallPython, InstallOllama, and/or DownloadModel
    when those actions are explicitly authorized; otherwise missing steps fail.
.PARAMETER InstallPython
    Explicitly authorize winget to install Python.Python.3.12 if Python 3.11+
    is unavailable.
.PARAMETER InstallOllama
    Explicitly authorize winget to install Ollama.Ollama if Ollama is missing.
.PARAMETER DownloadModel
    Explicitly authorize downloading the selected standard Qwen3 chat model.
.PARAMETER Model
    qwen3:4b or qwen3:8b. Defaults to 4b; 8b requires at least 24 GiB RAM.
.PARAMETER EnableWindowsAutomation
    Explicitly authorize optional pyautogui/pygetwindow packages. This does not
    enable computer control or bypass Mavi's focus and confirmation checks.
.PARAMETER EnableDictation
    Explicitly authorize faster-whisper and Hugging Face Hub packages. A local
    Whisper model is still separate and is never downloaded automatically.
.PARAMETER EnableModelDownloads
    Explicitly authorize only the huggingface-hub package, used by optional
    model setup tools. This does not download any model weights.
.EXAMPLE
    .\Install-Mavi.ps1
    Interactive setup: asks before installing missing dependencies or model.
.EXAMPLE
    .\Install-Mavi.ps1 -NonInteractive -InstallPython -InstallOllama -DownloadModel
    Automation setup with those explicit installation/download authorizations.
.EXAMPLE
    .\Install-Mavi.ps1 -NonInteractive -Model qwen3:4b
    Uses installed dependencies/model only and fails if something is missing.
.EXAMPLE
    .\Install-Mavi.ps1 -EnableWindowsAutomation -EnableDictation
    Explicitly authorizes optional automation and dictation packages.
#>
[CmdletBinding()]
param(
    [switch]$NonInteractive,
    [switch]$InstallPython,
    [switch]$InstallOllama,
    [switch]$DownloadModel,
    [switch]$EnableWindowsAutomation,
    [switch]$EnableDictation,
    [switch]$EnableModelDownloads,
    [ValidateSet('', 'qwen3:4b', 'qwen3:8b')]
    [string]$Model = ''
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
trap {
    Write-Host ("Mavi setup stopped: " + $_.Exception.Message) -ForegroundColor Red
    if (-not $NonInteractive) { [void](Read-Host 'Press Enter to close') }
    exit 1
}

function Confirm-Action([string]$Message, [switch]$Explicit) {
    if ($Explicit) { return $true }
    if ($NonInteractive) { return $false }
    return ((Read-Host "$Message Type yes to continue") -ceq 'yes')
}

function Refresh-ProcessPath {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = (@($machine, $user, $env:Path) | Where-Object { $_ }) -join ';'
}

function Find-Python {
    Refresh-ProcessPath
    $launcher = Get-Command 'py.exe' -ErrorAction SilentlyContinue
    if ($launcher) {
        foreach ($version in @('3.12', '3.11')) {
            $prefix = @("-$version")
            $output = (& $launcher.Source @prefix --version 2>&1 | Out-String).Trim()
            if ($LASTEXITCODE -eq 0 -and $output -match 'Python\s+3\.(\d+)') {
                return @{ Exe = $launcher.Source; Prefix = $prefix }
            }
        }
    }
    foreach ($candidate in @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'),
        (Join-Path $env:ProgramFiles 'Python312\python.exe'),
        (Join-Path $env:ProgramFiles 'Python311\python.exe')
    )) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return @{ Exe = $candidate; Prefix = @() }
        }
    }
    if ($launcher) {
        $prefix = @('-3')
        $output = (& $launcher.Source @prefix --version 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -eq 0 -and $output -match 'Python\s+(\d+)\.(\d+)') {
            return @{ Exe = $launcher.Source; Prefix = $prefix }
        }
    }
    $python = Get-Command 'python.exe' -ErrorAction SilentlyContinue
    if ($python) { return @{ Exe = $python.Source; Prefix = @() } }
    return $null
}

function Test-PythonVersion($Python) {
    if (-not $Python) { return $false }
    $versionArgs = @($Python.Prefix) + @('--version')
    $versionText = (& $Python.Exe @versionArgs 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $versionText -notmatch 'Python\s+(\d+)\.(\d+)') { return $false }
    $script:PythonVersionText = $versionText
    return ([int]$Matches[1] -gt 3 -or ([int]$Matches[1] -eq 3 -and [int]$Matches[2] -ge 11))
}

function Install-WingetPackage([string]$PackageId) {
    $winget = Get-Command 'winget.exe' -ErrorAction SilentlyContinue
    if (-not $winget) { throw "winget is not available. Install the dependency from its official site, then rerun this installer." }
    Write-Host "Installing missing dependency $PackageId with winget. Review any package prompts."
    $wingetArgs = @('install', '--id', $PackageId, '--exact', '--source', 'winget', '--scope', 'user')
    if ($NonInteractive) { $wingetArgs += '--disable-interactivity' }
    & $winget.Source @wingetArgs
    if ($LASTEXITCODE -ne 0) { throw "winget could not install $PackageId (exit $LASTEXITCODE). No existing Mavi data was removed." }
    Refresh-ProcessPath
}

function Find-Ollama {
    Refresh-ProcessPath
    $command = Get-Command 'ollama.exe' -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    if ($env:LOCALAPPDATA) {
        $candidate = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
    return $null
}

function Get-OllamaTags([string]$Uri) {
    try {
        return Invoke-RestMethod -Uri "$Uri/api/tags" -Method Get -TimeoutSec 3
    } catch { return $null }
}

function Wait-Ollama([string]$Uri, [int]$Seconds = 45) {
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    do {
        $tags = Get-OllamaTags $Uri
        if ($null -ne $tags) { return $tags }
        Start-Sleep -Seconds 1
    } while ([DateTime]::UtcNow -lt $deadline)
    return $null
}

function Get-FreeDiskGiB([string]$Path) {
    $root = [IO.Path]::GetPathRoot([IO.Path]::GetFullPath($Path))
    $drive = [IO.DriveInfo]::new($root)
    return [math]::Floor($drive.AvailableFreeSpace / 1GB)
}

function Test-PortClosed([int]$Port) {
    $client = [Net.Sockets.TcpClient]::new()
    try {
        $pending = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        if (-not $pending.AsyncWaitHandle.WaitOne(300)) { return $true }
        try { $client.EndConnect($pending); return $false } catch { return $true }
    } finally { $client.Dispose() }
}

$releaseRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not (Test-Path -LiteralPath (Join-Path $releaseRoot 'portable\server.py') -PathType Leaf)) {
    throw 'This folder is missing portable\server.py. Extract the complete Mavi Windows ZIP and rerun Install-Mavi.cmd.'
}
if (-not $env:LOCALAPPDATA) { throw 'LOCALAPPDATA is unavailable for this Windows account.' }
$maviHome = Join-Path $env:LOCALAPPDATA 'Mavi'
$dataDir = $maviHome
$venv = Join-Path $maviHome 'venv'
$requirements = Join-Path $releaseRoot 'portable\requirements.txt'
$marker = Join-Path $maviHome 'requirements-installed.txt'
$ollamaUri = 'http://127.0.0.1:11434'
$maviUri = 'http://127.0.0.1:8769/'

Write-Host 'Mavi setup stores this installation''s settings and files in:' $maviHome
if (-not (Test-PortClosed 8769)) {
    throw 'Port 8769 is already in use. This installer did not assume the existing service is Mavi or stop it. Close that service, then retry.'
}
if (Test-Path -LiteralPath $maviHome) {
    if ((Get-Item -LiteralPath $maviHome -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw 'Mavi data folder is a link; nothing was written there.'
    }
}
New-Item -ItemType Directory -Path $maviHome -Force | Out-Null
$workspacePath = Join-Path $maviHome 'workspace.json'
$defaultSettings = [ordered]@{
    model = ''
    theme = 'system'
    onboarded = $false
    project_path = ''
    release_url = ''
    discord = [pscustomobject]@{}
}
$workspace = [pscustomobject]@{
    chats = @()
    profile = ''
    settings = [pscustomobject]$defaultSettings
}
if (Test-Path -LiteralPath $workspacePath -PathType Leaf) {
    if ((Get-Item -LiteralPath $workspacePath -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw 'Existing Mavi workspace is a link; nothing was read or overwritten.'
    }
    try { $workspace = Get-Content -LiteralPath $workspacePath -Raw | ConvertFrom-Json }
    catch { throw 'Existing Mavi workspace settings are not valid JSON; nothing was overwritten.' }
    if ($null -eq $workspace -or -not $workspace.PSObject) { throw 'Existing Mavi workspace data is not an object; nothing was overwritten.' }
    if (-not $workspace.PSObject.Properties['settings'] -or $null -eq $workspace.settings) {
        $workspace | Add-Member -MemberType NoteProperty -Name settings -Value ([pscustomobject]@{}) -Force
    }
    if ($workspace.settings -isnot [System.Management.Automation.PSCustomObject]) {
        throw 'Existing Mavi settings are not an object; nothing was overwritten.'
    }
    foreach ($key in $defaultSettings.Keys) {
        if (-not $workspace.settings.PSObject.Properties[$key] -or $null -eq $workspace.settings.$key) {
            $workspace.settings | Add-Member -MemberType NoteProperty -Name $key -Value $defaultSettings[$key]
        }
    }
    if (-not $workspace.PSObject.Properties['chats'] -or $null -eq $workspace.chats) { $workspace | Add-Member -MemberType NoteProperty -Name chats -Value @() -Force }
    if (-not $workspace.PSObject.Properties['profile'] -or $null -eq $workspace.profile) { $workspace | Add-Member -MemberType NoteProperty -Name profile -Value '' -Force }
}
$savedModel = ''
if ($workspace.settings.PSObject.Properties['model']) { $savedModel = [string]$workspace.settings.model }

$python = Find-Python
if (-not (Test-PythonVersion $python)) {
    if (-not (Confirm-Action 'Python 3.12 is missing or Python 3.11+ was not found. Install official Python 3.12 through winget?' -Explicit:$InstallPython)) {
        throw 'Python 3.11 or newer is required. Rerun interactively or use -InstallPython to authorize the official winget install.'
    }
    Install-WingetPackage 'Python.Python.3.12'
    $python = Find-Python
    if (-not (Test-PythonVersion $python)) {
        throw 'Python was installed but could not be resolved as Python 3.11+ in this session. Open a new terminal and retry.'
    }
}
Write-Host "Using $($script:PythonVersionText)."

$ollamaExe = Find-Ollama
if (-not $ollamaExe) {
    if (-not (Confirm-Action 'Ollama is missing. Install official Ollama through winget?' -Explicit:$InstallOllama)) {
        throw 'Ollama is required. Rerun interactively or use -InstallOllama to authorize the official winget install.'
    }
    Install-WingetPackage 'Ollama.Ollama'
    $ollamaExe = Find-Ollama
    if (-not $ollamaExe) { throw 'Ollama was installed but its executable is not on PATH or in the standard per-user install folder. Open a new terminal and retry.' }
}

$tags = Get-OllamaTags $ollamaUri
if ($null -eq $tags) {
    Write-Host 'Starting the local Ollama service and waiting up to 45 seconds.'
    Start-Process -FilePath $ollamaExe -ArgumentList @('serve') -WindowStyle Hidden | Out-Null
    $tags = Wait-Ollama $ollamaUri
}
if ($null -eq $tags) { throw 'Ollama did not become ready on loopback. Start Ollama from the Start menu and rerun setup.' }
$installedNames = @($tags.models | ForEach-Object { [string]$_.name })

$ramGiB = [math]::Floor((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
$ollamaModelsPath = if ($env:OLLAMA_MODELS) { $env:OLLAMA_MODELS } else { Join-Path $env:USERPROFILE '.ollama\models' }
$freeGiB = Get-FreeDiskGiB $ollamaModelsPath
if (-not $Model) {
    if ($savedModel -and $savedModel -in $installedNames) { $Model = $savedModel }
    elseif ($ramGiB -ge 24) { $Model = 'qwen3:8b' }
    else { $Model = 'qwen3:4b' }
}
if ($Model -eq 'qwen3:8b' -and $ramGiB -lt 24) { throw 'qwen3:8b requires at least 24 GiB system RAM in this installer. Choose qwen3:4b.' }
$minimumFreeGiB = if ($Model -eq 'qwen3:8b') { 10 } else { 6 }
if (-not $PSBoundParameters.ContainsKey('Model') -and $Model -eq 'qwen3:8b' -and
    $Model -notin $installedNames -and $freeGiB -lt 10) { $Model = 'qwen3:4b'; $minimumFreeGiB = 6 }
Write-Host "Detected $ramGiB GiB system RAM and $freeGiB GiB free on the Ollama model drive. Selected model: $Model."
if ($Model -notin $installedNames) {
    if ($freeGiB -lt $minimumFreeGiB) { throw "Not enough free disk space for the selected model (need at least $minimumFreeGiB GiB by this check). Free space before retrying; no model download was started." }
    if (-not (Confirm-Action "Download standard local model $Model now? Ollama reports the download size before transfer." -Explicit:$DownloadModel)) {
        throw "Model $Model is not installed. No model download was started. Rerun and approve the prompt or pass -DownloadModel."
    }
    & $ollamaExe pull $Model
    if ($LASTEXITCODE -ne 0) { throw "Ollama could not download $Model (exit $LASTEXITCODE). Existing models and Mavi data remain intact." }
    $tags = Get-OllamaTags $ollamaUri
    $installedNames = @($tags.models | ForEach-Object { [string]$_.name })
    if ($Model -notin $installedNames) { throw "Ollama pull completed without making $Model available." }
}

Write-Host 'Running a bounded real-response check against the selected local model.'
$smokePayload = @{
    model = $Model
    messages = @(@{ role = 'user'; content = 'Give one short friendly greeting. Do not show analysis.' })
    stream = $false
    think = $false
    options = @{ num_predict = 64; num_ctx = 1024 }
} | ConvertTo-Json -Depth 6
try {
    $smoke = Invoke-RestMethod -Uri "$ollamaUri/api/chat" -Method Post -ContentType 'application/json' -Body $smokePayload -TimeoutSec 180
} catch { throw 'The selected model did not complete the local response check. Mavi was not started. Check Ollama and available memory, then retry.' }
$responseText = [string]$smoke.message.content
if ($responseText.IndexOf('</think>', [StringComparison]::OrdinalIgnoreCase) -ge 0) {
    $closeIndex = $responseText.IndexOf('</think>', [StringComparison]::OrdinalIgnoreCase)
    $openIndex = $responseText.IndexOf('<think>', [StringComparison]::OrdinalIgnoreCase)
    if ($openIndex -lt 0 -or $closeIndex -lt $openIndex) { $responseText = $responseText.Substring($closeIndex + 8) }
}
$visibleResponse = [regex]::Replace($responseText, '(?is)<think>.*?(?:</think>|$)', '')
if ([string]::IsNullOrWhiteSpace($visibleResponse.Trim()) -or $smoke.done -ne $true) {
    throw 'The local model response check returned no completed answer. Mavi was not started.'
}
Write-Host 'Selected model response check passed.'

$pythonArgs = @($python.Prefix) + @('-m', 'venv', $venv)
if (-not (Test-Path -LiteralPath (Join-Path $venv 'Scripts\python.exe') -PathType Leaf)) {
    Write-Host "Creating the per-user Python environment at $venv."
    & $python.Exe @pythonArgs
    if ($LASTEXITCODE -ne 0) { throw 'Could not create Mavi''s Python environment. Existing user data was preserved.' }
}
$venvPython = Join-Path $venv 'Scripts\python.exe'
$needsRequirements = $true
if ((Test-Path -LiteralPath $marker -PathType Leaf) -and (Test-Path -LiteralPath $requirements -PathType Leaf)) {
    $markerBytes = [Convert]::ToBase64String([IO.File]::ReadAllBytes($marker))
    $requiredBytes = [Convert]::ToBase64String([IO.File]::ReadAllBytes($requirements))
    $needsRequirements = $markerBytes -cne $requiredBytes
}
if ($needsRequirements) {
    Write-Host 'Installing the release''s required Python packages. The success marker is written only after pip succeeds.'
    & $venvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw 'Could not update pip. The requirements success marker was not changed.' }
    & $venvPython -m pip install -r $requirements
    if ($LASTEXITCODE -ne 0) { throw 'Python package installation failed. The requirements success marker was not changed.' }
    [IO.File]::WriteAllBytes($marker, [IO.File]::ReadAllBytes($requirements))
}

function Install-OptionalPipPackages([string[]]$Packages, [string]$Feature, [switch]$Explicit) {
    if (-not (Confirm-Action "Install optional $Feature Python packages in Mavi's private environment?" -Explicit:$Explicit)) {
        throw "Optional $Feature packages were not installed. Rerun with the corresponding explicit feature switch to authorize them."
    }
    & $venvPython -m pip install @Packages
    if ($LASTEXITCODE -ne 0) { throw "Optional $Feature package installation failed. The base Mavi setup remains available; retry setup later." }
}

if ($EnableWindowsAutomation) {
    Install-OptionalPipPackages @('pyautogui>=0.9.54,<1', 'pygetwindow>=0.0.9,<1') 'focused-window automation' -Explicit:$EnableWindowsAutomation
}
if ($EnableDictation) {
    Install-OptionalPipPackages @('faster-whisper>=1.1,<2', 'huggingface-hub>=0.30,<2') 'offline dictation' -Explicit:$EnableDictation
    Write-Host "Dictation still needs a compatible local CTranslate2 model under $maviHome\models\whisper; no model weights were downloaded."
}
if ($EnableModelDownloads -and -not $EnableDictation) {
    Install-OptionalPipPackages @('huggingface-hub>=0.30,<2') 'optional model-download tools' -Explicit:$EnableModelDownloads
}

$driveFreeAfterPull = Get-FreeDiskGiB $ollamaModelsPath
Write-Host "Free disk after model setup: $driveFreeAfterPull GiB. Running the local setup checks."
$doctorScript = Join-Path $releaseRoot 'portable\setup_check.py'
if (-not (Test-Path -LiteralPath $doctorScript -PathType Leaf)) { throw 'The packaged local setup checker is missing.' }
$doctorArgs = @($doctorScript, '--json', '--spreadsheet-smoke', '--timeout', '60')
$doctorOutput = & $venvPython @doctorArgs
if ($LASTEXITCODE -ne 0) { throw 'The local setup doctor or one of its real chat/spreadsheet checks failed. Mavi was not started.' }
try { $doctor = ($doctorOutput -join "`n") | ConvertFrom-Json }
catch { throw 'The setup doctor returned invalid output. Mavi was not started.' }
if (-not $doctor.smoke_tests.spreadsheet.passed) {
    throw 'The setup doctor did not pass its temporary spreadsheet export check. Mavi was not started.'
}
Write-Host 'Setup doctor passed: temporary spreadsheet export opened and validated.'

# Pin an unset/stale workspace preference to the selected model only after all
# setup checks have passed. Preserve every other chat, profile, and setting.
$currentModel = ''
if ($workspace.settings.PSObject.Properties['model']) { $currentModel = [string]$workspace.settings.model }
if (($PSBoundParameters.ContainsKey('Model') -and $Model -and $currentModel -ne $Model) -or
    -not $currentModel -or $currentModel -notin $installedNames) {
    if ($workspace.settings.PSObject.Properties['model']) { $workspace.settings.model = $Model }
    else { $workspace.settings | Add-Member -MemberType NoteProperty -Name model -Value $Model }
    $workspaceTemp = Join-Path $maviHome ('.workspace-' + [guid]::NewGuid().ToString('N') + '.tmp')
    $workspaceJson = $workspace | ConvertTo-Json -Depth 32
    [IO.File]::WriteAllText($workspaceTemp, $workspaceJson, [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $workspaceTemp -Destination $workspacePath -Force
}

$env:MAVI_DATA_DIR = $dataDir
$env:MAVI_HOST = '127.0.0.1'
$env:MAVI_PORT = '8769'
$serverScript = Join-Path $releaseRoot 'portable\server.py'
$server = Start-Process -FilePath $venvPython -ArgumentList @('"' + $serverScript + '"', '--port', '8769') -WorkingDirectory $releaseRoot -PassThru
$ready = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    if ($server.HasExited) { break }
    try {
        $web = Invoke-WebRequest -Uri $maviUri -Method Get -TimeoutSec 2 -UseBasicParsing
        if ($web.StatusCode -eq 200 -and $web.Content -match 'Mavi') { $ready = $true; break }
    } catch { }
    Start-Sleep -Seconds 1
}
if (-not $ready) { throw 'The Mavi process did not pass its loopback page check. Inspect the server window; this installer did not claim setup succeeded.' }
Write-Host ''
Write-Host 'Mavi setup checks passed: required packages installed, local model answered, and the new Mavi loopback page responded.'
Write-Host "Open $maviUri in your browser. The server process is running in its own console window."
Write-Host 'GPU, browser automation, dictation, image generation, and Discord behavior still depend on Windows hardware/configuration and are not verified by setup.'
Start-Process $maviUri | Out-Null
