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

function Get-MuBrandFolder {
  # %LOCALAPPDATA%\Atalaya (brand.json folder), como el launcher de Linux
  $folder = 'mpv-uos'
  try {
    $brand = Join-Path (Split-Path -Parent $PSScriptRoot) 'brand.json'
    if (Test-Path -LiteralPath $brand -PathType Leaf) {
      $b = Get-Content -LiteralPath $brand -Raw | ConvertFrom-Json
      if ($b.folder) { $folder = [string]$b.folder }
    }
  } catch { }
  return $folder
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
  $downloads = @(); $plain = @(); $links = @(); $join = ''
  foreach ($a in $Arguments) {
    # Edge y otros navegadores a veces añaden una barra: mpv-uos://open/?path=… . Se acepta igual.
    $a = [regex]::Replace($a, '^(?i)mpv-uos://(open|download)/\?', 'mpv-uos://$1?')
    if ($a.StartsWith('mpv-uos://download?')) { $downloads += $a; continue }
    # H44/C6 · un enlace de sala (…/s/<sala>#k=<token>) no se reproduce: se entra en ella. Igual que bin/mpv-uos.
    if ($join -eq '' -and $a -match '^https?://.+/s/[\w-]+#k=.') { $join = $a; continue }
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
  return @{ Downloads = $downloads; Arguments = $out; Join = $join }
}

function Get-MuExtraArguments {
  # Always stay open when idle (a file that fails to load must not close the player); without files/URLs open the
  # window like the Start menu shortcut does, unless the caller chose --idle or an operation mode.
  param([string[]]$Arguments, [string]$Join = '')
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
  if ($Join) { $extra += "--script-opts-append=mu-share-join=$Join" }
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
  # The interpreter with the mpvd package: the packaged runtime (runtime\python.exe, where the zip and the
  # installer put it since ADR-137), then the old .venv\Scripts of an already installed copy, else python from PATH.
  param([string]$Root)
  foreach ($c in @((Join-Path $Root 'runtime\python.exe'),
                   (Join-Path $Root '.venv\Scripts\python.exe'),
                   (Join-Path (Join-Path (Join-Path $Root '.venv') 'bin') 'python'))) {
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

# Every real launch leaves a line in %LOCALAPPDATA%\<brand folder>\lanzador.log: the mpv-uos:// handler runs hidden
# from the browser, and without this nothing tells what Windows actually passed or how mpv ended.
$LogFile = $null
if (-not $DryRun -and $env:LOCALAPPDATA) {
  $folder = 'Atalaya'
  try {
    $b = (Get-Content -Raw -LiteralPath (Join-Path $Root 'brand.json') | ConvertFrom-Json).folder
    if ($b) { $folder = [string]$b }
  } catch { }
  $LogFile = Join-Path (Join-Path $env:LOCALAPPDATA $folder) 'lanzador.log'
}
function Write-MuLog {
  param([string]$Text)
  if (-not $LogFile) { return }
  try {
    $dir = Split-Path -Parent $LogFile
    if (-not (Test-Path -LiteralPath $dir)) { $null = New-Item -ItemType Directory -Force -Path $dir }
    # shortcut: one old copy kept at 1 MB, enough to debug a handler; a real rotation if it ever matters
    if ((Test-Path -LiteralPath $LogFile) -and (Get-Item -LiteralPath $LogFile).Length -gt 1MB) {
      Move-Item -Force -LiteralPath $LogFile -Destination ($LogFile + '.1')
    }
    $line = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff') + " [$PID] " + $Text + [Environment]::NewLine
    [System.IO.File]::AppendAllText($LogFile, $line, (New-Object System.Text.UTF8Encoding($false)))
  } catch { }   # logging must never stop the player from opening
}
trap { Write-MuLog ('error: ' + $_ + ' @ ' + $_.InvocationInfo.PositionMessage); break }
Write-MuLog ('start gui=' + $Gui + ' args=' + (ConvertTo-Json -Compress -InputObject @($argv)))

# One pipe per instance, never inherited: a program opened from mpv would otherwise pass it on to the next mpv-uos.
Remove-Item Env:MPV_UOS_SOCKET -ErrorAction SilentlyContinue
$Pipe = Get-MuPipeName

# User data lives outside the checkout (a cleanup of .cache must never take favourites or notes with it).
$DataDir = Get-MuDataDir
$WatchLater = Join-Path $DataDir 'watch_later'
if (-not $DryRun) { $null = New-Item -ItemType Directory -Force -Path $WatchLater }
$env:MPV_UOS_ROOT = $Root
$env:MPV_UOS_DATA_DIR = $DataDir
# mpv hereda esto y mu-core lo usa para `python -m mpvd`: sin PYTHONPATH, el Python del paquete no
# encuentra el módulo y la tele, la grabación y todo lo del daemon dicen «mpvd no está instalado».
if ($env:PYTHONPATH) { $env:PYTHONPATH = $Root + [System.IO.Path]::PathSeparator + $env:PYTHONPATH }
else { $env:PYTHONPATH = $Root }
$exported = [ordered]@{ MPV_UOS_ROOT = $Root; MPV_UOS_DATA_DIR = $DataDir; PYTHONPATH = $env:PYTHONPATH }

# Canal de instalación: si esta copia la puso el instalador (.exe), actualizarse es bajar el instalador nuevo, no
# «git pull». Se marca con MPV_UOS_PACKAGED para que la comprobación de versión nueva (mpvd/updates.py) se encienda.
if (-not $env:MPV_UOS_PACKAGED) {
  $statePath = Join-Path $Root 'install.json'
  if (Test-Path -LiteralPath $statePath -PathType Leaf) {
    try {
      $st = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
      $prop = $st.PSObject.Properties['distro']
      if ($prop -and $prop.Value) {
        $env:MPV_UOS_PACKAGED = [string]$prop.Value
        $exported['MPV_UOS_PACKAGED'] = $env:MPV_UOS_PACKAGED
      }
    } catch { }
  }
}

$split = Split-MuArguments -Arguments $argv
$MpvBin = $env:MPV_UOS_MPV
if (-not $MpvBin) {
  # El mpv del sistema manda (se actualiza y trae los drivers de la máquina). Si no hay ninguno, la copia de reserva
  # que deja el instalador .exe en <app>\mpv\mpv.exe: así una instalación nueva funciona sin pedir nada más.
  $onPath = Get-Command -Name 'mpv' -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($onPath) { $MpvBin = $onPath.Path }
  elseif (Test-Path -LiteralPath (Join-Path (Join-Path $Root 'mpv') 'mpv.exe') -PathType Leaf) {
    $MpvBin = Join-Path (Join-Path $Root 'mpv') 'mpv.exe'
  } else {
    # El instalador de shinchiro (winget) deja mpv aquí sin tocar el PATH: si el .exe vio mpv y no copió
    # la reserva, el lanzador tiene que encontrarlo igual o diría «falta mpv» con mpv instalado.
    $candidatos = @()
    if ($env:ProgramFiles) { $candidatos += (Join-Path (Join-Path $env:ProgramFiles 'MPV Player') 'mpv.exe') }
    if (${env:ProgramFiles(x86)}) { $candidatos += (Join-Path (Join-Path ${env:ProgramFiles(x86)} 'MPV Player') 'mpv.exe') }
    if ($env:LOCALAPPDATA) { $candidatos += (Join-Path (Join-Path (Join-Path $env:LOCALAPPDATA 'Programs') 'MPV Player') 'mpv.exe') }
    $encontrado = $false
    foreach ($c in $candidatos) {
      if (Test-Path -LiteralPath $c -PathType Leaf) { $MpvBin = $c; $encontrado = $true; break }
    }
    if (-not $encontrado) { $MpvBin = 'mpv' }
  }
}

if ($split.Downloads.Count -gt 0) {
  # «Enviar a MPV-UOS» from the browser: mpvd (started if needed) queues the download; no player window opens.
  $py = Get-MuPython -Root $Root
  $cmd = @($py, '-m', 'mpvd', 'link', '--root', $Root) + $split.Downloads
  if ($DryRun) {
    [ordered]@{ mpv = $null; mpvd = $cmd; pipe = $null; env = $exported } | ConvertTo-Json -Compress -Depth 4
    exit 0
  }
  Write-MuLog ('mpvd ' + (ConvertTo-Json -Compress -InputObject @($cmd)))
  & $cmd[0] @($cmd[1..($cmd.Count - 1)])
  Write-MuLog "mpvd exit=$LASTEXITCODE"
  exit $LASTEXITCODE
}

Write-MuLog "mpv=$MpvBin"

# Arranque explícito del daemon: mu-core también lo intenta, pero si su subproceso falla en Windows
# (PATH, PYTHONPATH, permisos) la tele se queda en «starting» para siempre. Aquí se arranca antes,
# con el Python del paquete y dejando su stderr en un log que se puede leer.
if ((Test-OnWindows) -and $Gui -and -not $DryRun) {
  $py = Get-MuPython -Root $Root
  if ($py -ne 'python' -and (Test-Path -LiteralPath $py -PathType Leaf)) {
    $logDir = Join-Path $env:LOCALAPPDATA (Get-MuBrandFolder)
    $null = New-Item -ItemType Directory -Force -Path $logDir
    Write-MuLog "mpvd prearranque: $py"
    try {
      Start-Process -FilePath $py -ArgumentList @('-m', 'mpvd', 'serve') -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDir 'mpvd-serve.out.log') `
        -RedirectStandardError (Join-Path $logDir 'mpvd-serve.err.log') | Out-Null
      Start-Sleep -Milliseconds 900
    } catch {
      Write-MuLog ('mpvd prearranque falló: ' + $_)
    }
  }
}

$mpvArgs = @("--config-dir=$ConfigDir", "--input-ipc-server=$Pipe", "--watch-later-dir=$WatchLater")
$mpvArgs += Get-MuExtraArguments -Arguments $split.Arguments -Join $split.Join
$mpvArgs += $split.Arguments
if ($DryRun) {
  [ordered]@{ mpv = @($MpvBin) + $mpvArgs; mpvd = $null; pipe = $Pipe; env = $exported } | ConvertTo-Json -Compress -Depth 4
  exit 0
}

# mpv.exe es una aplicación de ventana: si falla al arrancar (driver, filtro, script) no deja rastro visible. Con
# --log-file el motivo queda escrito al lado del registro del lanzador, que es lo que hace diagnosticable «no se abre».
if (-not $DryRun -and $LogFile) { $mpvArgs += ('--log-file=' + [System.IO.Path]::ChangeExtension($LogFile, '.mpv.log')) }

$found = Get-Command -Name $MpvBin -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $found) {
  Write-MuLog "mpv not found: $MpvBin"
  Show-MuError -Gui $Gui -Message ("No encuentro mpv ($MpvBin). Instálalo (mpv >= 0.41) con «winget install --id shinchiro.mpv --exact» o " +
    "«scoop bucket add extras; scoop install mpv», o indica el ejecutable en MPV_UOS_MPV.")
  exit 1
}
# mpv.com (console wrapper) is waited for and keeps the terminal output; a GUI mpv.exe returns at once.
Write-MuLog ('mpv ' + (ConvertTo-Json -Compress -InputObject (@($found.Path) + $mpvArgs)))
$reintentado = $false
$arranque = Get-Date
& $found.Path @mpvArgs
$codigo = $LASTEXITCODE
$segundos = [int]((Get-Date) - $arranque).TotalSeconds
Write-MuLog "mpv exit=$codigo tras ${segundos}s"
# Arrancó y murió en segundos: casi siempre es el vídeo (driver, GPU, filtro) y, sin consola, no se ve nada. Se
# enseña el final del registro de mpv en un cuadro de diálogo: un fallo silencioso es un fallo que no se puede contar.
# Segundo intento, una sola vez: en máquinas sin aceleración 3D (máquinas virtuales, drivers viejos) el
# renderizador por defecto de mpv no llega a crear la ventana y muere en el sitio. `--vo=gpu` usa la ruta clásica.
# No se toca mpv.conf (ADR-130): el valor por defecto sigue siendo el de mpv, esto es solo una red debajo.
if ($codigo -ne 0 -and $segundos -lt 20 -and -not $reintentado) {
  $reintentado = $true
  Write-MuLog 'reintento con --vo=gpu'
  $arranque = Get-Date
  & $found.Path @mpvArgs '--vo=gpu'
  $codigo = $LASTEXITCODE
  $segundos = [int]((Get-Date) - $arranque).TotalSeconds
  Write-MuLog "mpv (--vo=gpu) exit=$codigo tras ${segundos}s"
  if ($codigo -eq 0) { exit 0 }
}
if ($Gui -and $codigo -ne 0 -and $segundos -lt 20) {
  $final = ''
  if ($LogFile) {
    $registro = [System.IO.Path]::ChangeExtension($LogFile, '.mpv.log')
    if (Test-Path -LiteralPath $registro) {
      $final = ((Get-Content -LiteralPath $registro -Tail 12 -ErrorAction SilentlyContinue) -join [Environment]::NewLine)
    }
  }
  Show-MuError -Gui $Gui -Message ("mpv se cerró nada más arrancar (código $codigo)." + [Environment]::NewLine +
    [Environment]::NewLine + $final)
}
if ($null -ne $codigo) { exit $codigo }
exit 0
