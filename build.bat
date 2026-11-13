@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Устанавливаю библиотеки...
python -m pip install --upgrade pyinstaller mss pillow pyautogui pynput pywin32 playwright
if errorlevel 1 goto fail
echo Рисую значок...
python -c "import test_izi_app as a; a.save_icon('icon.ico')"
if errorlevel 1 goto fail
echo Собираю TEST_IZI.exe...
python -m PyInstaller --noconfirm --onefile --windowed --name TEST_IZI --icon icon.ico --hidden-import pynput.keyboard._win32 --hidden-import pynput.mouse._win32 --hidden-import browser_chat --collect-all playwright test_izi_app.py
if errorlevel 1 goto fail
echo.
echo Готово: dist\TEST_IZI.exe
pause
exit /b 0
:fail
echo.
echo Сборка не удалась, пришли текст ошибки выше.
pause
exit /b 1
