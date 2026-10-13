@echo off
chcp 65001 >nul
echo ==============================================
echo  Збірка Truck Controls Manager в один .exe
echo ==============================================
echo.

pip install pyinstaller -q

echo Збираємо додаток...
pyinstaller --noconsole --onefile --clean --icon=assets/app.ico --add-data "assets;assets" --name="TruckControlsManager" --collect-all customtkinter app.py

echo.
if exist "dist\TruckControlsManager.exe" (
    copy /Y "dist\TruckControlsManager.exe" "TruckControlsManager.exe" >nul
    echo [УСПІХ] Готовий .exe файл створено: TruckControlsManager.exe та dist\TruckControlsManager.exe!
) else (
    echo [ПОМИЛКА] Щось пішло не так при збірці.
)
pause
