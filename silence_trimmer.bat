@echo off
setlocal EnableDelayedExpansion
title Video Silence Trimmer

set "LAUNCH_ARGS=%*"
set "ROOT=%~dp0"
set "VENV=%ROOT%.venv_trimmer"
set "PIP=%VENV%\Scripts\pip.exe"
set "PY=%VENV%\Scripts\python.exe"
set "ACTIVATE=%VENV%\Scripts\activate.bat"
set "PKG=%ROOT%silence_trimmer"
set "LOCAL_FFMPEG_BIN=%ROOT%tools\ffmpeg\bin"
set "LOCAL_FFMPEG_EXE=%LOCAL_FFMPEG_BIN%\ffmpeg.exe"
set "LOCAL_FFPROBE_EXE=%LOCAL_FFMPEG_BIN%\ffprobe.exe"
set "SILERO_DIR=%ROOT%silero-vad"
set "SILERO_MARKER=%SILERO_DIR%\hubconf.py"
set "CORE_STAMP=%VENV%\.deps_core_ok"
set "SILERO_STAMP=%VENV%\.deps_silero_ok"
set "TAGGING_STAMP=%VENV%\.deps_tagging_ok"
set "CORE_DEPS=textual tqdm psutil"
set "SILERO_DEPS=torch torchaudio packaging numpy"
set "TAGGING_DEPS=faster-whisper scikit-learn"

echo.
echo  +======================================+
echo  :   Video Silence Trimmer              :
echo  +======================================+
echo.

call :phase 1 8 "System checks"

if not exist "%PKG%\__main__.py" (
    echo [FAIL] silence_trimmer/ folder missing or incomplete.
    echo        Expected: %PKG%\__main__.py
    goto :fail
)

set "SYS_PY="
where python >nul 2>&1 && set "SYS_PY=python"
if not defined SYS_PY where python3 >nul 2>&1 && set "SYS_PY=python3"
if not defined SYS_PY where py >nul 2>&1 && set "SYS_PY=py"
if not defined SYS_PY (
    echo [FAIL] Python not found on PATH.
    echo        Install Python 3.10+ from https://www.python.org/downloads/
    goto :fail
)

set "VCHECK=%TEMP%\_vcheck.py"
>"%VCHECK%" echo import sys
>>"%VCHECK%" echo v = sys.version_info
>>"%VCHECK%" echo tag = '%SYS_PY%'
>>"%VCHECK%" echo print('[  OK] Python %%d.%%d.%%d (%%s)' %% (v.major, v.minor, v.micro, tag))
>>"%VCHECK%" echo sys.exit(0 if v.major * 100 + v.minor >= 310 else 1)
%SYS_PY% "%VCHECK%" 2>nul
if %errorlevel% neq 0 (
    echo [FAIL] Python 3.10+ required.
    %SYS_PY% --version 2>nul
    del "%VCHECK%" >nul 2>&1
    goto :fail
)
del "%VCHECK%" >nul 2>&1

%SYS_PY% -c "import venv" >nul 2>&1
if %errorlevel% neq 0 (
    echo [FAIL] Python venv module missing.
    echo        Windows: reinstall Python with default options.
    echo        Linux:   sudo apt install python3-venv
    goto :fail
)

if not exist "%ACTIVATE%" goto :create_venv

"%PY%" -c "pass" >nul 2>&1
if %errorlevel% neq 0 goto :rebuild_venv

echo [  OK] venv intact
goto :deps

:create_venv
echo.
call :phase 2 8 "Creating virtual environment"
echo [SETUP] Creating virtual environment...
%SYS_PY% -m venv "%VENV%"
if %errorlevel% neq 0 (
    echo [FAIL] venv creation failed. Delete .venv_trimmer/ and retry.
    goto :fail
)
if exist "%CORE_STAMP%" del "%CORE_STAMP%"
if exist "%SILERO_STAMP%" del "%SILERO_STAMP%"
if exist "%TAGGING_STAMP%" del "%TAGGING_STAMP%"
echo [  OK] venv created
goto :deps

:rebuild_venv
call :phase 2 8 "Rebuilding virtual environment"
echo [WARN] venv corrupted. Rebuilding...
rmdir /s /q "%VENV%" >nul 2>&1
%SYS_PY% -m venv "%VENV%"
if %errorlevel% neq 0 (
    echo [FAIL] venv rebuild failed.
    goto :fail
)
if exist "%CORE_STAMP%" del "%CORE_STAMP%"
if exist "%SILERO_STAMP%" del "%SILERO_STAMP%"
if exist "%TAGGING_STAMP%" del "%TAGGING_STAMP%"
echo [  OK] venv rebuilt

:deps
if not exist "%CORE_STAMP%" goto :install_core
echo [  OK] Core dependencies (cached)
goto :silero_deps

:install_core
echo.
call :phase 3 8 "Installing core dependencies"
echo [SETUP] Installing core dependencies...
"%PIP%" install --quiet --upgrade pip >nul 2>&1
"%PIP%" install %CORE_DEPS%
if %errorlevel% neq 0 (
    echo [FAIL] Core dependency install failed. Check network.
    goto :fail
)
echo [  OK] Installed: %CORE_DEPS%
echo %date% %time%> "%CORE_STAMP%"

:ffmpeg_tools
if exist "%LOCAL_FFMPEG_EXE%" if exist "%LOCAL_FFPROBE_EXE%" goto :use_local_ffmpeg
where ffmpeg >nul 2>&1
if %errorlevel% equ 0 (
    where ffprobe >nul 2>&1
    if %errorlevel% equ 0 (
        echo [  OK] ffmpeg + ffprobe on PATH
        goto :silero_deps
    )
)

echo.
call :phase 4 8 "Provisioning ffmpeg"
echo [SETUP] Provisioning local ffmpeg...
"%PY%" -m silence_trimmer.setup_ffmpeg
if %errorlevel% neq 0 (
    echo [FAIL] ffmpeg provisioning failed.
    goto :fail
)
if not exist "%LOCAL_FFMPEG_EXE%" (
    echo [FAIL] Local ffmpeg setup did not produce %LOCAL_FFMPEG_EXE%
    goto :fail
)
if not exist "%LOCAL_FFPROBE_EXE%" (
    echo [FAIL] Local ffprobe setup did not produce %LOCAL_FFPROBE_EXE%
    goto :fail
)

:use_local_ffmpeg
set "PATH=%LOCAL_FFMPEG_BIN%;%PATH%"
echo [  OK] ffmpeg + ffprobe ready from %LOCAL_FFMPEG_BIN%

:silero_deps
if exist "%SILERO_STAMP%" (
    echo [  OK] Silero dependencies (cached)
    goto :silero_repo
)

echo.
call :phase 5 8 "Installing Silero dependencies"
echo [SETUP] Installing Silero dependencies...
"%PIP%" install %SILERO_DEPS%
if %errorlevel% neq 0 (
    echo [FAIL] Silero dependency install failed.
    echo        Tried: %SILERO_DEPS%
    goto :fail
)
echo [  OK] Installed: %SILERO_DEPS%
echo %date% %time%> "%SILERO_STAMP%"

:silero_repo
if exist "%SILERO_MARKER%" (
    echo [  OK] Silero repo (cached)
    goto :tagging_deps
)

echo.
call :phase 6 8 "Provisioning Silero repo"
echo [SETUP] Provisioning Silero VAD repo...
if exist "%SILERO_DIR%" rmdir /s /q "%SILERO_DIR%" >nul 2>&1
"%PY%" -m silence_trimmer.setup_silero
if %errorlevel% neq 0 (
    echo [FAIL] Silero repo provisioning failed.
    goto :fail
)
if not exist "%SILERO_MARKER%" (
    echo [FAIL] Silero repo setup did not produce hubconf.py
    goto :fail
)
echo [  OK] Silero repo ready: %SILERO_DIR%

:tagging_deps
if exist "%TAGGING_STAMP%" (
    echo [  OK] Tagging dependencies (cached)
    goto :smoke
)

echo.
call :phase 7 8 "Installing tagging dependencies"
echo [SETUP] Installing tagging dependencies...
"%PIP%" install %TAGGING_DEPS%
if %errorlevel% neq 0 (
    echo [FAIL] Tagging dependency install failed.
    echo        Tried: %TAGGING_DEPS%
    goto :fail
)
echo [  OK] Installed: %TAGGING_DEPS%
echo %date% %time%> "%TAGGING_STAMP%"

:smoke
"%PY%" -c "import textual; import tqdm" >nul 2>&1
if %errorlevel% neq 0 goto :repair_core

"%PY%" -c "import torch; import torchaudio; import packaging; import numpy" >nul 2>&1
if %errorlevel% neq 0 goto :repair_silero

"%PY%" -c "import faster_whisper; import sklearn" >nul 2>&1
if %errorlevel% equ 0 goto :launch
goto :repair_tagging

:repair_core
call :phase 3 8 "Repairing core dependencies"
echo [WARN] Core import check failed. Reinstalling...
if exist "%CORE_STAMP%" del "%CORE_STAMP%"
"%PIP%" install --force-reinstall %CORE_DEPS%
if %errorlevel% neq 0 (
    echo [FAIL] Core reinstall failed.
    goto :fail
)
echo %date% %time%> "%CORE_STAMP%"
echo [  OK] Core dependencies reinstalled
goto :smoke

:repair_silero
call :phase 5 8 "Repairing Silero dependencies"
echo [WARN] Silero import check failed. Reinstalling...
if exist "%SILERO_STAMP%" del "%SILERO_STAMP%"
"%PIP%" install --force-reinstall %SILERO_DEPS%
if %errorlevel% neq 0 (
    echo [FAIL] Silero reinstall failed.
    goto :fail
)
echo %date% %time%> "%SILERO_STAMP%"
echo [  OK] Silero dependencies reinstalled
goto :smoke

:repair_tagging
call :phase 7 8 "Repairing tagging dependencies"
echo [WARN] Tagging import check failed. Reinstalling...
if exist "%TAGGING_STAMP%" del "%TAGGING_STAMP%"
"%PIP%" install --force-reinstall %TAGGING_DEPS%
if %errorlevel% neq 0 (
    echo [FAIL] Tagging reinstall failed.
    goto :fail
)
echo %date% %time%> "%TAGGING_STAMP%"
echo [  OK] Tagging dependencies reinstalled
goto :smoke

:launch
if defined APP_ALREADY_LAUNCHED goto :eof
set "APP_ALREADY_LAUNCHED=1"
echo.
call :phase 8 8 "Launching app"
echo  Starting TUI...
echo  ---------------
echo.
echo  Auto-configured:
echo    Default backend:    ffmpeg
echo    ffmpeg bin dir:     %LOCAL_FFMPEG_BIN%
echo    Silero repo dir:    %SILERO_DIR%
echo    Allow downloads:    0
echo.

set "SILENCE_TRIMMER_DEFAULT_BACKEND=ffmpeg"
set "SILENCE_TRIMMER_ALLOW_MODEL_DOWNLOADS=0"
if exist "%LOCAL_FFMPEG_EXE%" (
    set "SILENCE_TRIMMER_FFMPEG_BIN_DIR=%LOCAL_FFMPEG_BIN%"
) else (
    set "SILENCE_TRIMMER_FFMPEG_BIN_DIR="
)
if exist "%SILERO_MARKER%" (
    set "SILENCE_TRIMMER_SILERO_REPO_DIR=%SILERO_DIR%"
) else (
    set "SILENCE_TRIMMER_SILERO_REPO_DIR="
)

"%PY%" -m silence_trimmer %LAUNCH_ARGS%
set "EXIT_CODE=%errorlevel%"

if %EXIT_CODE% neq 0 (
    echo.
    echo [EXIT] Exited with code %EXIT_CODE%.
    pause
)
goto :eof

:phase
setlocal
set "STEP=%~1"
set "TOTAL=%~2"
set "LABEL=%~3"
set /a FILLED=(STEP*20)/TOTAL
set "BAR="
for /l %%I in (1,1,20) do (
    if %%I LEQ !FILLED! (
        set "BAR=!BAR!#"
    ) else (
        set "BAR=!BAR!-"
    )
)
set /a PCT=(STEP*100)/TOTAL
echo [!BAR!] %PCT%%%  %LABEL%
endlocal
exit /b 0

:fail
echo.
echo  Fix the issue above and re-run.
echo.
pause
exit /b 1
