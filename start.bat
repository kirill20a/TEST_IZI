@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem метка "флешка": программа будет закрываться при извлечении
> flash_mode echo 1

if exist "TEST_IZI.exe" (
    start "" "TEST_IZI.exe"
) else (
    where pythonw >nul 2>nul
    if errorlevel 1 (
        echo Python не найден. Установи его с python.org, затем в этой папке:
        echo    pip install -r requirements.txt
        echo Или собери TEST_IZI.exe через build_flash.bat.
        pause
        exit /b 1
    )
    start "" pythonw test_izi_app.py
)
exit /b 0
