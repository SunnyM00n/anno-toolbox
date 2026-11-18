@echo off
rem The link helper (README.md): version.dll with the live link, built with MSVC for x64.
rem   build.cmd [folder]   version.dll and version.map into the folder (default: out\ beside this file)
rem   build.cmd 1800 [folder] / build.cmd 2205 [folder]   the same for Anno 1800 or Anno 2205 (default: out\anno1800)
rem   build.cmd test       the host tests (no game needed; link\pipe_run.py needs Python 3.12 or later)
setlocal
cd /d "%~dp0"
where cl >nul 2>nul
if not errorlevel 1 goto msvc_ready
rem no compiler on the path: Visual Studio's own locator finds the newest install with the C++ tools
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist "%VSWHERE%" goto no_msvc
set "VSDIR="
for /f "usebackq delims=" %%i in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSDIR=%%i"
if not defined VSDIR goto no_msvc
call "%VSDIR%\VC\Auxiliary\Build\vcvars64.bat" >nul
where cl >nul 2>nul
if errorlevel 1 goto no_msvc
:msvc_ready
if "%~1"=="test" goto test

set "GAMEDEF="
set "GAMEDIR="
if "%~1"=="1800" set "GAMEDEF=/DHLIB_GAME_1800"
if "%~1"=="1800" set "GAMEDIR=\anno1800"
if "%~1"=="2205" set "GAMEDEF=/DHLIB_GAME_2205"
if "%~1"=="2205" set "GAMEDIR=\anno2205"
if defined GAMEDEF shift
if "%~1"=="117" shift
set "LOUT=%~1"
if "%LOUT%"=="" set "LOUT=%~dp0out%GAMEDIR%"
if not exist "%LOUT%\obj" mkdir "%LOUT%\obj"
ml64 /nologo /c /Fo"%LOUT%\obj\version_stubs.obj" framework\version_stubs.asm
if errorlevel 1 exit /b 1
rem /Brepro and a fixed stamp: the same sources and compiler give the same bytes (no time stamp, no __DATE__)
cl /nologo /I. /Iframework /Ilink /O2 /W3 /LD /Brepro %GAMEDEF% /DHLIB_BUILD_STAMP=\"link\" /Fo"%LOUT%\obj\\" ^
  framework\proxy.c framework\core.c framework\config.c framework\features_logic.c framework\session.c framework\rngsalt.c framework\hooks.c framework\layer.c framework\dispatch.c framework\engine.c framework\compwalk_logic.c framework\luaapi.c framework\script_api.c framework\persist.c framework\util.c framework\ledger.c framework\bus.c ^
  framework\tick.c framework\events.c framework\mods.c framework\registry.c framework\registry_logic.c framework\uiframe.c framework\notify.c framework\markers.c framework\luacmd.c framework\gamesetup.c framework\gamesetup_logic.c ^
  link\linkonly.c link\mod_livereload.c link\reload_logic.c link\mod_livelink.c link\livelink_reload.c link\livelink_requests.c link\livelink_logic.c link\stability_logic.c link\debug_logic.c link\modules_logic.c ^
  link\mod_livevalues.c link\livevalues_logic.c link\widgetread.c link\widgetread_logic.c ^
  "%LOUT%\obj\version_stubs.obj" /Fe:"%LOUT%\version.dll" /link /Brepro /DEF:version.def /MAP:"%LOUT%\version.map" /IMPLIB:"%LOUT%\obj\version.lib" user32.lib kernel32.lib advapi32.lib
set RC=%errorlevel%
rmdir /s /q "%LOUT%\obj" 2>nul
if not %RC%==0 (echo the link helper build failed with %RC% & exit /b 1)
echo the link helper: %LOUT%\version.dll
exit /b 0

:test
call :host script_test "framework\script_test.c framework\script_api.c framework\util.c" "" || exit /b 1
call :host hooks_test "framework\hooks_test.c" "/O2" || exit /b 1
call :host framework_test "framework\framework_test.c framework\features_logic.c framework\util.c" "" || exit /b 1
call :host reload_test "link\reload_test.c link\reload_logic.c" "/Ilink" || exit /b 1
call :host livelink_test "link\livelink_test.c link\livelink_logic.c" "/Ilink" || exit /b 1
call :host stability_test "link\stability_test.c link\stability_logic.c" "/Ilink" || exit /b 1
call :host debug_test "link\debug_test.c link\debug_logic.c" "/Ilink" || exit /b 1
call :host linkmodules_test "link\linkmodules_test.c link\modules_logic.c framework\features_logic.c" "/Ilink" || exit /b 1
call :host livevalues_test "link\livevalues_test.c link\livevalues_logic.c link\widgetread_logic.c" "/Ilink" || exit /b 1
rem the link's engine side with the engine stubbed, against the Toolbox's client over a real pipe
cl /nologo /I. /Iframework /Ilink /W3 /DRELOAD_START_WAIT_MS=1500 /DRELOAD_END_WAIT_MS=4000 /DLL_STALL_MIN_MS=200 /Fe:livelink_pipe_test.exe link\livelink_pipe_test.c link\livelink_logic.c link\livevalues_logic.c link\widgetread_logic.c link\stability_logic.c link\debug_logic.c link\modules_logic.c framework\features_logic.c user32.lib advapi32.lib
if errorlevel 1 exit /b 1
python link\pipe_run.py "%CD%\livelink_pipe_test.exe"
set RC=%errorlevel%
del /q *.obj livelink_pipe_test.exe 2>nul
if not %RC%==0 (echo pipe_run failed with %RC% & exit /b 1)
call :game_tests "" anno117 || exit /b 1
call :game_tests /DHLIB_GAME_1800 anno1800 || exit /b 1
call :game_tests /DHLIB_GAME_2205 anno2205 || exit /b 1
echo all host tests passed
exit /b 0

rem :game_tests <cl define> <game key>: the game header test of one edition and, for a game with no engine side,
rem the link run over a real pipe
:game_tests
cl /nologo /I. /Iframework /W3 %~1 /Fe:game_test.exe game_test.c
if errorlevel 1 exit /b 1
.\game_test.exe
set RC=%errorlevel%
del /q *.obj game_test.exe 2>nul
if not %RC%==0 (echo game_test failed with %RC% & exit /b 1)
if "%~1"=="" exit /b 0
cl /nologo /I. /Iframework /Ilink /W3 %~1 /DRELOAD_START_WAIT_MS=1500 /DRELOAD_END_WAIT_MS=4000 /DLL_STALL_MIN_MS=200 /Fe:livelink_pipe_%~2.exe link\livelink_pipe_test.c link\livelink_logic.c link\livevalues_logic.c link\widgetread_logic.c link\stability_logic.c link\debug_logic.c link\modules_logic.c framework\features_logic.c user32.lib advapi32.lib
if errorlevel 1 exit /b 1
python link\pipe_run.py "%CD%\livelink_pipe_%~2.exe" %~2
set RC=%errorlevel%
del /q *.obj livelink_pipe_%~2.exe 2>nul
if not %RC%==0 (echo pipe_run %~2 failed with %RC% & exit /b 1)
exit /b 0

rem :host name "sources" "flags": one host test built, run and removed
:host
cl /nologo /I. /Iframework /W3 %~3 /Fe:%1.exe %~2
if errorlevel 1 exit /b 1
.\%1.exe
set RC=%errorlevel%
del /q *.obj %1.exe 2>nul
if not %RC%==0 (echo %1 failed with %RC% & exit /b 1)
exit /b 0

:no_msvc
echo No MSVC found: install Visual Studio or its Build Tools with "Desktop development with C++", or run this from
echo an "x64 Native Tools Command Prompt".
exit /b 1
