@echo off
setlocal
cd /d "%~dp0"

set "PY="
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY (
  where py >nul 2>nul && set "PY=py -3"
)
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo.
  echo PrintPal needs Python 3.10 or newer.
  echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
  echo.
  pause
  exit /b 1
)

%PY% -m printpal %*
if errorlevel 1 (
  echo.
  echo PrintPal exited with an error.
  pause
)
