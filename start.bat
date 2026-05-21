@echo off
cd /d "%~dp0"
title Printer Monitor - Starting...

:: ============================================================
::   Multi-Brand Printer Monitor v3  -  Start Script
:: ============================================================

echo.
echo  ============================================================
echo   Multi-Brand Printer Monitor v3
echo  ============================================================
echo.

:: --- Step 1: Check Python --------------------------------
echo [1/5] Checking Python installation...
python --version > nul 2>&1
if %errorlevel% neq 0 (
    echo  [ERROR] Python not found.
    echo  Install Python 3.10+ from https://www.python.org/downloads/
    echo  Check 'Add Python to PATH' during installation.
    pause
    exit /b 1
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PY_VER=%%v
for /f "tokens=1,2 delims=." %%a in ("%PY_VER%") do (
    set PY_MAJOR=%%a
    set PY_MINOR=%%b
)
if %PY_MAJOR% LSS 3 goto :python_old
if %PY_MAJOR% EQU 3 if %PY_MINOR% LSS 10 goto :python_old
echo  [OK] Python %PY_VER% (3.10+ required)
goto :step2

:python_old
echo  [ERROR] Python %PY_VER% is too old. Requires 3.10+
pause
exit /b 1

:: --- Step 2: Clean cache ---------------------------------
:step2
echo [2/5] Cleaning Python cache...
for /d /r . %%d in (__pycache__) do (
    if exist "%%d" rd /s /q "%%d" 2>nul
)
del /s /q *.pyc 2>nul
echo  [OK] Cache cleared

:: --- Step 3: Install dependencies -----------------------
echo [3/5] Checking dependencies...
if not exist "requirements.txt" (
    echo  [SKIP] requirements.txt not found
    goto :step4
)
python -m pip install -r requirements.txt -q --disable-pip-version-check
if errorlevel 1 (
    echo  [WARN] Some packages may have failed. Run manually if needed:
    echo         pip install -r requirements.txt
) else (
    echo  [OK] Dependencies ready
)

:: --- Step 4: Check project files ------------------------
:step4
echo [4/5] Checking project files...
if not exist "run.py" (
    echo  [ERROR] run.py not found. Run this script from the project root folder.
    pause
    exit /b 1
)
echo  [OK] run.py
if exist "printers.json"   (echo  [OK] printers.json)   else (echo  [INFO] printers.json    - will be created on first run)
if exist "logs.db"         (echo  [OK] logs.db)         else (echo  [INFO] logs.db          - will be created on first run)
if exist "oid_profiles.json" (echo  [OK] oid_profiles.json) else (echo  [INFO] oid_profiles.json - will be created after first scan)

:: --- Step 5: Choose port --------------------------------
echo [5/5] Port configuration...
echo.
echo  Default port: 5053
echo  Press ENTER to keep default, or type a custom port (1024-65535):
echo.
set /p USER_PORT="  Port [5053]: "
if "%USER_PORT%"=="" set USER_PORT=5053

:: Validate: must be numeric
set PORT_VALID=1
for /f "delims=0123456789" %%i in ("%USER_PORT%") do set PORT_VALID=0
if "%PORT_VALID%"=="0" (
    echo  [WARN] Invalid input - using default port 5053
    set USER_PORT=5053
)

:: Patch FLASK_PORT in settings.py if changed
if not "%USER_PORT%"=="5053" (
    python -c "import re; path='config/settings.py'; c=open(path).read(); open(path,'w').write(re.sub(r'FLASK_PORT\s*=\s*\d+','FLASK_PORT = %USER_PORT%',c))" 2>nul
    echo  [OK] Port set to %USER_PORT%
) else (
    echo  [OK] Using default port 5053
)

:: --- Launch ---------------------------------------------
echo.
echo  ============================================================
echo   Launching on http://localhost:%USER_PORT%/
echo   Press Ctrl+C to stop the server
echo  ============================================================
echo.

python run.py

:: --- Stopped --------------------------------------------
echo.
echo  Printer Monitor stopped.
pause
