@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Устанавливаю библиотеки...
python -m pip install --upgrade pyinstaller mss pillow pyautogui pynput pywin32 playwright
if errorlevel 1 goto fail
echo Рисую значок...
python -c "import test_izi_app as a; a.save_icon('icon.ico')"
echo Собираю TEST_IZI.exe прямо в эту папку...
python -m PyInstaller --noconfirm --onefile --windowed --name TEST_IZI --icon icon.ico --distpath . --hidden-import pynput.keyboard._win32 --hidden-import pynput.mouse._win32 --hidden-import browser_chat --collect-all playwright test_izi_app.py
if errorlevel 1 goto fail
rmdir /s /q build >/dev/null 2>nul
del TEST_IZI.spec >/dev/null 2>nul
echo.
echo Готово: TEST_IZI.exe лежит рядом. Запускай флешку через ЗАПУСК.bat
pause
exit /b 0
:fail
echo Сборка не удалась, пришли текст ошибки выше.
pause
exit /b 1
