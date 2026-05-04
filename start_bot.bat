@echo off
chcp 65001 >nul
title RadioBOSS Bot
echo ========================================
echo    RadioBOSS Bot - Запуск
echo ========================================

:: Добавляем ffmpeg в PATH если есть
if exist "C:\ffmpeg\ffmpeg.exe" (
    set PATH=%PATH%;C:\ffmpeg
)
if exist "%~dp0ffmpeg\ffmpeg.exe" (
    set PATH=%PATH%;%~dp0ffmpeg
)

:: Проверяем .env
if not exist "%~dp0.env" (
    echo [ОШИБКА] Файл .env не найден!
    echo Запусти сначала install.bat
    pause
    exit /b 1
)

:loop
echo [%date% %time%] Запускаю бота...
python -X utf8 "%~dp0go_bot.py"
echo [%date% %time%] Бот остановился. Перезапуск через 5 секунд...
timeout /t 5 /nobreak >nul
goto loop
