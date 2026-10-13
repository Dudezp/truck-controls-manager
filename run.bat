@echo off
cd /d "%~dp0"
title Truck Controls Manager

:: 1. Спроба через прямі шляхи Python на цій машині
if exist "%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe" (
    "%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe" app.py
    if errorlevel 1 goto :error
    goto :eof
)

if exist "%LOCALAPPDATA%\Python\bin\python.exe" (
    "%LOCALAPPDATA%\Python\bin\python.exe" app.py
    if errorlevel 1 goto :error
    goto :eof
)

:: 2. Спроба через стандартний python в PATH
where python >nul 2>nul
if %errorlevel% equ 0 (
    python app.py
    if errorlevel 1 goto :error
    goto :eof
)

:: 3. Спроба через утиліту py
where py >nul 2>nul
if %errorlevel% equ 0 (
    py app.py
    if errorlevel 1 goto :error
    goto :eof
)

echo [ПОМИЛКА] Python не знайдено в системі!
echo Будь ласка, переконайтеся, що Python встановлено.
pause
goto :eof

:error
echo.
echo =======================================================
echo  Помилка під час роботи додатку.
echo  Код помилки: %errorlevel%
echo =======================================================
pause
