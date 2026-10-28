/* Atalaya.exe: lanzador de subsistema GUI para Windows.
 *
 * Existe por tres motivos concretos:
 *  1. Un acceso directo o la casilla «Abrir Atalaya» del instalador no deben abrir una consola.
 *  2. Windows solo enseña una aplicación en «Abrir con» si hay un .exe real registrado en
 *     HKCU\Software\Classes\Applications.
 *  3. El handler mpv-uos:// deja de anunciarse como «Windows PowerShell».
 *
 * No hace nada por su cuenta: arranca bin\mpv-uos.ps1 -Gui con los argumentos que reciba,
 * oculto y sin ventana. Toda la lógica sigue en el .ps1, que es el camino probado.
 */
#define UNICODE
#define _UNICODE
#include <windows.h>
#include <stdio.h>
#include <wchar.h>

static void die(const wchar_t *msg) {
    MessageBoxW(NULL, msg, L"Atalaya Player", MB_OK | MB_ICONERROR);
}

/* Devuelve el resto de la línea de comandos después del nombre del ejecutable,
 * conservando las comillas tal cual las puso Windows/Explorer. */
static const wchar_t *tail_of_command_line(void) {
    const wchar_t *p = GetCommandLineW();
    while (*p == L' ' || *p == L'\t') p++;
    if (*p == L'"') {
        p++;
        while (*p && *p != L'"') p++;
        if (*p == L'"') p++;
    } else {
        while (*p && *p != L' ' && *p != L'\t') p++;
    }
    while (*p == L' ' || *p == L'\t') p++;
    return p;
}

int WINAPI WinMain(HINSTANCE hInstance, HINSTANCE hPrev, LPSTR cmd, int show) {
    (void)hInstance; (void)hPrev; (void)cmd; (void)show;

    wchar_t exe[MAX_PATH];
    DWORD n = GetModuleFileNameW(NULL, exe, MAX_PATH);
    if (n == 0 || n >= MAX_PATH) { die(L"No puedo localizar Atalaya.exe."); return 1; }

    wchar_t *slash = wcsrchr(exe, L'\\');
    if (!slash) { die(L"Ruta de instalación no válida."); return 1; }
    *slash = 0;                         /* exe = carpeta bin */

    wchar_t root[MAX_PATH];
    wcsncpy(root, exe, MAX_PATH);
    root[MAX_PATH - 1] = 0;
    wchar_t *last = wcsrchr(root, L'\\');   /* root = carpeta de la aplicación */
    if (last) *last = 0;

    wchar_t ps1[MAX_PATH * 2];
    _snwprintf(ps1, MAX_PATH * 2, L"%ls\\bin\\mpv-uos.ps1", root);
    if (GetFileAttributesW(ps1) == INVALID_FILE_ATTRIBUTES) {
        die(L"No encuentro bin\\mpv-uos.ps1. La instalación está incompleta.");
        return 1;
    }

    wchar_t sysroot[MAX_PATH];
    if (!GetEnvironmentVariableW(L"SystemRoot", sysroot, MAX_PATH)) {
        wcscpy(sysroot, L"C:\\Windows");
    }
    wchar_t powershell[MAX_PATH * 2];
    _snwprintf(powershell, MAX_PATH * 2,
               L"%ls\\System32\\WindowsPowerShell\\v1.0\\powershell.exe", sysroot);
    if (GetFileAttributesW(powershell) == INVALID_FILE_ATTRIBUTES) {
        die(L"No encuentro Windows PowerShell.");
        return 1;
    }

    const wchar_t *tail = tail_of_command_line();
    static wchar_t cmdline[32768];
    if (tail && *tail) {
        _snwprintf(cmdline, 32768,
                   L"\"%ls\" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"%ls\" -Gui %ls",
                   powershell, ps1, tail);
    } else {
        _snwprintf(cmdline, 32768,
                   L"\"%ls\" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"%ls\" -Gui",
                   powershell, ps1);
    }
    cmdline[32767] = 0;

    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    si.dwFlags = STARTF_USESHOWWINDOW;
    si.wShowWindow = SW_HIDE;
    ZeroMemory(&pi, sizeof(pi));

    DWORD flags = CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT;
    if (!CreateProcessW(powershell, cmdline, NULL, NULL, FALSE, flags, NULL, root, &si, &pi)) {
        die(L"No he podido arrancar el reproductor.");
        return 1;
    }
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return 0;
}
