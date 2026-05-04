# RadioBOSS Discord Bot — Инструкция по установке

## Требования
- Windows 10/11 или Windows Server
- Python 3.11+
- RadioBOSS с включённым Web API

---

## Быстрая установка

### 1. Установи Python
Скачай с https://python.org/downloads — при установке отметь **Add Python to PATH**

### 2. Скопируй файлы бота
Все файлы должны быть в одной папке:
```
go_bot.py
requirements.txt
.env
install.bat
start_bot.bat
install_service.bat
```

### 3. Запусти установку
Дважды кликни `install.bat` — он установит зависимости, ffmpeg и создаст `.env`

### 4. Заполни .env
```env
DISCORD_TOKEN=твой_токен_бота

# RadioBOSS (должен быть на том же ПК что и бот!)
RB_SERVER=127.0.0.1
RB_PORT=9000
RB_PASSWORD=твой_пароль

# Пути
DOWNLOAD_DIR=C:\Users\ИМЯ\Downloads
FFMPEG_PATH=C:\ffmpeg\ffmpeg.exe
```

### 5. Запуск

**Вариант A — простой запуск (окно консоли):**
```
start_bot.bat
```

**Вариант B — как служба Windows (рекомендуется):**
Запусти `install_service.bat` от имени администратора.
Бот будет запускаться автоматически при старте Windows.

---

## Настройка RadioBOSS Web API

1. Открой RadioBOSS → Настройки → Удалённое управление
2. Включи Web API
3. Установи порт (по умолчанию 9000)
4. Установи пароль
5. Убедись что RadioBOSS и бот на **одном ПК**

---

## Настройка бота в Discord

После запуска бота напиши в нужных каналах:

| Команда | Описание |
|---------|----------|
| `!setup_panel` | Создать панель ведущего (в канале для DJ) |
| `!setup_requests` | Установить канал запросов (почта радио) |
| `!setup_log` | Установить канал логов |
| `!dj_setup @user ip port pass` | Зарегистрировать ведущего |
| `!setup_role dj @role` | Настроить роль DJ |
| `!setup_role vip @role` | Настроить роль VIP |
| `!setup_role moderator @role` | Настроить роль модератора |
| `!setup_role admin @role` | Настроить роль администратора |

---

## Команды для слушателей

| Команда | Описание |
|---------|----------|
| `!request Metallica Enter Sandman` | Запрос по названию |
| `!request https://music.youtube.com/...` | Запрос по ссылке |
| `!request Metallica \| Привет всем!` | Запрос с сообщением |
| `/play` | Поиск с выбором версии |
| `/rb_search` | Поиск в базе RadioBOSS |
| `/top` | Топ треков и слушателей |
| `/stats` | Личная статистика |

---

## Важно: бот и RadioBOSS на разных ПК

Если RadioBOSS на другом компьютере — нужна общая сетевая папка:

1. На ПК с RadioBOSS создай папку `C:\RadioMusic` и открой общий доступ
2. В `.env` бота измени `DOWNLOAD_DIR`:
```env
DOWNLOAD_DIR=\\192.168.1.100\RadioMusic
```
3. В RadioBOSS настрой Song Request чтобы он читал из `C:\RadioMusic`

---

## Логи

Логи бота (при установке как службы): `logs\bot.log`

Для просмотра в реальном времени:
```
Get-Content logs\bot.log -Wait -Tail 50
```
