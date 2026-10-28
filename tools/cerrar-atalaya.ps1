# Cierra solo los procesos de Atalaya que impiden sobrescribir python.exe / mpv.exe / Atalaya.exe (ADR-137).
#
# Por RUTA: cualquier proceso que corra desde la carpeta instalada. Por NOMBRE: python.exe, mpv.exe y Atalaya.exe
# cuando Windows no deja leer su ruta (si la ruta se lee y es de otra carpeta NO se toca: puede ser el Python de otro
# programa de la persona). Después comprueba que ya no quedan; si queda alguno, lo escribe y sale con código 2 para
# que el instalador enseñe un mensaje claro en castellano en vez del diálogo crudo de NSIS.
param([Parameter(Mandatory = $true)][string]$InstallDir)
$ErrorActionPreference = 'SilentlyContinue'
$dir = $InstallDir.TrimEnd('\')
$nombres = @('python.exe', 'mpv.exe', 'Atalaya.exe')

function Get-MuDelAtalaya {
  Get-Process | Where-Object {
    $p = $_
    if ($p.Path) { return $p.Path.StartsWith($dir + '\', [System.StringComparison]::OrdinalIgnoreCase) }
    return $nombres -contains ($p.ProcessName + '.exe')
  }
}

foreach ($p in @(Get-MuDelAtalaya)) {
  try { Stop-Process -Id $p.Id -Force -ErrorAction Stop } catch { }
}
Start-Sleep -Milliseconds 900
$quedan = @(Get-MuDelAtalaya)
if ($quedan.Count -gt 0) {
  $lista = ($quedan | ForEach-Object { $_.ProcessName + ' (' + $_.Id + ')' }) -join ', '
  Write-Output ("Sigue en marcha: " + $lista)
  exit 2
}
exit 0
