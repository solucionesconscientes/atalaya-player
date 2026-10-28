# Cierra solo lo que corre desde la carpeta de Atalaya (mpv, python/mpvd, Atalaya.exe),
# para que el instalador pueda sobrescribir python312.dll sin el diálogo de «archivo en uso».
# No toca ningún otro programa del equipo.
param([Parameter(Mandatory = $true)][string]$InstallDir)
$ErrorActionPreference = 'SilentlyContinue'
$dir = $InstallDir.TrimEnd('\')
Get-Process | Where-Object {
  $_.Path -and $_.Path.StartsWith($dir + '\', [System.StringComparison]::OrdinalIgnoreCase)
} | ForEach-Object {
  try { Stop-Process -Id $_.Id -Force -ErrorAction Stop } catch { }
}
Start-Sleep -Milliseconds 700
exit 0
