# SPDX-License-Identifier: GPL-3.0-or-later
<#
.SYNOPSIS
    OpenETQW: Windows (x64 and arm64) build helper.

.DESCRIPTION
    With no options: checks the build dependencies (Visual Studio with C++,
    Python with meson and ninja, vcpkg with the engine's packages, and
    glslangValidator), finds the game repository ($env:OPENETQW_GAME_REPO,
    else ..\openETQW-game, cloned at the engine's pinned commit if missing),
    builds the game module and idLib, then the engine's client, dedicated
    server and unit tests, all with one build type. The engine build copies
    the game module, SDL3.dll and z.dll next to its binaries. An existing build
    directory is reconfigured, so changed game headers are always staged again.
    docs/build-scripts.md describes the behaviour.

.PARAMETER BuildType
    debug, debugoptimized (default) or release.

.PARAMETER Clean
    Wipe both build directories first.

.PARAMETER BuildDir
    Engine build directory (default: build). The game always builds in its
    repository's build\, where the engine looks.

.PARAMETER SyncGame
    Check the game repository out at the engine's pinned commit
    (tools\ci\companion.ref) first. Refuses if it has changes.

.PARAMETER VcpkgRoot
    The vcpkg tree (default: $env:VCPKG_ROOT, else $env:USERPROFILE\vcpkg).

.PARAMETER NoDeps
    Skip the dependency check.

.PARAMETER InstallDeps
    Install missing vcpkg packages (vcpkg install, no administrator rights).
    Without it, the install command is printed.

.PARAMETER Test
    After the build, run the asset-free unit suite.

.PARAMETER Run
    After the build, start the client (main menu; with -Map, a devmap session).

.PARAMETER Server
    After the build, start a dedicated server on -Map (default valley).

.PARAMETER Map
    The map for -Run or -Server.

.PARAMETER Validate
    Instead of a build, run tools\validation\openetqw_validate.py pr, the merge
    gate's local check (its own build directories).

.PARAMETER DryRun
    Print the commands without running them.

.PARAMETER Help
    Show this help and exit.

.NOTES
    Retail data: -Run and -Server use $env:ETQW_DATA_PATH when it is set (the
    install root whose base\ holds pak000.pk4); otherwise the engine looks for
    it itself (readme, Install and packages).
#>
param(
    [ValidateSet("debug", "debugoptimized", "release")]
    [string]$BuildType = "",
    [switch]$Clean,
    [string]$BuildDir = "",
    [switch]$SyncGame,
    [string]$VcpkgRoot = "",
    [switch]$NoDeps,
    [switch]$InstallDeps,
    [switch]$Test,
    [switch]$Run,
    [switch]$Server,
    [string]$Map = "",
    [switch]$Validate,
    [switch]$DryRun,
    [switch]$Help
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
# ConstrainedLanguage mode (AppLocker, WDAC) allows method calls on core types
# only, so this script uses cmdlets and operators, no .NET static or object
# methods beyond strings'; tests/test_build_scripts.py runs it in that mode.

function Write-Info([string]$Message) { Write-Host "[openetqw] $Message" -ForegroundColor Green }
function Write-Warn([string]$Message) { Write-Host "[openetqw] $Message" -ForegroundColor Yellow }
function Fail([string]$Message) { Write-Host "[openetqw] $Message" -ForegroundColor Red; exit 1 }
function Usage-Error([string]$Message) {
    Write-Host $Message -ForegroundColor Red
    Write-Host "Run: Get-Help .\scripts\build-windows.ps1 -Detailed"
    exit 2
}

# Print a command, then run it unless this is a dry run; a failing native
# command stops the script.
function Invoke-Step([string]$Exe, [string[]]$Arguments) {
    Write-Host "+ $Exe $($Arguments -join ' ')"
    if ($DryRun) { return }
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { Fail "$Exe exited with $LASTEXITCODE" }
}

if ($Help) { Get-Help $PSCommandPath -Detailed; exit 0 }
if ($args.Count -gt 0) { Usage-Error "Unknown option: $($args -join ' ')" }

$after = @($Test, $Run, $Server) | Where-Object { $_ } | Measure-Object | Select-Object -ExpandProperty Count
if ($after -gt 1) { Usage-Error "Choose at most one of -Test, -Run and -Server" }
if ($Map -and -not ($Run -or $Server)) { Usage-Error "-Map needs -Run or -Server" }
if ($Validate -and ($after -gt 0 -or $Clean -or $BuildType -or $BuildDir)) {
    Usage-Error "-Validate cannot be combined with build or launch options"
}
if ($NoDeps -and $InstallDeps) { Usage-Error "-NoDeps and -InstallDeps cannot be combined" }
if (-not $BuildType) { $BuildType = "debugoptimized" }
if (-not $BuildDir) { $BuildDir = "build" }

$EngineRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$GameUrl = "https://github.com/jm2/openETQW-game.git"
if (-not (Split-Path -IsAbsolute $BuildDir)) { $BuildDir = Join-Path $EngineRoot $BuildDir }

# The machine's architecture, from the system environment in the registry: the
# process's PROCESSOR_ARCHITECTURE says AMD64 in an x64 PowerShell emulated on an
# arm64 host. Off Windows (the dry-run test) ask uname.
$machine = ""
$sessionEnv = "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
if (Test-Path $sessionEnv) { $machine = (Get-ItemProperty $sessionEnv).PROCESSOR_ARCHITECTURE }
if (-not $machine) { $machine = if ($env:PROCESSOR_ARCHITECTURE) { $env:PROCESSOR_ARCHITECTURE } else { "$(uname -m)" } }
switch -Regex ($machine) {
    "^(ARM64|arm64|aarch64)$" { $Arch = "arm64" }
    "^(AMD64|x86_64)$" { $Arch = "x64" }
    default { Fail "Unsupported architecture: $machine" }
}
$Triplet = "$Arch-windows"
if (-not $VcpkgRoot) { $VcpkgRoot = if ($env:VCPKG_ROOT) { $env:VCPKG_ROOT } else { Join-Path $(if ($env:USERPROFILE) { $env:USERPROFILE } else { $HOME }) "vcpkg" } }
$VcpkgInstalled = Join-Path $VcpkgRoot "installed\$Triplet"
$VcpkgPackages = @("sdl3[vulkan]", "zlib", "vulkan-headers", "vulkan-loader", "pkgconf")

# A native command whose stderr is discarded; Windows PowerShell 5.1 turns
# redirected stderr into a terminating error while ErrorActionPreference is Stop.
function Invoke-Quiet([string]$Exe, [string[]]$Arguments) {
    $saved = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { $out = & $Exe @Arguments 2>$null } finally { $ErrorActionPreference = $saved }
    return $out
}

function Find-Python {
    foreach ($candidate in @("python", "py")) {
        if (Get-Command $candidate -ErrorAction SilentlyContinue) { return $candidate }
    }
    return $null
}

# `pip install --user meson ninja` puts them in Python's per-user Scripts
# directory, which is not on PATH by default; add it for this run.
function Add-PythonUserScripts {
    if (Get-Command meson -ErrorAction SilentlyContinue) { return }
    $py = Find-Python
    if (-not $py) { return }
    $dir = Invoke-Quiet $py @("-c", "import os, sysconfig; print(sysconfig.get_path('scripts', os.name + '_user'))")
    if ($dir -and (Test-Path (Join-Path "$dir" "meson.exe"))) {
        Write-Info "Using meson from Python's user Scripts directory: $dir"
        $env:PATH = "$dir;$env:PATH"
    }
}

function Test-Dependencies {
    if ($PSVersionTable.PSEdition -eq "Core" -and -not $IsWindows) { Fail "This is the Windows helper; use scripts/build-macos.sh or scripts/build-linux.sh" }
    $vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path $vswhere)) { Fail "Visual Studio not found. Install Visual Studio 2026 with the 'Desktop development with C++' workload" }
    $vs = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if ($Arch -eq "arm64") {
        $vs = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.ARM64 -property installationPath
    }
    if (-not $vs) { Fail "Visual Studio has no C++ tools for $Arch. Add the 'Desktop development with C++' workload (and the ARM64 build tools on arm64)" }
    Write-Info "Visual Studio: $vs"

    if (-not (Find-Python)) { Fail "Python 3 not found. Install it from https://www.python.org or: winget install Python.Python.3.14" }
    foreach ($tool in @("meson", "ninja")) {
        if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { Fail "$tool not found. Install: python -m pip install --user meson ninja" }
    }
    $glslang = Get-Command glslangValidator -ErrorAction SilentlyContinue
    if (-not $glslang -and $env:VULKAN_SDK) { $glslang = Test-Path (Join-Path $env:VULKAN_SDK "Bin\glslangValidator.exe") }
    if (-not $glslang) { Write-Warn "glslangValidator not found (the client compiles its shaders with it). Install the Vulkan SDK from https://vulkan.lunarg.com, or build it with tools\build\build_glslang.py" }

    $vcpkg = Join-Path $VcpkgRoot "vcpkg.exe"
    if (-not (Test-Path $vcpkg)) { Fail "vcpkg not found at $VcpkgRoot. Install: git clone https://github.com/microsoft/vcpkg $VcpkgRoot; $VcpkgRoot\bootstrap-vcpkg.bat (or pass -VcpkgRoot)" }
    $missing = @()
    foreach ($pc in @("sdl3", "zlib", "vulkan")) {
        if (-not (Test-Path (Join-Path $VcpkgInstalled "lib\pkgconfig\$pc.pc"))) { $missing += $pc }
    }
    if (-not (Test-Path (Join-Path $VcpkgInstalled "tools\pkgconf\pkgconf.exe"))) { $missing += "pkgconf" }
    if ($missing.Count -eq 0) { Write-Info "Dependencies: all vcpkg packages present ($Triplet)"; return }
    Write-Warn "Missing in vcpkg ($Triplet): $($missing -join ', ')"
    $installArgs = @("install") + $VcpkgPackages + @("--triplet", $Triplet)
    if ($InstallDeps) { Invoke-Step $vcpkg $installArgs }
    else { Fail "Install them with:  $vcpkg $($installArgs -join ' ')`n    (or rerun with -InstallDeps, which runs that command)" }
}

function Get-PinnedRef {
    $ref = (Get-Content (Join-Path $EngineRoot "tools\ci\companion.ref") -Raw).Trim()
    if ($ref -notmatch "^[0-9a-f]{40}$") { Fail "tools\ci\companion.ref does not hold a commit ID" }
    return $ref
}

function Resolve-GameRepo {
    $pin = Get-PinnedRef
    $repo = if ($env:OPENETQW_GAME_REPO) { $env:OPENETQW_GAME_REPO } else { Join-Path $EngineRoot "..\openETQW-game" }
    if (-not (Test-Path $repo)) {
        if ($env:OPENETQW_GAME_REPO) { Fail "OPENETQW_GAME_REPO is set to $repo, which does not exist" }
        Write-Info "The game repository is not at $repo; cloning it at the pinned commit $($pin.Substring(0, 12))"
        Invoke-Step git @("clone", $GameUrl, $repo)
        Invoke-Step git @("-C", $repo, "checkout", "--detach", $pin)
        if ($DryRun) { return $repo }
        return (Resolve-Path $repo).Path
    }
    $repo = (Resolve-Path $repo).Path
    if (-not (Test-Path (Join-Path $repo "meson.build"))) { Fail "$repo is not an openETQW-game checkout (no meson.build)" }
    $head = Invoke-Quiet git @("-C", $repo, "rev-parse", "HEAD")
    if ($LASTEXITCODE -ne 0) { Write-Warn "$repo is not a git checkout; not checking it against the pin"; return $repo }
    if ($head -eq $pin) { Write-Info "Game repository: $repo at the pinned commit $($pin.Substring(0, 12))"; return $repo }
    if ($SyncGame) {
        if (& git -C $repo status --porcelain --untracked-files=no) { Fail "-SyncGame: $repo has uncommitted changes; commit or stash them first" }
        Invoke-Quiet git @("-C", $repo, "cat-file", "-e", "$pin^{commit}") | Out-Null
        if ($LASTEXITCODE -ne 0) { Invoke-Step git @("-C", $repo, "fetch", "origin") }
        Invoke-Step git @("-C", $repo, "checkout", "--detach", $pin)
    } else {
        Write-Warn "Game repository $repo is at $($head.Substring(0, 12)), not the engine's pinned $($pin.Substring(0, 12))."
        Write-Warn "Building anyway; -SyncGame checks out the pinned commit."
    }
    return $repo
}

# Configure (or reconfigure) a meson build directory in MSVC's environment, then compile it.
function Invoke-MesonBuild([string]$Source, [string]$Build, [string[]]$Options) {
    if ($Clean -and (Test-Path $Build)) {
        Write-Host "+ Remove-Item -Recurse -Force $Build"
        if (-not $DryRun) { Remove-Item -Recurse -Force $Build }
    }
    $setup = @("setup")
    if (-not $Clean -and (Test-Path (Join-Path $Build "meson-private\coredata.dat"))) { $setup += "--reconfigure" }
    $setup += @($Build, $Source, "--vsenv", "--buildtype=$BuildType") + $Options
    Invoke-Step meson $setup
    Invoke-Step meson @("compile", "-C", $Build)
}

Add-PythonUserScripts

# the vcpkg environment, as the CI prepare action sets it (.github/actions/prepare); -Validate's builds need it too
$env:PKG_CONFIG = Join-Path $VcpkgInstalled "tools\pkgconf\pkgconf.exe"
$env:PKG_CONFIG_PATH = "$(Join-Path $VcpkgInstalled 'lib\pkgconfig');$(Join-Path $VcpkgInstalled 'share\pkgconfig')"
$env:CMAKE_PREFIX_PATH = $VcpkgInstalled

if ($Validate) {
    if (-not $NoDeps) { Test-Dependencies }
    # the validator builds whatever checkout it is given; check it against the pin as a build does
    $GameRepo = Resolve-GameRepo
    $py = Find-Python
    if (-not $py) { $py = "python" }
    Push-Location $EngineRoot
    try { Invoke-Step $py @("tools\validation\openetqw_validate.py", "pr", "--game-repo", $GameRepo) } finally { Pop-Location }
    exit 0
}

if (-not $NoDeps) { Test-Dependencies }

$GameRepo = Resolve-GameRepo

Write-Info "Building the game module and idLib ($BuildType)"
Invoke-MesonBuild $GameRepo (Join-Path $GameRepo "build") @()

Write-Info "Building the engine ($BuildType)"
# the engine finds the game through OPENETQW_GAME_REPO when it is not ..\openETQW-game
$env:OPENETQW_GAME_REPO = $GameRepo
Invoke-MesonBuild $EngineRoot $BuildDir @("-Dbuild_engine=true", "-Dbuild_dedicated=true", "-Dbuild_tests=true", "-Dcmake_prefix_path=$VcpkgInstalled")

$client = Join-Path $BuildDir "openETQW-client_$Arch.exe"
$ded = Join-Path $BuildDir "openETQW-ded_$Arch.exe"
Write-Info "Built:"
Write-Info "  client           $client"
Write-Info "  dedicated server $ded"
Write-Info "  game module      $(Join-Path $BuildDir "game_$Arch.dll") (copied by the engine build)"

$data = @()
if ($env:ETQW_DATA_PATH) { $data = @("+set", "fs_basepath", $env:ETQW_DATA_PATH) }
if ($Test) {
    Invoke-Step meson @("test", "-C", $BuildDir, "--suite", "unit", "--print-errorlogs")
} elseif ($Run) {
    # the client finds its Vulkan shaders (base\vkprogs) through the current directory
    Push-Location $EngineRoot
    try {
        if ($Map) { Invoke-Step $client ($data + @("+set", "fs_game", "base", "+set", "si_rules", "sdGameRulesObjective", "+devmap", $Map)) }
        else { Invoke-Step $client $data }
    } finally { Pop-Location }
} elseif ($Server) {
    if (-not $Map) { $Map = "valley" }
    Push-Location $EngineRoot
    try {
        Invoke-Step $ded (@("+set", "dedicated", "1") + $data + @("+set", "fs_game", "base", "+set", "si_rules", "sdGameRulesObjective", "+set", "si_pure", "0", "+spawnServer", $Map))
    } finally { Pop-Location }
}
exit 0
