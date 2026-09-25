@echo off
setlocal
cd /d "%~dp0"

set "TOOL_DIR=.zine-tool"
set "VENV_DIR=%TOOL_DIR%\venv"
set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
  echo Preparing the image and QR tools. This may take a few minutes.
  python -m venv "%VENV_DIR%"
  if errorlevel 1 goto :setup_error
)

"%PYTHON_EXE%" -c "import PIL, qrcode, zxingcpp" >nul 2>nul
if errorlevel 1 (
  echo Installing the image and QR tools. This may take a few minutes.
  "%PYTHON_EXE%" -m pip install --disable-pip-version-check -r requirements-zine-tool.txt
  if errorlevel 1 goto :setup_error
)

"%PYTHON_EXE%" tools\zine_register.py %*
set "RESULT=%ERRORLEVEL%"

if not "%RESULT%"=="0" (
  echo.
  echo Registration did not finish. Please review the message above.
  pause
)

exit /b %RESULT%

:setup_error
echo.
echo Setup failed. Please check the internet connection and Python installation.
pause
exit /b 1
