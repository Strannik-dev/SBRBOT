@echo off
chcp 65001 >nul
echo ========================================
echo    Установка бота как службы Windows
echo ========================================

:: Проверяем права администратора
net session >nul 2>&1
if errorlevel 1 (
    echo [ОШИБКА] Нужны права администратора!
    echo Запусти этот файл от имени администратора
    pause
    exit /b 1
)

set BOT_DIR=%~dp0
set PYTHON_PATH=
for /f "tokens=*" %%i in ('where python') do set PYTHON_PATH=%%i

if "%PYTHON_PATH%"=="" (
    echo [ОШИБКА] Python не найден!
    pause
    exit /b 1
)

echo [OK] Python: %PYTHON_PATH%
echo [OK] Папка бота: %BOT_DIR%

:: Скачиваем NSSM если нет
if not exist "%BOT_DIR%nssm.exe" (
    echo Скачиваю NSSM...
    python -c "import urllib.request; urllib.request.urlretrieve('https://nssm.cc/release/nssm-2.24.zip', '%BOT_DIR%nssm.zip'); print('OK')"
    python -c "
import zipfile, os, shutil
bot_dir = r'%BOT_DIR%'
with zipfile.ZipFile(bot_dir + 'nssm.zip', 'r') as z:
    z.extractall(bot_dir + 'nssm_tmp')
for root, dirs, files in os.walk(bot_dir + 'nssm_tmp'):
    if 'nssm.exe' in files and 'win64' in root:
        shutil.copy2(os.path.join(root, 'nssm.exe'), bot_dir + 'nssm.exe')
        print('Copied nssm.exe')
        break
shutil.rmtree(bot_dir + 'nssm_tmp')
os.remove(bot_dir + 'nssm.zip')
"
)

if not exist "%BOT_DIR%nssm.exe" (
    echo [ОШИБКА] Не удалось скачать NSSM
    pause
    exit /b 1
)

echo [OK] NSSM найден

:: Удаляем старую службу если есть
"%BOT_DIR%nssm.exe" stop RadioBOSSBot >nul 2>&1
"%BOT_DIR%nssm.exe" remove RadioBOSSBot confirm >nul 2>&1

:: Устанавливаем службу
echo Устанавливаю службу...
"%BOT_DIR%nssm.exe" install RadioBOSSBot "%PYTHON_PATH%" "-X utf8 %BOT_DIR%go_bot.py"
"%BOT_DIR%nssm.exe" set RadioBOSSBot AppDirectory "%BOT_DIR%"
"%BOT_DIR%nssm.exe" set RadioBOSSBot DisplayName "RadioBOSS Discord Bot"
"%BOT_DIR%nssm.exe" set RadioBOSSBot Description "Discord bot for RadioBOSS song requests"
"%BOT_DIR%nssm.exe" set RadioBOSSBot Start SERVICE_AUTO_START
"%BOT_DIR%nssm.exe" set RadioBOSSBot AppStdout "%BOT_DIR%logs\bot.log"
"%BOT_DIR%nssm.exe" set RadioBOSSBot AppStderr "%BOT_DIR%logs\bot_error.log"
"%BOT_DIR%nssm.exe" set RadioBOSSBot AppRotateFiles 1
"%BOT_DIR%nssm.exe" set RadioBOSSBot AppRotateBytes 10485760

:: Создаём папку для логов
if not exist "%BOT_DIR%logs" mkdir "%BOT_DIR%logs"

:: Добавляем ffmpeg в PATH службы
"%BOT_DIR%nssm.exe" set RadioBOSSBot AppEnvironmentExtra "PATH=%PATH%;C:\ffmpeg;%BOT_DIR%ffmpeg"

:: Запускаем службу
echo Запускаю службу...
"%BOT_DIR%nssm.exe" start RadioBOSSBot

timeout /t 3 /nobreak >nul

:: Проверяем статус
"%BOT_DIR%nssm.exe" status RadioBOSSBot

echo.
echo ========================================
echo    Готово! Бот установлен как служба
echo    Управление:
echo    - Запуск:   nssm start RadioBOSSBot
echo    - Стоп:     nssm stop RadioBOSSBot
echo    - Удалить:  nssm remove RadioBOSSBot
echo    - Логи:     %BOT_DIR%logs\
echo ========================================
pause
