@echo off
rem Atalaya para Windows: un solo doble clic. Llama a bin\empezar.ps1 saltandose la directiva de ejecucion
rem (-ExecutionPolicy Bypass vale solo para este proceso: no cambia ningun ajuste del equipo). Un .ps1 no se
rem ejecuta al hacer doble clic -- Windows lo abre en el editor -- asi que lo que se le da a una persona es esto.
rem ASCII a proposito: cmd.exe lee los .cmd en la pagina de codigos OEM y los acentos saldrian mal.
set "MARCA=%APPDATA%\mpv-uos\acceso_preguntado"
if not exist "%MARCA%" (
  echo.
  echo Quieres un acceso directo en el Escritorio y en el menu Inicio,
  echo y que los enlaces mpv-uos:// funcionen?
  echo Esta carpeta es la aplicacion: no la borres ni la muevas despues.
  choice /c SN /n /m "Crear los accesos (S/N)? "
  if not errorlevel 2 powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\install.ps1" -Shortcuts -Desktop
  if not exist "%APPDATA%\mpv-uos" mkdir "%APPDATA%\mpv-uos"
  echo preguntado> "%MARCA%"
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0bin\empezar.ps1" %*
if errorlevel 1 pause
