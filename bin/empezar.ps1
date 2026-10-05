# Atalaya · «Empezar aquí» para Windows: toda la cadena en un doble clic (ADR-123).
# Finds mpv wherever it is, installs it with winget when it is missing, and hands bin\mpv-uos.ps1 the exact mpv.exe
# through MPV_UOS_MPV instead of trusting the PATH: a console that has just installed something with winget does NOT
# see the new PATH, so the naive version would install mpv and fail anyway -- the worst of the two outcomes.
# Usage: double click EMPEZAR-AQUI.cmd at the top of the folder, or drop files on it.
#        (or: powershell -NoProfile -ExecutionPolicy Bypass -File bin\empezar.ps1 [mpv options] [files])
# Env:   MPV_UOS_MPV  an mpv already chosen: used as is and nothing is installed
#        MU_WINGET    the winget binary (the tests point this at a fake one)
# Switch: -DryRun  do everything except launching the player, and print what was found as one JSON line
# This file is UTF-8 WITH BOM on purpose: Windows PowerShell 5.1 reads a BOM-less file as ANSI and the accents in
# these messages would reach the person as mojibake (tests/test_windows_scripts.py watches it).

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

function Test-OnWindows {
  return [System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT
}

function Join-MuEnvPath {
  # <env var> + path parts, or $null when the variable is not set. One Join-Path per part and not a single
  # 'a\b\c' string: that way the same code builds real paths under the tests, which run on Linux.
  param([string]$Variable, [string[]]$Parts)
  $base = [System.Environment]::GetEnvironmentVariable($Variable)
  if ([string]::IsNullOrWhiteSpace($base)) { return $null }
  $path = $base
  foreach ($part in $Parts) { $path = Join-Path $path $part }
  return $path
}

function Get-MuMpvCandidates {
  # Where winget, scoop, choco and the plain installers leave mpv. The PATH is the normal answer; this list is for
  # the minutes right after an install, when the PATH of THIS console is still the old one.
  $out = @()
  foreach ($p in @(
    (Join-MuEnvPath 'LOCALAPPDATA' @('Microsoft', 'WinGet', 'Links', 'mpv.exe')),
    (Join-MuEnvPath 'LOCALAPPDATA' @('Programs', 'mpv', 'mpv.exe')),
    (Join-MuEnvPath 'USERPROFILE'  @('scoop', 'shims', 'mpv.exe')),
    (Join-MuEnvPath 'USERPROFILE'  @('scoop', 'apps', 'mpv', 'current', 'mpv.exe')),
    (Join-MuEnvPath 'ProgramData'  @('chocolatey', 'bin', 'mpv.exe')),
    (Join-MuEnvPath 'ProgramFiles' @('mpv', 'mpv.exe')),
    (Join-MuEnvPath 'ProgramW6432' @('mpv', 'mpv.exe'))
  )) {
    if ($p) { $out += $p }
  }
  return $out
}

function Find-MuMpvInWingetPackages {
  # winget's portable packages land in a folder whose name carries the version, so it has to be looked up
  $dir = Join-MuEnvPath 'LOCALAPPDATA' @('Microsoft', 'WinGet', 'Packages')
  if (-not $dir -or -not (Test-Path -LiteralPath $dir)) { return $null }
  $hit = Get-ChildItem -LiteralPath $dir -Filter 'mpv.exe' -Recurse -Depth 3 -File -ErrorAction SilentlyContinue |
         Select-Object -First 1
  if ($hit) { return $hit.FullName }
  return $null
}

function Update-MuPathFromRegistry {
  # What makes the difference after installing: the PATH of this process is a copy made when it started.
  if (-not (Test-OnWindows)) { return }
  $parts = @()
  foreach ($key in @('HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment', 'HKCU:\Environment')) {
    try {
      $value = (Get-ItemProperty -LiteralPath $key -Name 'Path' -ErrorAction Stop).Path
      if ($value) { $parts += $value }
    } catch {
      # a PATH we cannot read is not a reason to stop: the candidate list is still there
    }
  }
  if ($parts.Count -gt 0) { $env:PATH = ($parts -join ';') + ';' + $env:PATH }
}

function Find-MuMpv {
  # Returns @{ ruta; origen } or $null. The order is «what the person chose, what the PATH says, where it is usually».
  if (-not [string]::IsNullOrWhiteSpace($env:MPV_UOS_MPV)) {
    $chosen = Get-Command -Name $env:MPV_UOS_MPV -CommandType Application -ErrorAction SilentlyContinue |
              Select-Object -First 1
    if ($chosen) { return @{ ruta = $chosen.Path; origen = 'MPV_UOS_MPV' } }
    if (Test-Path -LiteralPath $env:MPV_UOS_MPV -PathType Leaf) {
      return @{ ruta = $env:MPV_UOS_MPV; origen = 'MPV_UOS_MPV' }
    }
  }
  $onPath = Get-Command -Name 'mpv' -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($onPath) { return @{ ruta = $onPath.Path; origen = 'PATH' } }
  foreach ($candidate in Get-MuMpvCandidates) {
    if (Test-Path -LiteralPath $candidate -PathType Leaf) { return @{ ruta = $candidate; origen = 'instalado' } }
  }
  $packaged = Find-MuMpvInWingetPackages
  if ($packaged) { return @{ ruta = $packaged; origen = 'winget' } }
  return $null
}

function Get-MuWinget {
  if (-not [string]::IsNullOrWhiteSpace($env:MU_WINGET)) {
    if (Test-Path -LiteralPath $env:MU_WINGET -PathType Leaf) { return $env:MU_WINGET }
  }
  $found = Get-Command -Name 'winget' -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($found) { return $found.Path }
  return $null
}

function Install-MuMpv {
  # «mpv» is the catalogue moniker, which is what mpv.io itself documents. If some day it stops resolving to one
  # package, winget asks instead of choosing, and that is why the failure below has to say what to do by hand.
  param([string]$Winget)
  Write-Host 'Te falta mpv, que es el reproductor que Atalaya usa por debajo. Lo instalo con winget; tarda un minuto.'
  & $Winget @('install', 'mpv', '--accept-package-agreements', '--accept-source-agreements')
  $code = $LASTEXITCODE
  if ($null -ne $code -and $code -ne 0) {
    Write-Host ''
    Write-Host ("winget ha devuelto $code y no ha instalado mpv.")
    return $false
  }
  return $true
}

function Show-MuMissingMpv {
  $lines = @(
    '',
    'No encuentro mpv y no he podido instalarlo solo.',
    '',
    'Instálalo a mano, con una de estas tres (la primera es la fácil):',
    '    winget install mpv',
    '    scoop bucket add extras; scoop install mpv',
    '    o bájalo de https://mpv.io/installation/ y deja mpv.exe en el PATH',
    '',
    'Hace falta mpv 0.41 o más nuevo. Cuando lo tengas, vuelve a hacer doble clic aquí.'
  )
  [Console]::Error.WriteLine(($lines -join [System.Environment]::NewLine))
}

$DryRun = $false
$Rest = @()
foreach ($a in $Args) {
  if ($a -is [string] -and $a -ieq '-DryRun') { $DryRun = $true; continue }
  $Rest += $a
}

$Root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$Launcher = Join-Path (Join-Path $Root 'bin') 'mpv-uos.ps1'
$installed = $false

$mpv = Find-MuMpv
if (-not $mpv) {
  $winget = Get-MuWinget
  if ($winget) {
    $installed = Install-MuMpv -Winget $winget
    if ($installed) {
      Update-MuPathFromRegistry
      $mpv = Find-MuMpv
    }
  }
}

if (-not $mpv) {
  Show-MuMissingMpv
  exit 1
}

# The launcher gets the exact file, not a name to look up: this is the whole point of this script
$env:MPV_UOS_MPV = $mpv.ruta

if ($DryRun) {
  [ordered]@{ mpv = $mpv.ruta; origen = $mpv.origen; instalado = $installed; lanzador = $Launcher
              argumentos = @($Rest) } | ConvertTo-Json -Compress -Depth 4
  exit 0
}

& $Launcher @Rest
# Get-Variable and not $LASTEXITCODE: under Set-StrictMode an automatic variable nobody has set yet throws, and
# bin\mpv-uos.ps1 always ends in `exit`, so this line only runs if some day it stops doing that
$code = Get-Variable -Name 'LASTEXITCODE' -ValueOnly -ErrorAction SilentlyContinue
if ($null -ne $code) { exit $code }
exit 0
