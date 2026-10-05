@echo off
rem Atalaya para Windows: un solo doble clic. Llama a bin\empezar.ps1 saltandose la directiva de ejecucion
rem (-ExecutionPolicy Bypass vale solo para este proceso: no cambia ningun ajuste del equipo). Un .ps1 no se
rem ejecuta al hacer doble clic -- Windows lo abre en el editor -- asi que lo que se le da a una persona es esto.
rem ASCII a proposito: cmd.exe lee los .cmd en la pagina de codigos OEM y los acentos saldrian mal.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0bin\empezar.ps1" %*
if errorlevel 1 pause
