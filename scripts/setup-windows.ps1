<#
.SYNOPSIS
Sets up this project on Windows, inside WSL 2 with Ubuntu 24.04.

.DESCRIPTION
The Makefile and the scripts in this repository are written for bash, so on
Windows they run in WSL: a real Ubuntu running alongside Windows. This script:

  1. Turns on WSL, if it is not on yet. Windows then needs a restart, after
     which you run this script again.
  2. Installs Ubuntu 24.04, if it is not there. Ubuntu asks you to choose a
     Linux user name and password. Remember the password: setup asks for it.
  3. Copies this repository into your Linux home folder, as ~/warehouse-cloud.
     Building from C:\ through /mnt/c is slow and loses Linux file permissions.
  4. Runs scripts/setup-linux.sh there, which installs every tool.

Run it from PowerShell, in the repository folder. The first run needs
PowerShell opened as Administrator, to turn on WSL:

  powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1

It is safe to run again. Each step checks what is already there.

.PARAMETER NoDocker
Skip Docker, which only the local run and the integration tests need.

.PARAMETER Distro
The WSL distribution to use. The default is Ubuntu-24.04.
#>
param(
    [switch]$NoDocker,
    [string]$Distro = "Ubuntu-24.04"
)

$ErrorActionPreference = "Stop"
# Without this, wsl.exe writes UTF-16, which PowerShell reads as spaced-out text.
$env:WSL_UTF8 = "1"

function Say([string]$Message) {
    Write-Host ""
    Write-Host "== $Message" -ForegroundColor Cyan
}

function Note([string]$Message) {
    Write-Host "   $Message"
}

function Fail([string]$Message) {
    Write-Host ""
    Write-Host "error: $Message" -ForegroundColor Red
    exit 1
}

# Runs wsl.exe, captures what it prints, and returns the exit code and the text.
# Only for commands that need no input: captured output is not a terminal.
function Invoke-WslCapture([string[]]$WslArgs) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $lines = & wsl.exe @WslArgs 2>&1 | ForEach-Object { "$_" }
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    $text = (($lines -join "`n") -replace "`0", "").Trim()
    return [pscustomobject]@{ Code = $code; Text = $text }
}

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not ((Test-Path (Join-Path $repo "Makefile")) -and (Test-Path (Join-Path $repo "infra")))) {
    Fail "run this script from a copy of the repository: scripts\setup-windows.ps1"
}

# ------------------------------------------------------------------------- WSL

Say "WSL"
$build = [Environment]::OSVersion.Version.Build
if ($build -lt 19041) {
    Fail "WSL 2 needs Windows 10 version 2004 (build 19041) or later, or Windows 11. This is build $build. Update Windows first."
}

$wslReady = $false
if (Get-Command wsl.exe -ErrorAction SilentlyContinue) {
    $status = Invoke-WslCapture @("--status")
    $wslReady = ($status.Code -eq 0)
}

if (-not $wslReady) {
    if (-not (Test-Administrator)) {
        Fail "WSL is not turned on yet. Open PowerShell as Administrator (right-click, Run as administrator) and run this script again."
    }
    Note "turning on WSL. This downloads about 200 MB."
    & wsl.exe --install --no-distribution
    if ($LASTEXITCODE -ne 0) {
        Fail "wsl --install failed. Check that virtualization is enabled in the BIOS, update Windows, or install 'Windows Subsystem for Linux' from the Microsoft Store. Then run this script again."
    }
    Write-Host ""
    Write-Host "WSL is installed. Restart Windows, then run this script again." -ForegroundColor Yellow
    exit 0
}
Note "on"

# ---------------------------------------------------------------------- Ubuntu

Say $Distro
$installed = (Invoke-WslCapture @("--list", "--quiet")).Text -split "`r?`n" | ForEach-Object { $_.Trim() }
if ($installed -notcontains $Distro) {
    Note "installing. When it asks, choose a Linux user name and password."
    Note "Once you see a Linux prompt ending in $, type exit and press Enter to carry on."
    & wsl.exe --install -d $Distro
    if ($LASTEXITCODE -ne 0) {
        Fail "could not install $Distro. Run 'wsl --list --online' to see the names available, and pass one with -Distro."
    }
    $installed = (Invoke-WslCapture @("--list", "--quiet")).Text -split "`r?`n" | ForEach-Object { $_.Trim() }
    if ($installed -notcontains $Distro) {
        Fail "$Distro did not finish installing. Run 'wsl -d $Distro' once, create your user, type exit, then run this script again."
    }
}

$linuxUser = (Invoke-WslCapture @("-d", $Distro, "--exec", "id", "-un")).Text
if ($linuxUser -eq "root" -or $linuxUser -eq "") {
    Fail "$Distro has no Linux user yet. Run 'wsl -d $Distro', create a user name and password, type exit, then run this script again."
}
$linuxHome = (Invoke-WslCapture @("-d", $Distro, "--exec", "printenv", "HOME")).Text
if (-not $linuxHome.StartsWith("/")) { $linuxHome = "/home/$linuxUser" }
Note "ready, as Linux user $linuxUser"

# Docker inside WSL needs systemd to start by itself. Ubuntu 24.04 turns it on, but
# older installs may not have.
if (-not $NoDocker) {
    $wslConf = (Invoke-WslCapture @("-d", $Distro, "--exec", "cat", "/etc/wsl.conf")).Text
    if ($wslConf -notmatch "(?m)^\s*systemd\s*=\s*true") {
        Note "turning on systemd in $Distro, for Docker"
        & wsl.exe -d $Distro -u root --exec sh -c "printf '\n[boot]\nsystemd=true\n' >> /etc/wsl.conf"
        & wsl.exe --terminate $Distro | Out-Null
    }
}

# ------------------------------------------------------------ the repository

Say "Repository"
$target = "$linuxHome/warehouse-cloud"
$exists = (Invoke-WslCapture @("-d", $Distro, "--exec", "test", "-d", $target)).Code -eq 0
if ($exists) {
    Note "using the copy already in $target. To update it: cd ~/warehouse-cloud; git pull"
} else {
    $source = (Invoke-WslCapture @("-d", $Distro, "--exec", "wslpath", "-a", $repo)).Text
    if ((Invoke-WslCapture @("-d", $Distro, "--exec", "sh", "-c", "command -v git")).Code -ne 0) {
        Note "installing git in $Distro"
        & wsl.exe -d $Distro -u root --exec sh -c "apt-get update -q && apt-get install -y -q git"
    }
    if (Test-Path (Join-Path $repo ".git")) {
        # A clone, not a file copy: Linux git checks out LF line endings, whatever
        # Git for Windows did with this folder.
        & wsl.exe -d $Distro --exec git -c "safe.directory=*" clone -q $source $target
        if ($LASTEXITCODE -ne 0) { Fail "could not copy the repository into $target" }
        $origin = (Invoke-WslCapture @("-d", $Distro, "--exec", "git", "-c", "safe.directory=*", "-C", $source, "remote", "get-url", "origin")).Text
        if ($origin -match "github.com") {
            & wsl.exe -d $Distro --exec git -C $target remote set-url origin $origin
            Note "copied, with origin $origin"
        } else {
            Note "copied"
        }
    } else {
        & wsl.exe -d $Distro --exec cp -r $source $target
        if ($LASTEXITCODE -ne 0) { Fail "could not copy the repository into $target" }
        Note "copied (not a git clone, so git pull will not work there)"
    }
}

# --------------------------------------------------------------- the tools

Say "Tools, installed by scripts/setup-linux.sh inside $Distro"
Note "sudo asks for the Linux password you chose for $linuxUser."
$linuxArgs = @()
if ($NoDocker) { $linuxArgs += "--no-docker" }
& wsl.exe -d $Distro --cd $target --exec bash scripts/setup-linux.sh @linuxArgs
if ($LASTEXITCODE -ne 0) {
    Fail "setup inside $Distro did not finish. Read the message above, then run this script again."
}

# Restart Ubuntu, so systemd and the docker group take effect.
& wsl.exe --terminate $Distro | Out-Null

Write-Host ""
Write-Host "Done. To work on the project:" -ForegroundColor Green
Write-Host "  1. Open '$Distro' from the Start menu, or run: wsl -d $Distro"
Write-Host "  2. cd ~/warehouse-cloud && source .venv/bin/activate"
Write-Host "  3. Carry on from step 3 of what setup printed above: sign in to Google Cloud"
Write-Host "     and GitHub, run the tests, then deploy as README.md describes."
Write-Host ""
Write-Host "Keep Argo CD's deploy key at ~/argocd_deploy_key inside Ubuntu, beside the"
Write-Host "repository, where make up looks for it. Your Windows files are under /mnt/c."
Write-Host "To edit in VS Code, install its WSL extension and run 'code .' in the repository."
