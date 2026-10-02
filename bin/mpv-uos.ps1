# MPV-UOS launcher for Windows: the PowerShell twin of bin/mpv-uos (same options, same environment for mu-core/mpvd).
# Launches mpv with the project's portable config dir and a unique IPC named pipe per instance.
# Usage: bin\mpv-uos.cmd [mpv options] [files/URLs]    (or: powershell -NoProfile -File bin\mpv-uos.ps1 ...)
# Env:   MPV_UOS_MPV (mpv binary, default "mpv" from PATH), MPV_UOS_DATA_DIR (user data: favourites, notes,
#        watch_later, prefs; default %APPDATA%\mpv-uos, the same folder mpvd uses on Windows).
# Launcher-only switches (removed before calling mpv):
#   -DryRun  print the mpv / mpvd command lines and the exported environment as one JSON line, run nothing
#   -Gui     report errors in a message box (the Start menu shortcut and mpv-uos:// links have no console)
# Works with Windows PowerShell 5.1 and PowerShell 7 (no 7-only syntax). No param() block on purpose: mpv options
# such as -v or --fs must reach mpv untouched instead of being bound (or prefix-matched) as script parameters.

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

function Test-OnWindows {
  return [System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT
}

function Get-MuPipeName {
  # mpv on Windows creates a named pipe for --input-ipc-server (man mpv, 0.41: "the path refers to the pipe namespace
  # (\\.\pipe\<name>)"). This PowerShell does not always outlive mpv (a GUI mpv.exe returns at once), so the PID alone
  # could be reused: a random suffix keeps two instances apart. mu-core attaches with whatever name mpv reports.
  param([int]$ProcessId = $PID)
  $rand = [System.Guid]::NewGuid().ToString('N').Substring(0, 8)
  return '\\.\pipe\mpv-uos-' + $ProcessId + '-' + $rand
}

function Get-MuDataDir {
  if ($env:MPV_UOS_DATA_DIR) { return $env:MPV_UOS_DATA_DIR }
  if ((Test-OnWindows) -and $env:APPDATA) { return (Join-Path $env:APPDATA 'mpv-uos') }
  # Only reached when running this script with pwsh on Linux/macOS (tests): same default as bin/mpv-uos.
  $base = $env:XDG_DATA_HOME
  if (-not $base) { $base = Join-Path (Join-Path $HOME '.local') 'share' }
  return (Join-Path $base 'mpv-uos')
}

function ConvertFrom-MuUrlComponent {
  param([string]$Text)
  return [System.Uri]::UnescapeDataString($Text.Replace('+', ' '))
}

function Split-MuArguments {
  # mpv-uos://download?url=... (browser, H23) -> Downloads; mpv-uos://open?path=...&t=... («Mis notas», H17) become
  # per-file groups (--{ --start=<t> <path> --}) placed before the first `--`, exactly like bin/mpv-uos.
  param([string[]]$Arguments)
  $downloads = @(); $plain = @(); $links = @()
  foreach ($a in $Arguments) {
    if ($a.StartsWith('mpv-uos://download?')) { $downloads += $a; continue }
    if ($a.StartsWith('mpv-uos://open?')) {
      $p = ''; $t = ''
      foreach ($kv in $a.Substring($a.IndexOf('?') + 1).Split('&')) {
        if ($kv.StartsWith('path=')) { $p = ConvertFrom-MuUrlComponent $kv.Substring(5) }
        elseif ($kv.StartsWith('t=')) { $t = $kv.Substring(2) }
      }
      if ($t -notmatch '^[0-9]+(\.[0-9]+)?$') { $t = '' }
      if ($p -and $t) { $links += @('--{', "--start=$t", $p, '--}') } elseif ($p) { $links += $p }
      continue
    }
    $plain += $a
  }
  $out = $plain
  if ($links.Count -gt 0) {
    $out = @(); $placed = $false
    foreach ($a in $plain) {
      if ($a -eq '--' -and -not $placed) { $out += $links; $placed = $true }
      $out += $a
    }
    if (-not $placed) { $out += $links }
  }
  return @{ Downloads = $downloads; Arguments = $out }
}

function Get-MuExtraArguments {
  # Always stay open when idle (a file that fails to load must not close the player); without files/URLs open the
  # window like the Start menu shortcut does, unless the caller chose --idle or an operation mode.
  param([string[]]$Arguments)
  $hasTarget = $false; $hasMode = $false; $hasIdle = $false; $afterDd = $false
  foreach ($a in $Arguments) {
    if ($afterDd) { $hasTarget = $true; continue }
    if ($a -eq '--') { $afterDd = $true }
    elseif ($a -eq '--idle' -or $a.StartsWith('--idle=') -or $a -eq '--no-idle') { $hasIdle = $true; $hasMode = $true }
    elseif ($a.StartsWith('--player-operation-mode=')) { $hasMode = $true }
    elseif ($a.StartsWith('-')) { }
    else { $hasTarget = $true }
  }
  $extra = @()
  if (-not $hasTarget -and -not $hasMode) { $extra += '--player-operation-mode=pseudo-gui' }
  if (-not $hasIdle) { $extra += '--idle=yes' }
  # H49 · el idioma, igual que en bin/mpv-uos y antes de que cargue ningún script. En Windows las variables de
  # POSIX no suelen estar, así que además se mira el idioma de la interfaz del sistema. Regla: castellano o francés
  # → ese; inglés o cualquier otro → inglés. MPV_UOS_LANG lo fuerza.
  $extra += @("--script-opts-append=mu-core-lang=$(Get-MuLanguage)",
              "--script-opts-append=uosc-languages=$(Get-MuLanguage),slang,en")
  return $extra
}

function Get-MuLanguage {
  $forced = $env:MPV_UOS_LANG
  if ($forced -in @('es', 'en', 'fr')) { return $forced }
  # La preferencia de *Preferencias → Idioma* gana al idioma del sistema (igual que en bin/mpv-uos)
  $prefs = Join-Path (Get-MuDataDir) 'prefs.json'
  if (Test-Path -LiteralPath $prefs) {
    try {
      $saved = (Get-Content -Raw -LiteralPath $prefs | ConvertFrom-Json).'mu-menu'.lang
      if ($saved -in @('es', 'en', 'fr')) { return $saved }
    } catch { }
  }
  $tags = @()
  foreach ($v in @($env:LC_ALL, $env:LC_MESSAGES, $env:LANG)) {
    if ($v -and $v -notin @('C', 'POSIX') -and -not $v.StartsWith('C.')) { $tags += $v }
  }
  try { $tags += (Get-UICulture).Name } catch { }
  foreach ($tag in $tags) {
    $code = ($tag -replace '^([A-Za-z]{2}).*$', '$1').ToLowerInvariant()
    if ($code -eq 'es' -or $code -eq 'fr') { return $code }
    if ($code.Length -eq 2) { return 'en' }
  }
  return 'en'
}

function Get-MuPython {
  # The interpreter with the mpvd package: the project's venv (Windows layout first), else python from PATH.
  param([string]$Root)
  foreach ($c in @((Join-Path $Root '.venv\Scripts\python.exe'), (Join-Path (Join-Path (Join-Path $Root '.venv') 'bin') 'python'))) {
    if (Test-Path -LiteralPath $c -PathType Leaf) { return $c }
  }
  return 'python'
}

function Show-MuError {
  param([string]$Message, [bool]$Gui)
  [Console]::Error.WriteLine($Message)
  if ($Gui -and (Test-OnWindows) -and -not $env:MPV_UOS_NO_NOTIFY) {
    try { $null = (New-Object -ComObject WScript.Shell).Popup($Message, 0, 'MPV-UOS', 16) } catch { }
  }
}

# ---------------------------------------------------------------------------------------------------------------------
if ($MyInvocation.InvocationName -eq '.') { return }   # dot-sourced (tests): functions only

$Root = Split-Path -Parent $PSScriptRoot
$ConfigDir = Join-Path $Root 'mpv-config'
$DryRun = $false; $Gui = $false
$argv = @()
foreach ($a in $args) {
  $s = [string]$a
  if ($s -eq '-DryRun') { $DryRun = $true } elseif ($s -eq '-Gui') { $Gui = $true } else { $argv += $s }
}

# One pipe per instance, never inherited: a program opened from mpv would otherwise pass it on to the next mpv-uos.
Remove-Item Env:MPV_UOS_SOCKET -ErrorAction SilentlyContinue
$Pipe = Get-MuPipeName

# User data lives outside the checkout (a cleanup of .cache must never take favourites or notes with it).
$DataDir = Get-MuDataDir
$WatchLater = Join-Path $DataDir 'watch_later'
if (-not $DryRun) { $null = New-Item -ItemType Directory -Force -Path $WatchLater }
$env:MPV_UOS_ROOT = $Root
$env:MPV_UOS_DATA_DIR = $DataDir
$exported = [ordered]@{ MPV_UOS_ROOT = $Root; MPV_UOS_DATA_DIR = $DataDir }

$split = Split-MuArguments -Arguments $argv
$MpvBin = $env:MPV_UOS_MPV
if (-not $MpvBin) { $MpvBin = 'mpv' }

if ($split.Downloads.Count -gt 0) {
  # «Enviar a MPV-UOS» from the browser: mpvd (started if needed) queues the download; no player window opens.
  $py = Get-MuPython -Root $Root
  $env:PYTHONPATH = if ($env:PYTHONPATH) { $Root + [System.IO.Path]::PathSeparator + $env:PYTHONPATH } else { $Root }
  $exported['PYTHONPATH'] = $env:PYTHONPATH
  $cmd = @($py, '-m', 'mpvd', 'link', '--root', $Root) + $split.Downloads
  if ($DryRun) {
    [ordered]@{ mpv = $null; mpvd = $cmd; pipe = $null; env = $exported } | ConvertTo-Json -Compress -Depth 4
    exit 0
  }
  & $cmd[0] @($cmd[1..($cmd.Count - 1)])
  exit $LASTEXITCODE
}

$mpvArgs = @("--config-dir=$ConfigDir", "--input-ipc-server=$Pipe", "--watch-later-dir=$WatchLater")
$mpvArgs += Get-MuExtraArguments -Arguments $split.Arguments
$mpvArgs += $split.Arguments
if ($DryRun) {
  [ordered]@{ mpv = @($MpvBin) + $mpvArgs; mpvd = $null; pipe = $Pipe; env = $exported } | ConvertTo-Json -Compress -Depth 4
  exit 0
}

$found = Get-Command -Name $MpvBin -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $found) {
  Show-MuError -Gui $Gui -Message ("No encuentro mpv ($MpvBin). Instálalo (mpv >= 0.41) con «winget install mpv» o " +
    "«scoop bucket add extras; scoop install mpv», o indica el ejecutable en MPV_UOS_MPV.")
  exit 1
}
# mpv.com (console wrapper) is waited for and keeps the terminal output; a GUI mpv.exe returns at once.
& $found.Path @mpvArgs
if ($null -ne $LASTEXITCODE) { exit $LASTEXITCODE }
exit 0
