; Atalaya Player · instalador para Windows (H75/F5)
;
; Por qué existe: el zip portable trae EMPEZAR-AQUI.cmd, y un .cmd descargado de internet lleva la marca de
; Internet, así que el Control Inteligente de Aplicaciones (SAC) lo bloquea con el recuadro azul y «Aceptable»
; no siempre lo desbloquea. Instalado con este .exe, los ficheros no llevan esa marca y el .cmd arranca sin
; recuadros: el único aviso posible es el de SmartScreen al descargar el instalador (no está firmado).
;
; Instalación POR USUARIO (sin UAC): %LOCALAPPDATA%\Programs\Atalaya. Los accesos directos, el lanzador y los
; enlaces mpv-uos:// los crea el propio proyecto (tools\install.ps1 -Shortcuts), que es el camino ya probado.
;
; Compilar: tools/build_win_installer.sh  (makensis + el zip de dist/)

Unicode true

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"

!ifndef VERSION
  !define VERSION "0.1.0"
!endif
!ifndef SRCDIR
  !error "Falta /DSRCDIR (la carpeta Atalaya del zip portable)"
!endif
!ifndef OUTFILE
  !define OUTFILE "Atalaya-${VERSION}-setup.exe"
!endif

!define APP_NAME "Atalaya Player"
!define APP_PUBLISHER "Soluciones Conscientes"
!define APP_URL "https://solucionesconscientes.es/atalaya"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\Atalaya"

Name "${APP_NAME} ${VERSION}"
OutFile "${OUTFILE}"
InstallDir "$LOCALAPPDATA\Programs\Atalaya"
InstallDirRegKey HKCU "${UNINST_KEY}" "InstallLocation"
RequestExecutionLevel user
SetCompressor /SOLID lzma

VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "${APP_NAME}"
VIAddVersionKey "FileDescription" "Instalador de ${APP_NAME}"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "CompanyName" "${APP_PUBLISHER}"
VIAddVersionKey "LegalCopyright" "${APP_PUBLISHER}"
VIAddVersionKey "ProductURL" "${APP_URL}"

!define MUI_ABORTWARNING
!ifdef ICON
  !define MUI_ICON "${ICON}"
  !define MUI_UNICON "${ICON}"
!endif
!define MUI_WELCOMEPAGE_TITLE "Instalar ${APP_NAME}"
!define MUI_WELCOMEPAGE_TEXT "Se instala solo para tu usuario, en tu carpeta personal: no pide contraseña de \
administrador ni cambia nada del sistema.$\r$\n$\r$\n\
Si Windows te ha enseñado un aviso azul antes de abrir este instalador («Windows protegió tu PC»), es porque el \
instalador no está firmado: se pasa con «Más información» y luego «Ejecutar de todas formas». Dentro de la \
aplicación no vuelve a salir, que es justo lo que se arregla con este instalador.$\r$\n$\r$\n\
Para reproducir vídeo hace falta mpv; si no lo tienes, al abrir la aplicación te dirá cómo instalarlo."
!define MUI_FINISHPAGE_RUN ""
!define MUI_FINISHPAGE_RUN_FUNCTION "AbrirAtalaya"
!define MUI_FINISHPAGE_RUN_TEXT "Abrir ${APP_NAME} ahora"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "Spanish"

; La casilla final abre lo mismo que el atajo del menú Inicio: PowerShell oculto y el script con -Gui, así no
; aparece ninguna ventana de consola. Antes ejecutaba bin\mpv-uos.cmd y por eso salía un cmd.
Function AbrirAtalaya
  ; Atalaya.exe es un lanzador de subsistema GUI: arranca PowerShell y mpv sin crear ninguna consola.
  Exec '"$INSTDIR\bin\Atalaya.exe"'
FunctionEnd

Section "Atalaya Player" SecMain
  SectionIn RO
  ; ADR-137 · Si la versión anterior está abierta, python.exe está cargado y NSIS no puede sobrescribirlo (el síntoma
  ; era «Error abriendo archivo para escritura: …\AppData\Local\Programs\Atalaya\.venv\Scripts\python.exe»). Se cierra
  ; lo que corre desde ESTA carpeta, por ruta y por nombre, y se comprueba que ya no queda nada antes de seguir.
  InitPluginsDir
  SetOutPath "$PLUGINSDIR"
  File "${__FILEDIR__}\cerrar-atalaya.ps1"
  DetailPrint "Cerrando una instalación anterior de Atalaya si está abierta…"
  nsExec::ExecToStack 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$PLUGINSDIR\cerrar-atalaya.ps1" -InstallDir "$INSTDIR"'
  Pop $0
  Pop $1
  ${If} $0 != 0
    MessageBox MB_OK|MB_ICONSTOP "No se ha podido cerrar Atalaya Player, así que el instalador no puede reemplazar sus archivos.$\r$\n$\r$\n$1$\r$\n$\r$\nCierra el reproductor (o el daemon) y vuelve a ejecutar este instalador."
    Abort
  ${EndIf}

  ; El intérprete vive en runtime\ (antes en .venv\Scripts). Se borra antes de extraer para que no queden restos de
  ; la versión anterior; si Windows no deja, se dice en castellano en vez de dejar que falle NSIS a medias.
  SetOverwrite on
  ; En una instalación nueva esa carpeta no existe y `RMDir /r` deja el indicador de error puesto aunque no haya
  ; nada que borrar: sin el `IfFileExists`, el instalador se abortaba solo (código 2) antes de copiar nada. Solo
  ; se comprueba el error si de verdad había algo que borrar.
  ${If} ${FileExists} "$INSTDIR\runtime\*.*"
    ClearErrors
    RMDir /r "$INSTDIR\runtime"
    ${If} ${Errors}
      MessageBox MB_OK|MB_ICONSTOP "No se ha podido reemplazar el intérprete de Atalaya ($INSTDIR\runtime).$\r$\n$\r$\nCierra cualquier programa que lo esté usando y vuelve a ejecutar el instalador."
      Abort
    ${EndIf}
  ${EndIf}
  ${If} ${FileExists} "$INSTDIR\.venv\*.*"
    ClearErrors
    RMDir /r "$INSTDIR\.venv"
    ${If} ${Errors}
      MessageBox MB_OK|MB_ICONSTOP "No se ha podido borrar el intérprete anterior de Atalaya ($INSTDIR\.venv).$\r$\n$\r$\nCierra cualquier programa que lo esté usando y vuelve a ejecutar el instalador."
      Abort
    ${EndIf}
  ${EndIf}

  SetOutPath "$INSTDIR"
  RMDir /r "$INSTDIR\mpv-config"
  File /r "${SRCDIR}\*.*"

  ; mpv: el del sistema manda (se actualiza y trae los drivers de la máquina). Solo si no hay ninguno se descomprime
  ; la copia de reserva que viaja en este instalador, para que la aplicación funcione sin pedir nada más.
  nsExec::ExecToStack 'where mpv.exe'
  Pop $R0
  Pop $R1
  StrCmp $R0 "0" atalaya_mpv_ready
  IfFileExists "$PROGRAMFILES64\MPV Player\mpv.exe" atalaya_mpv_ready
  IfFileExists "$PROGRAMFILES\MPV Player\mpv.exe" atalaya_mpv_ready
    DetailPrint "No hay mpv en el equipo: se instala una copia propia (no toca el sistema)."
    SetOutPath "$INSTDIR\mpv"
!ifdef MPVDIR
    File "${MPVDIR}\mpv.exe"
    File "${MPVDIR}\mpv.com"
    File "${MPVDIR}\d3dcompiler_43.dll"
    File "${MPVDIR}\mpv\fonts.conf"
!endif
    SetOutPath "$INSTDIR"
    Goto atalaya_mpv_done
  atalaya_mpv_ready:
    DetailPrint "mpv ya está en el equipo: se usa ese y no se instala otra copia."
  atalaya_mpv_done:

  ; Accesos directos, lanzador y enlaces mpv-uos:// con el script del propio proyecto (el mismo del zip), que es
  ; el camino ya probado. Todo lo que dice queda en instalador.log: si algo falla, se ve el motivo exacto.
  DetailPrint "Creando accesos directos y enlaces mpv-uos://…"
  StrCpy $R3 ""
  IfFileExists "$INSTDIR\mpv\mpv.exe" 0 atalaya_sin_icono
    StrCpy $R3 '-IconPath "$INSTDIR\mpv\mpv.exe"'
  atalaya_sin_icono:
  Delete "$INSTDIR\instalador.log"
  nsExec::ExecToLog 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\tools\install.ps1" -Shortcuts -Desktop -InstallDir "$INSTDIR" -Log "$INSTDIR\instalador.log" -Distro exe $R3'
  Pop $0
  ${If} $0 != 0
    MessageBox MB_YESNO|MB_ICONEXCLAMATION "Los archivos están instalados, pero los accesos directos no se han podido crear (código $0). Puedes abrir la aplicación con EMPEZAR-AQUI.cmd.$\r$\n$\r$\n¿Quieres ver el detalle?" IDNO atalaya_sin_log
      ExecShell "open" "$INSTDIR\instalador.log"
    atalaya_sin_log:
  ${EndIf}

  WriteUninstaller "$INSTDIR\Desinstalar ${APP_NAME}.exe"

  WriteRegStr HKCU "${UNINST_KEY}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINST_KEY}" "Publisher" "${APP_PUBLISHER}"
  WriteRegStr HKCU "${UNINST_KEY}" "URLInfoAbout" "${APP_URL}"
  WriteRegStr HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\Desinstalar ${APP_NAME}.exe"'
  WriteRegStr HKCU "${UNINST_KEY}" "QuietUninstallString" '"$INSTDIR\Desinstalar ${APP_NAME}.exe" /S'
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "EstimatedSize" 0
SectionEnd

Section "Uninstall"
  ; Cierra el reproductor y el daemon antes de borrar la carpeta.
  InitPluginsDir
  SetOutPath "$PLUGINSDIR"
  File "${__FILEDIR__}\cerrar-atalaya.ps1"
  nsExec::ExecToLog 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$PLUGINSDIR\cerrar-atalaya.ps1" -InstallDir "$INSTDIR"'
  Pop $0
  ; Quita el lanzador, los accesos directos y los enlaces mpv-uos:// (lo que el proyecto registró).
  nsExec::ExecToLog 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\tools\install.ps1" -Uninstall -InstallDir "$INSTDIR"'
  Pop $0
  ; Los datos del usuario (%APPDATA%\mpv-uos: descargas, historial, ajustes) NO se tocan.
  RMDir /r "$INSTDIR"
  DeleteRegKey HKCU "${UNINST_KEY}"
SectionEnd
