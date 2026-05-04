@echo off
chcp 65001 >nul
echo ========================================
echo    RadioBOSS Bot - Установка
echo ========================================

:: Проверяем Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ОШИБКА] Python не найден!
    echo Скачай Python 3.11+ с https://python.org
    pause
    exit /b 1
)

echo [OK] Python найден
echo.

:: Устанавливаем зависимости
echo Устанавливаю зависимости...
pip install -r requirements.txt
if errorlevel 1 (
    echo [ОШИБКА] Не удалось установить зависимости
    pause
    exit /b 1
)
echo [OK] Зависимости установлены
echo.

:: Проверяем .env
if not exist .env (
    echo [ВНИМАНИЕ] Файл .env не найден!
    echo Создаю шаблон .env...
    (
        echo DISCORD_TOKEN=вставь_токен_сюда
        echo.
        echo # RadioBOSS
        echo RB_SERVER=192.168.1.100
        echo RB_PORT=9000
        echo RB_PASSWORD=пароль
        echo.
        echo # Пути
        echo DOWNLOAD_DIR=C:\Users\%USERNAME%\Downloads
        echo FFMPEG_PATH=C:\ffmpeg\ffmpeg.exe
    ) > .env
    echo [OK] Файл .env создан - заполни его перед запуском!
    notepad .env
    pause
    exit /b 0
)

echo [OK] Файл .env найден
echo.

:: Проверяем ffmpeg
ffmpeg -version >nul 2>&1
if errorlevel 1 (
    echo [ВНИМАНИЕ] ffmpeg не найден в PATH
    echo Скачиваю ffmpeg...
    python -c "import urllib.request; urllib.request.urlretrieve('https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip', 'ffmpeg.zip'); print('OK')"
    python -c "
import zipfile, os, shutil
with zipfile.ZipFile('ffmpeg.zip', 'r') as z:
    z.extractall('ffmpeg_tmp')
for root, dirs, files in os.walk('ffmpeg_tmp'):
    if 'ffmpeg.exe' in files:
        os.makedirs('C:\\ffmpeg', exist_ok=True)
        for f in ['ffmpeg.exe', 'ffprobe.exe']:
            src = os.path.join(root, f)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join('C:\\ffmpeg', f))
                print('Copied', f)
        break
shutil.rmtree('ffmpeg_tmp')
os.remove('ffmpeg.zip')
print('ffmpeg installed to C:\\ffmpeg')
"
    echo [OK] ffmpeg установлен в C:\ffmpeg
    echo Обнови FFMPEG_PATH в .env на C:\ffmpeg\ffmpeg.exe
)

echo.
echo ========================================
echo    Установка завершена!
echo    Запусти start_bot.bat для старта
echo ========================================
pause
