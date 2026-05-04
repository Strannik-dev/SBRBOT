# -*- coding: utf-8 -*-
import discord
from discord.ext import commands
from discord import app_commands
import os
import json
from dotenv import load_dotenv
import aiohttp
from yt_dlp import YoutubeDL
import asyncio
import re
from typing import Optional, Dict, Any, Union
from urllib.parse import quote
from datetime import datetime

load_dotenv()
BOT_TOKEN = os.getenv("DISCORD_TOKEN")

if not BOT_TOKEN:
    raise ValueError("DISCORD_TOKEN не найден в .env файле!")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Download Configuration
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", os.path.join(os.path.expanduser("~"), "Downloads"))
FFMPEG_PATH = os.getenv("FFMPEG_PATH", "ffmpeg")

DJS_FILE = os.path.join(os.path.dirname(__file__), "djs.json")
STATS_FILE = os.path.join(os.path.dirname(__file__), "stats.json")

# ─── Хранилище данных ────────────────────────────────────────────────────────

def load_data() -> Dict[str, Any]:
    if os.path.exists(DJS_FILE):
        with open(DJS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "djs": {}, "active_dj": None,
        "request_channel": None, "dj_panel_channel": None,
        "log_channel": None,
        "roles": {"dj": None, "vip": None, "moderator": None, "admin": None}
    }

def save_data(data: Dict[str, Any]):
    with open(DJS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_stats() -> Dict[str, Any]:
    if os.path.exists(STATS_FILE):
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"users": {}, "tracks": {}}

def save_stats(stats: Dict[str, Any]):
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

def record_request(user: discord.User | discord.Member, artist: str, title: str):
    """Записать запрос в статистику"""
    stats = load_stats()
    uid = str(user.id)
    track_key = f"{artist} - {title}"

    if uid not in stats["users"]:
        stats["users"][uid] = {"name": user.display_name, "count": 0}
    stats["users"][uid]["count"] += 1
    stats["users"][uid]["name"] = user.display_name

    if track_key not in stats["tracks"]:
        stats["tracks"][track_key] = 0
    stats["tracks"][track_key] += 1

    save_stats(stats)

# ─── Ролевая модель ──────────────────────────────────────────────────────────

def get_roles(data: Dict[str, Any]) -> Dict[str, Optional[int]]:
    roles = data.get("roles", {})
    return {
        "dj": int(roles["dj"]) if roles.get("dj") else None,
        "vip": int(roles["vip"]) if roles.get("vip") else None,
        "moderator": int(roles["moderator"]) if roles.get("moderator") else None,
        "admin": int(roles["admin"]) if roles.get("admin") else None,
    }

def has_role(member: discord.Member, role_id: Optional[int]) -> bool:
    if not role_id:
        return False
    return any(r.id == role_id for r in member.roles)

def is_admin(member: discord.Member, data: Dict[str, Any]) -> bool:
    if member.guild_permissions.administrator:
        return True
    roles = get_roles(data)
    return has_role(member, roles["admin"])

def is_moderator(member: discord.Member, data: Dict[str, Any]) -> bool:
    if is_admin(member, data):
        return True
    roles = get_roles(data)
    return has_role(member, roles["moderator"])

def is_dj(member: discord.Member, data: Dict[str, Any]) -> bool:
    if is_admin(member, data):
        return True
    roles = get_roles(data)
    return has_role(member, roles["dj"])

def is_vip(member: discord.Member, data: Dict[str, Any]) -> bool:
    roles = get_roles(data)
    return has_role(member, roles["vip"]) or is_moderator(member, data)

def get_request_limit(member: discord.Member, data: Dict[str, Any]) -> int:
    """Лимит запросов в очереди для пользователя"""
    if is_moderator(member, data):
        return 10
    if is_vip(member, data):
        return 5
    return 2

# ─── RadioBOSS ───────────────────────────────────────────────────────────────

async def add_to_radioboss(rb_server: str, rb_port: str, rb_password: str,
                            song_info: Dict[str, Any], user_message: str = "") -> tuple[bool, str]:
    """Добавить песню в RadioBOSS. Возвращает (успех, сообщение)."""
    try:
        song_url = song_info["url"]
        song_title = song_info["title"]
        artist = song_info["artist"]

        local_path = None
        if "youtube.com" in song_url or "youtu.be" in song_url:
            local_path = await download_youtube_audio(song_url, artist, song_title)
            if not local_path:
                return False, "Не удалось скачать аудио с YouTube"
            file_ref = local_path
        else:
            file_ref = song_url

        encoded_file = quote(file_ref)
        encoded_message = quote(user_message) if user_message else ""
        api_url = (
            f"http://{rb_server}:{rb_port}/?pass={rb_password}"
            f"&action=songrequest&filename={encoded_file}&message={encoded_message}"
        )

        print(f"Send to RadioBOSS {rb_server}: {file_ref}")

        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=10)) as response:  # type: ignore
                result = (await response.text()).strip()
                print(f"RadioBOSS response: {result}")

                if response.status != 200:
                    if local_path and os.path.exists(local_path):
                        os.remove(local_path)
                    return False, f"HTTP {response.status}"

                if result.startswith("E0"):
                    error_messages = {
                        "E001": "Неверный пароль",
                        "E002": "Неверная команда",
                        "E003": "Ошибка параметров",
                        "E004": "Файл не найден",
                        "E005": "Ошибка воспроизведения",
                        "E006": "Очередь заполнена",
                        "E007": "Запросы отключены",
                        "E008": "Песня уже была запрошена или недавно играла",
                        "E009": "Превышен лимит запросов",
                    }
                    code = result.split(":")[0].strip() if ":" in result else result
                    msg = error_messages.get(code, result)
                    if local_path and os.path.exists(local_path):
                        os.remove(local_path)
                    return False, msg

                return True, "Добавлено в очередь"

    except Exception as e:
        print(f"RadioBOSS Error: {e}")
        return False, str(e)

async def check_radioboss_connection(rb_server: str, rb_port: str, rb_password: str) -> bool:
    try:
        api_url = f"http://{rb_server}:{rb_port}/?pass={rb_password}&action=status"
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=5)) as response:  # type: ignore
                return response.status == 200
    except Exception:
        return False

async def get_songrequest_queue(rb_server: str, rb_port: str, rb_password: str) -> list:
    """Получить очередь запросов из RadioBOSS"""
    try:
        import xml.etree.ElementTree as ET
        api_url = f"http://{rb_server}:{rb_port}/?pass={rb_password}&action=songrequestlist"
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=5)) as response:  # type: ignore
                if response.status != 200:
                    return []
                text = await response.text(encoding="utf-8")
        root = ET.fromstring(text)
        queue = []
        for req in root.findall("Request"):
            filename = req.get("filename", "")
            message = req.get("message", "")
            played = req.get("played", "False")
            if played == "False":
                # Берём имя файла без пути
                name = os.path.basename(filename).replace(".mp3", "")
                queue.append({"name": name, "message": message})
        return queue
    except Exception as e:
        print(f"Queue error: {e}")
        return []

async def send_to_log(guild: Optional[discord.Guild], user: discord.User | discord.Member,
                      song_info: Dict[str, Any], dj: dict, rb_server: str, rb_port: str, rb_password: str):
    """Отправить информацию о добавленном треке в лог-канал"""
    if not guild:
        return
    data = load_data()
    log_channel_id = data.get("log_channel")
    if not log_channel_id:
        return
    channel = guild.get_channel(int(log_channel_id))
    if not channel or not isinstance(channel, discord.TextChannel):
        return

    # Записываем статистику
    record_request(user, song_info.get("artist", ""), song_info.get("title", ""))

    queue = await get_songrequest_queue(rb_server, rb_port, rb_password)

    # Определяем роль пользователя
    role_label = "👤 Слушатель"
    if isinstance(user, discord.Member):
        if is_moderator(user, data):
            role_label = "🛡️ Модератор"
        elif is_vip(user, data):
            role_label = "⭐ VIP"
        elif is_dj(user, data):
            role_label = "🎙️ DJ"

    embed = discord.Embed(
        title="🎵 Новый запрос",
        description=f"**{song_info['artist']}** - **{song_info['title']}**",
        color=discord.Color.blue()
    )
    embed.add_field(name="Запросил", value=f"{user.mention} {role_label}", inline=True)
    embed.add_field(name="Ведущий", value=dj["name"], inline=True)
    embed.add_field(name="Длительность", value=song_info.get("duration", "N/A"), inline=True)
    if song_info.get("url"):
        embed.add_field(name="Ссылка", value=f"[Открыть]({song_info['url']})", inline=False)
    embed.set_thumbnail(url=song_info.get("thumbnail", ""))

    if queue:
        queue_text = "\n".join(
            f"{i}. {q['name']}" + (f" — _{q['message']}_" if q['message'] else "")
            for i, q in enumerate(queue[:10], 1)
        )
        embed.add_field(name=f"📋 Очередь ({len(queue)})", value=queue_text, inline=False)
    else:
        embed.add_field(name="📋 Очередь", value="Пусто", inline=False)

    embed.set_footer(text=datetime.now().strftime("%H:%M:%S"))
    await channel.send(embed=embed)

async def search_radioboss(rb_server: str, rb_port: str, rb_password: str, query: str) -> list:
    """Поиск треков в базе RadioBOSS"""
    try:
        import xml.etree.ElementTree as ET
        api_url = f"http://{rb_server}:{rb_port}/?pass={rb_password}&action=library&filename=Base"
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=15)) as response:  # type: ignore
                if response.status != 200:
                    return []
                text = await response.text(encoding="utf-8")
        root = ET.fromstring(text)
        query_lower = query.lower()
        results = []
        for track in root.findall(".//TRACK"):
            artist = track.get("ARTIST", "")
            title = track.get("TITLE", "")
            filename = track.get("FILENAME", "")
            if query_lower in artist.lower() or query_lower in title.lower():
                results.append({
                    "artist": artist,
                    "title": title,
                    "filename": filename,
                    "duration": track.get("DURATION", ""),
                    "source": "RadioBOSS",
                })
            if len(results) >= 10:
                break
        return results
    except Exception as e:
        print(f"RadioBOSS search error: {e}")
        return []

async def add_file_to_radioboss(rb_server: str, rb_port: str, rb_password: str,
                                 filename: str, user_message: str = "") -> tuple[bool, str]:
    """Добавить локальный файл из базы RadioBOSS в очередь запросов"""
    try:
        encoded_file = quote(filename)
        encoded_message = quote(user_message) if user_message else ""
        api_url = (
            f"http://{rb_server}:{rb_port}/?pass={rb_password}"
            f"&action=songrequest&filename={encoded_file}&message={encoded_message}"
        )
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=10)) as response:  # type: ignore
                result = (await response.text()).strip()
                if result.startswith("E0"):
                    error_messages = {
                        "E001": "Неверный пароль", "E002": "Неверная команда",
                        "E003": "Ошибка параметров", "E004": "Файл не найден",
                        "E005": "Ошибка воспроизведения", "E006": "Очередь заполнена",
                        "E007": "Запросы отключены", "E008": "Песня уже была запрошена или недавно играла",
                        "E009": "Превышен лимит запросов",
                    }
                    code = result.split(":")[0].strip() if ":" in result else result
                    return False, error_messages.get(code, result)
                return True, "Добавлено в очередь"
    except Exception as e:
        return False, str(e)

# ─── YouTube Music ───────────────────────────────────────────────────────────

def is_youtube_url(query: str) -> bool:
    """Проверить является ли строка ссылкой на YouTube/YouTube Music"""
    return any(x in query for x in [
        "youtube.com/watch", "youtu.be/",
        "music.youtube.com/watch", "youtube.com/shorts"
    ])

async def fetch_youtube_url(url: str) -> Optional[Dict[str, Any]]:
    """Получить метаданные трека по прямой ссылке"""
    try:
        loop = asyncio.get_event_loop()

        def run_fetch() -> Optional[Dict[str, Any]]:
            try:
                opts: Dict[str, Any] = {
                    "quiet": True, "no_warnings": True, "socket_timeout": 15,
                }
                with YoutubeDL(opts) as ydl:
                    v = ydl.extract_info(url, download=False)
                    if not v:
                        return None
                    artist = (
                        v.get("artist") or
                        v.get("creator") or
                        v.get("uploader") or
                        v.get("channel") or
                        extract_artist(v.get("title", ""))
                    )
                    track_title = v.get("track") or clean_title(v.get("title", ""), artist)
                    video_id = v.get("id", "")
                    return {
                        "title": track_title,
                        "artist": artist,
                        "url": f"https://www.youtube.com/watch?v={video_id}",
                        "duration": format_duration(int(v.get("duration") or 0)),
                        "thumbnail": v.get("thumbnail") or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                        "source": "YouTube Music",
                    }
            except Exception as e:
                print(f"URL fetch error: {e}")
                return None

        return await loop.run_in_executor(None, run_fetch)
    except Exception as e:
        print(f"fetch_youtube_url error: {e}")
        return None

async def search_youtube(query: str, max_results: int = 5) -> Optional[list]:
    """Поиск треков через YouTube Music (только аудио, не видео)"""
    try:
        loop = asyncio.get_event_loop()

        def run_search() -> Optional[list]:
            try:
                # Шаг 1: получаем список ID через flat поиск
                flat_opts: Dict[str, Any] = {
                    "quiet": True, "no_warnings": True,
                    "socket_timeout": 10, "extract_flat": True,
                }
                search_url = "https://music.youtube.com/search?q=" + query.replace(" ", "+")
                candidate_ids = []

                with YoutubeDL(flat_opts) as ydl:
                    info = ydl.extract_info(search_url, download=False)
                    for entry in info.get("entries", []):
                        if not entry:
                            continue
                        video_id = entry.get("id", "")
                        title = entry.get("title") or ""
                        if not title or not video_id:
                            continue
                        title_lower = title.lower()
                        if any(w in title_lower for w in ["full album", "playlist", "concert", "live at", "tour"]):
                            continue
                        candidate_ids.append(video_id)
                        if len(candidate_ids) >= max_results:
                            break

                if not candidate_ids:
                    return None

                # Шаг 2: получаем полные метаданные для каждого трека
                full_opts: Dict[str, Any] = {
                    "quiet": True, "no_warnings": True, "socket_timeout": 10,
                }
                results = []
                with YoutubeDL(full_opts) as ydl:
                    for video_id in candidate_ids:
                        try:
                            url = f"https://music.youtube.com/watch?v={video_id}"
                            v = ydl.extract_info(url, download=False)
                            if not v:
                                continue
                            artist = (
                                v.get("artist") or
                                v.get("creator") or
                                v.get("uploader") or
                                v.get("channel") or
                                extract_artist(v.get("title", ""))
                            )
                            track_title = v.get("track") or clean_title(v.get("title", ""), artist)
                            results.append({
                                "title": track_title,
                                "artist": artist,
                                "url": f"https://www.youtube.com/watch?v={video_id}",
                                "duration": format_duration(int(v.get("duration") or 0)),
                                "thumbnail": v.get("thumbnail") or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                                "source": "YouTube Music",
                            })
                        except Exception as e:
                            print(f"Error fetching {video_id}: {e}")

                return results if results else None

            except Exception as e:
                print(f"YouTube Music Error: {e}")
                return None

        result = await loop.run_in_executor(None, run_search)

        # Fallback на обычный YouTube если YouTube Music ничего не нашёл
        if not result:
            print("YouTube Music returned nothing, falling back to YouTube search")

            def run_fallback() -> Optional[list]:
                try:
                    ydl_opts_fb: Dict[str, Any] = {
                        "quiet": True, "no_warnings": True,
                        "socket_timeout": 10, "extract_flat": True,
                    }
                    with YoutubeDL(ydl_opts_fb) as ydl:
                        info = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
                        if not info or "entries" not in info or not info["entries"]:
                            return None
                        results = []
                        for entry in info["entries"]:
                            if not entry:
                                continue
                            video_id = entry.get("id", "")
                            title = entry.get("title", "Unknown")
                            artist = (
                                entry.get("artist") or
                                entry.get("uploader") or
                                entry.get("channel") or
                                extract_artist(title)
                            )
                            track_title = entry.get("track") or clean_title(title, artist)
                            results.append({
                                "title": track_title,
                                "artist": artist,
                                "url": f"https://www.youtube.com/watch?v={video_id}",
                                "duration": format_duration(int(entry.get("duration") or 0)),
                                "thumbnail": entry.get("thumbnail") or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                                "source": "YouTube",
                            })
                        return results if results else None
                except Exception as e:
                    print(f"Fallback YoutubeDL Error: {e}")
                    return None

            result = await loop.run_in_executor(None, run_fallback)

        return result
    except Exception as e:
        print(f"YouTube Search Error: {e}")
        return None

async def download_youtube_audio(url: str, artist: str, title: str) -> Optional[str]:
    try:
        loop = asyncio.get_event_loop()
        safe_name = "".join(c for c in f"{artist} - {title}" if c.isalnum() or c in (" ", "-", "_")).strip()
        output_path = os.path.join(DOWNLOAD_DIR, safe_name)

        ydl_opts: Dict[str, Any] = {
            "format": "bestaudio/best",
            "quiet": True,
            "no_warnings": True,
            "outtmpl": output_path + ".%(ext)s",
            "ffmpeg_location": FFMPEG_PATH,
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"},
                {"key": "FFmpegMetadata", "add_metadata": True},
            ],
            "postprocessor_args": [
                "-metadata", f"artist={artist}",
                "-metadata", f"title={title}",
            ],
            "socket_timeout": 30,
        }

        def run_download() -> Optional[str]:
            try:
                with YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
                mp3_path = output_path + ".mp3"
                return mp3_path if os.path.exists(mp3_path) else None
            except Exception as e:
                print(f"Download error: {e}")
                return None

        return await loop.run_in_executor(None, run_download)
    except Exception as e:
        print(f"YouTube Download Error: {e}")
        return None

# ─── Утилиты ─────────────────────────────────────────────────────────────────

def extract_artist(title: str) -> str:
    if " - " in title:
        return title.split(" - ")[0].strip()
    return "Unknown Artist"

def clean_title(title: str, artist: str) -> str:
    if artist and artist != "Unknown Artist":
        for sep in [" - ", ": ", " – "]:
            if title.lower().startswith(artist.lower() + sep.lower()):
                title = title[len(artist) + len(sep):]
                break
    title = re.sub(r"\s*\(Official\s*(Music\s*)?Video\)", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*\(Official\s*Audio\)", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*\[Official\s*(Music\s*)?Video\]", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*\(Lyrics?\)", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*\[Lyrics?\]", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*\(HD\)", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*\[HD\]", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*\(HQ\)", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*4K$", "", title, flags=re.IGNORECASE)
    return title.strip()

def format_duration(seconds: int) -> str:
    if seconds < 3600:
        return f"{seconds // 60}:{seconds % 60:02d}"
    return f"{seconds // 3600}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"

# ─── Кнопки панели ведущего ──────────────────────────────────────────────────

class DJPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="В эфире", style=discord.ButtonStyle.success, emoji="🎙️", custom_id="dj_on_air")
    async def on_air(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = load_data()
        user_id = str(interaction.user.id)

        # Проверяем роль DJ или регистрацию
        if user_id not in data["djs"]:
            if not isinstance(interaction.user, discord.Member) or not is_dj(interaction.user, data):
                await interaction.response.send_message(
                    "❌ Ты не зарегистрирован как ведущий. Обратись к администратору.",
                    ephemeral=True
                )
                return

        # Сразу отвечаем на interaction чтобы не истёк таймаут
        await interaction.response.defer(ephemeral=True)

        # Проверяем подключение к RadioBOSS
        dj = data["djs"][user_id]
        connected = await check_radioboss_connection(dj["server"], dj["port"], dj["password"])
        if not connected:
            await interaction.followup.send(
                f"❌ Не удалось подключиться к RadioBOSS ({dj['server']}:{dj['port']}). Проверь что RadioBOSS запущен.",
                ephemeral=True
            )
            return

        prev_dj_id = data["active_dj"]
        data["active_dj"] = user_id
        save_data(data)

        await update_dj_panel(interaction.guild)

        msg = f"🎙️ **{interaction.user.display_name}** теперь в эфире!"
        if prev_dj_id and prev_dj_id != user_id and prev_dj_id in data["djs"]:
            prev_name = data["djs"][prev_dj_id].get("name", "Предыдущий ведущий")
            msg += f"\n*(сменил {prev_name})*"

        await interaction.followup.send(msg, ephemeral=True)

    @discord.ui.button(label="Покинуть эфир", style=discord.ButtonStyle.danger, emoji="🔴", custom_id="dj_off_air")
    async def off_air(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = load_data()
        user_id = str(interaction.user.id)

        if data["active_dj"] != user_id:
            await interaction.response.send_message(
                "❌ Ты сейчас не в эфире.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        data["active_dj"] = None
        save_data(data)

        await update_dj_panel(interaction.guild)
        await interaction.followup.send(
            f"🔴 Ты покинул эфир.",
            ephemeral=True
        )

async def update_dj_panel(guild: Optional[discord.Guild]):
    """Обновить embed панели ведущего"""
    if not guild:
        return
    data = load_data()
    panel_channel_id = data.get("dj_panel_channel")
    if not panel_channel_id:
        return

    channel = guild.get_channel(int(panel_channel_id))
    if not channel or not isinstance(channel, discord.TextChannel):
        return

    active_dj_id = data.get("active_dj")
    if active_dj_id and active_dj_id in data["djs"]:
        dj = data["djs"][active_dj_id]
        embed = discord.Embed(
            title="🎙️ Панель ведущего",
            description=f"**В эфире:** {dj['name']}",
            color=discord.Color.green()
        )
    else:
        embed = discord.Embed(
            title="🎙️ Панель ведущего",
            description="**В эфире:** никого нет",
            color=discord.Color.red()
        )
    embed.set_footer(text="Нажми кнопку чтобы заступить или покинуть эфир")

    # Пробуем отредактировать сохранённое сообщение
    panel_msg_id = data.get("dj_panel_message")
    if panel_msg_id:
        try:
            msg = await channel.fetch_message(int(panel_msg_id))
            await msg.edit(embed=embed, view=DJPanelView())
            return
        except discord.NotFound:
            pass

    # Сообщение не найдено — создаём новое и сохраняем ID
    msg = await channel.send(embed=embed, view=DJPanelView())
    data["dj_panel_message"] = str(msg.id)
    save_data(data)

# ─── События ─────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    assert bot.user is not None
    print(f"Bot {bot.user} connected!")
    print(f"ID: {bot.user.id}")
    bot.add_view(DJPanelView())
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
    # Запускаем обновление статуса
    bot.loop.create_task(update_status_loop())

# ─── Команды администратора ──────────────────────────────────────────────────

@bot.command(name="dj_setup")
@commands.has_permissions(administrator=True)
async def dj_setup(ctx, member: discord.Member, rb_server: str, rb_port: str, rb_password: str):
    """
    Зарегистрировать ведущего
    Использование: !dj_setup @ведущий ip порт пароль
    Пример: !dj_setup @DJ_Vasya 192.168.1.100 9000 mypassword
    """
    data = load_data()
    data["djs"][str(member.id)] = {
        "name": member.display_name,
        "server": rb_server,
        "port": rb_port,
        "password": rb_password,
    }
    save_data(data)

    embed = discord.Embed(
        title="✅ Ведущий зарегистрирован",
        color=discord.Color.green()
    )
    embed.add_field(name="Ведущий", value=member.mention, inline=True)
    embed.add_field(name="RadioBOSS", value=f"{rb_server}:{rb_port}", inline=True)
    await ctx.send(embed=embed)

@bot.command(name="dj_remove")
@commands.has_permissions(administrator=True)
async def dj_remove(ctx, member: discord.Member):
    """Удалить ведущего"""
    data = load_data()
    user_id = str(member.id)

    if user_id not in data["djs"]:
        await ctx.send(f"❌ {member.mention} не зарегистрирован как ведущий.")
        return

    del data["djs"][user_id]
    if data["active_dj"] == user_id:
        data["active_dj"] = None
    save_data(data)

    await ctx.send(f"✅ {member.mention} удалён из списка ведущих.")

@bot.command(name="dj_list")
@commands.has_permissions(administrator=True)
async def dj_list(ctx):
    """Список зарегистрированных ведущих"""
    data = load_data()
    if not data["djs"]:
        await ctx.send("❌ Нет зарегистрированных ведущих.")
        return

    embed = discord.Embed(title="🎙️ Ведущие", color=discord.Color.blue())
    for user_id, dj in data["djs"].items():
        active = " 🟢 В эфире" if data["active_dj"] == user_id else ""
        embed.add_field(
            name=f"{dj['name']}{active}",
            value=f"RadioBOSS: `{dj['server']}:{dj['port']}`",
            inline=False
        )
    await ctx.send(embed=embed)

@bot.command(name="setup_panel")
@commands.has_permissions(administrator=True)
async def setup_panel(ctx):
    """Создать панель ведущего в текущем канале"""
    data = load_data()
    data["dj_panel_channel"] = str(ctx.channel.id)
    data["dj_panel_message"] = None  # сбрасываем старое сообщение
    save_data(data)

    embed = discord.Embed(
        title="🎙️ Панель ведущего",
        description="**В эфире:** никого нет",
        color=discord.Color.red()
    )
    embed.set_footer(text="Нажми кнопку чтобы заступить или покинуть эфир")
    msg = await ctx.send(embed=embed, view=DJPanelView())

    # Сохраняем ID сообщения
    data["dj_panel_message"] = str(msg.id)
    save_data(data)

@bot.command(name="setup_requests")
@commands.has_permissions(administrator=True)
async def setup_requests(ctx):
    """Установить текущий канал как канал запросов песен"""
    data = load_data()
    data["request_channel"] = str(ctx.channel.id)
    save_data(data)
    await ctx.send(f"✅ Канал {ctx.channel.mention} установлен как канал запросов песен.")

@bot.command(name="setup_log")
@commands.has_permissions(administrator=True)
async def setup_log(ctx):
    """Установить текущий канал как канал логов запросов"""
    data = load_data()
    data["log_channel"] = str(ctx.channel.id)
    save_data(data)
    await ctx.send(f"✅ Канал {ctx.channel.mention} установлен как канал логов запросов.")

# ─── Команда запроса песни ───────────────────────────────────────────────────

@bot.command(name="request")
async def request_song(ctx, *, query: Optional[str] = None):
    """
    Запросить песню
    Использование: !request Artist Title
    С сообщением: !request Artist Title | Привет всем!
    """
    if not query:
        await ctx.send("❌ Использование: `!request Artist Title`", delete_after=5)
        return

    data = load_data()

    # Проверяем что команда в правильном канале
    request_channel_id = data.get("request_channel")
    if request_channel_id and str(ctx.channel.id) != request_channel_id:
        await ctx.send(f"❌ Запросы принимаются только в <#{request_channel_id}>", delete_after=5)
        return

    # Проверяем что есть активный ведущий
    active_dj_id = data.get("active_dj")
    if not active_dj_id or active_dj_id not in data["djs"]:
        await ctx.send("❌ Сейчас нет ведущего в эфире. Попробуй позже.", delete_after=10)
        return

    dj = data["djs"][active_dj_id]

    # Разделяем запрос и сообщение
    user_message = ""
    if "|" in query:
        parts = query.split("|", 1)
        query = parts[0].strip()
        user_message = parts[1].strip()

    # Удаляем сообщение пользователя из чата
    try:
        await ctx.message.delete()
    except Exception:
        pass

    try:
        search_msg = await ctx.send(f"🔍 Обрабатываю: **{query}**...", delete_after=60)

        song_info = None
        rb_success = False
        rb_message = ""

        if is_youtube_url(query):
            # Прямая ссылка — берём метаданные напрямую
            await search_msg.edit(content=f"⬇️ Загружаю по ссылке...")
            song_info = await fetch_youtube_url(query)
            if song_info:
                rb_success, rb_message = await add_to_radioboss(
                    dj["server"], dj["port"], dj["password"],
                    song_info, user_message
                )
        else:
            # Текстовый поиск
            youtube_results = await search_youtube(query, max_results=5)
            if youtube_results:
                for i, candidate in enumerate(youtube_results):
                    if i > 0:
                        await search_msg.edit(content=f"🔍 Пробую вариант {i+1}/5...")
                    else:
                        await search_msg.edit(content=f"⬇️ Скачиваю: **{candidate['title']}**...")
                    rb_success, rb_message = await add_to_radioboss(
                        dj["server"], dj["port"], dj["password"],
                        candidate, user_message
                    )
                    song_info = candidate
                    if rb_success:
                        break
                    elif rb_message != "Песня уже была запрошена или недавно играла":
                        break

        if not song_info:
            await search_msg.edit(content=f"❌ Не найдено: **{query}**")
            return

        if rb_success:
            embed = discord.Embed(
                title="✅ Добавлено в очередь!",
                description=f"**{song_info['artist']}** - **{song_info['title']}**",
                color=discord.Color.green()
            )
            embed.add_field(name="Длительность", value=song_info["duration"], inline=True)
            embed.add_field(name="Ведущий", value=dj["name"], inline=True)
            if song_info.get("thumbnail"):
                embed.set_thumbnail(url=song_info["thumbnail"])
            embed.set_footer(text=f"Запрос от {ctx.author.display_name}")
            if user_message:
                embed.add_field(name="💬 Сообщение", value=user_message, inline=False)
            await search_msg.edit(content=None, embed=embed)
            await send_to_log(ctx.guild, ctx.author, song_info, dj,
                              dj["server"], dj["port"], dj["password"])
        else:
            await search_msg.edit(content=f"❌ {rb_message}")

    except Exception as e:
        await ctx.send(f"❌ Ошибка: {str(e)}", delete_after=10)

@bot.command(name="on_air")
async def on_air_status(ctx):
    """Показать кто сейчас в эфире"""
    data = load_data()
    active_dj_id = data.get("active_dj")

    if active_dj_id and active_dj_id in data["djs"]:
        dj = data["djs"][active_dj_id]
        embed = discord.Embed(
            title="🎙️ Сейчас в эфире",
            description=f"**{dj['name']}**",
            color=discord.Color.green()
        )
    else:
        embed = discord.Embed(
            title="🎙️ Эфир",
            description="Сейчас никого нет в эфире",
            color=discord.Color.red()
        )
    await ctx.send(embed=embed)

@bot.command(name="ping")
async def ping(ctx):
    await ctx.send("Pong!")

# ─── Select Menu для выбора трека ───────────────────────────────────────────

class YouTubeTrackSelect(discord.ui.View):
    def __init__(self, results: list, dj: dict, user_message: str = "", user_id: int = 0):
        super().__init__(timeout=60)
        self.results = results
        self.dj = dj
        self.user_message = user_message
        self.user_id = user_id

        options = [
            discord.SelectOption(
                label=f"{r['artist']} - {r['title']}"[:100],
                description=f"{r['duration']} | YouTube Music"[:100],
                value=str(i)
            )
            for i, r in enumerate(results[:5])
        ]
        select = discord.ui.Select(
            placeholder="Выбери версию трека...",
            options=options,
            custom_id="yt_track_select"
        )
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Это не твой выбор.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        idx = int(interaction.data["values"][0])  # type: ignore
        candidate = self.results[idx]

        await interaction.followup.send(
            f"⬇️ Скачиваю: **{candidate['title']}**...", ephemeral=True
        )

        rb_success, rb_message = await add_to_radioboss(
            self.dj["server"], self.dj["port"], self.dj["password"],
            candidate, self.user_message
        )

        # Убираем select menu из оригинального сообщения
        await interaction.edit_original_response(view=None)

        if rb_success:
            embed = discord.Embed(
                title="✅ Добавлено в очередь!",
                description=f"**{candidate['artist']}** - **{candidate['title']}**",
                color=discord.Color.green()
            )
            embed.add_field(name="Длительность", value=candidate["duration"], inline=True)
            embed.add_field(name="Ведущий", value=self.dj["name"], inline=True)
            if candidate.get("thumbnail"):
                embed.set_thumbnail(url=candidate["thumbnail"])
            embed.set_footer(text=f"Запрос от {interaction.user.display_name}")
            if self.user_message:
                embed.add_field(name="💬 Сообщение", value=self.user_message, inline=False)
            await interaction.followup.send(embed=embed, ephemeral=True)
            await send_to_log(interaction.guild, interaction.user, candidate,
                              self.dj, self.dj["server"], self.dj["port"], self.dj["password"])
        else:
            await interaction.followup.send(f"❌ {rb_message}", ephemeral=True)

class RBTrackSelect(discord.ui.View):
    def __init__(self, results: list, dj: dict, user_id: int = 0):
        super().__init__(timeout=60)
        self.results = results
        self.dj = dj
        self.user_id = user_id

        options = [
            discord.SelectOption(
                label=f"{r['artist']} - {r['title']}"[:100],
                description=f"{r['duration']} | RadioBOSS"[:100],
                value=str(i)
            )
            for i, r in enumerate(results[:5])
        ]
        select = discord.ui.Select(
            placeholder="Выбери трек из базы RadioBOSS...",
            options=options,
            custom_id="rb_track_select"
        )
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Это не твой выбор.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        idx = int(interaction.data["values"][0])  # type: ignore
        track = self.results[idx]

        ok, rb_msg = await add_file_to_radioboss(
            self.dj["server"], self.dj["port"], self.dj["password"], track["filename"]
        )

        # Убираем select menu
        await interaction.edit_original_response(view=None)

        if ok:
            await interaction.followup.send(
                f"✅ **{track['artist']} - {track['title']}** добавлено в очередь!",
                ephemeral=True
            )
            await send_to_log(interaction.guild, interaction.user,
                              {**track, "url": "", "thumbnail": ""},
                              self.dj, self.dj["server"], self.dj["port"], self.dj["password"])
        else:
            await interaction.followup.send(f"❌ {rb_msg}", ephemeral=True)

# ─── Слэш-команды ────────────────────────────────────────────────────────────

def get_active_dj() -> tuple[Optional[dict], str]:
    """Получить активного ведущего. Возвращает (dj_dict, error_message)"""
    data = load_data()
    active_dj_id = data.get("active_dj")
    if not active_dj_id or active_dj_id not in data["djs"]:
        return None, "❌ Сейчас нет ведущего в эфире. Попробуй позже."
    return data["djs"][active_dj_id], ""

@bot.tree.command(name="play", description="Найти песню на YouTube и добавить в RadioBOSS")
@app_commands.describe(query="Название трека или исполнитель", message="Сообщение ведущему (необязательно)")
@app_commands.guild_only()
async def slash_play(interaction: discord.Interaction, query: str, message: str = ""):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("❌ Команда доступна только на сервере.", ephemeral=True)
        return
    if not interaction.user.voice:
        await interaction.response.send_message("❌ Ты должен быть в голосовом канале!", ephemeral=True)
        return

    dj, error = get_active_dj()
    if not dj:
        await interaction.response.send_message(error, ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    # Если передана ссылка — берём напрямую без Select Menu
    if is_youtube_url(query):
        await interaction.followup.send("⬇️ Загружаю по ссылке...", ephemeral=True)
        song_info = await fetch_youtube_url(query)
        if not song_info:
            await interaction.followup.send("❌ Не удалось получить информацию по ссылке.", ephemeral=True)
            return

        rb_success, rb_message = await add_to_radioboss(
            dj["server"], dj["port"], dj["password"], song_info, message
        )

        embed = discord.Embed(
            title="✅ Добавлено в очередь!" if rb_success else "❌ Ошибка",
            description=f"**{song_info['artist']}** - **{song_info['title']}**",
            color=discord.Color.green() if rb_success else discord.Color.red()
        )
        embed.add_field(name="Длительность", value=song_info["duration"], inline=True)
        embed.add_field(name="Ведущий", value=dj["name"], inline=True)
        if song_info.get("thumbnail"):
            embed.set_thumbnail(url=song_info["thumbnail"])
        embed.set_footer(text=f"Запрос от {interaction.user.display_name}")
        if message:
            embed.add_field(name="💬 Сообщение", value=message, inline=False)
        embed.add_field(
            name="🎙️ RadioBOSS",
            value="✅ Добавлено в очередь!" if rb_success else f"❌ {rb_message}",
            inline=False
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        if rb_success:
            await send_to_log(interaction.guild, interaction.user, song_info,
                              dj, dj["server"], dj["port"], dj["password"])
        return

    # Текстовый поиск — показываем Select Menu
    youtube_results = await search_youtube(query, max_results=5)
    if not youtube_results:
        await interaction.followup.send(f"❌ Песня '**{query}**' не найдена на YouTube Music.", ephemeral=True)
        return

    # Показываем Select Menu для выбора версии
    embed = discord.Embed(
        title=f"🔍 Результаты поиска: {query}",
        description="Выбери нужную версию трека:",
        color=discord.Color.blue()
    )
    for i, r in enumerate(youtube_results[:5], 1):
        embed.add_field(
            name=f"{i}. {r['artist']} - {r['title']}",
            value=f"⏱ {r['duration']}",
            inline=False
        )
    if youtube_results[0].get("thumbnail"):
        embed.set_thumbnail(url=youtube_results[0]["thumbnail"])

    view = YouTubeTrackSelect(youtube_results, dj, message, interaction.user.id)
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)

@bot.tree.command(name="rb_search", description="Поиск трека в базе RadioBOSS")
@app_commands.describe(query="Название трека или исполнитель")
@app_commands.guild_only()
async def slash_rb_search(interaction: discord.Interaction, query: str):
    dj, error = get_active_dj()
    if not dj:
        await interaction.response.send_message(error, ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    results = await search_radioboss(dj["server"], dj["port"], dj["password"], query)

    if not results:
        embed = discord.Embed(
            title="❌ Не найдено в базе RadioBOSS",
            description=f"По запросу **{query}** ничего не найдено.\nИспользуй `/play {query}` для поиска на YouTube.",
            color=discord.Color.orange()
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    embed = discord.Embed(
        title=f"🔍 Найдено в RadioBOSS: {query}",
        description="Выбери трек для добавления в очередь:",
        color=discord.Color.blue()
    )
    for i, r in enumerate(results[:5], 1):
        embed.add_field(
            name=f"{i}. {r['artist']} - {r['title']}",
            value=f"⏱ {r['duration']}",
            inline=False
        )

    view = RBTrackSelect(results, dj, interaction.user.id)
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)

@bot.tree.command(name="rb_request", description="Найти и добавить трек из базы RadioBOSS")
@app_commands.describe(query="Название трека или исполнитель")
@app_commands.guild_only()
async def slash_rb_request(interaction: discord.Interaction, query: str):
    dj, error = get_active_dj()
    if not dj:
        await interaction.response.send_message(error, ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    results = await search_radioboss(dj["server"], dj["port"], dj["password"], query)

    if not results:
        embed = discord.Embed(
            title="❌ Не найдено в базе RadioBOSS",
            description=f"По запросу **{query}** ничего не найдено.\nИспользуй `/play {query}` для поиска на YouTube.",
            color=discord.Color.orange()
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    embed = discord.Embed(
        title=f"🔍 Найдено в RadioBOSS: {query}",
        description="Выбери трек для добавления в очередь:",
        color=discord.Color.blue()
    )
    for i, r in enumerate(results[:5], 1):
        embed.add_field(
            name=f"{i}. {r['artist']} - {r['title']}",
            value=f"⏱ {r['duration']}",
            inline=False
        )

    view = RBTrackSelect(results, dj, interaction.user.id)
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)

@bot.tree.command(name="help", description="Список всех команд бота")
@app_commands.guild_only()
async def slash_help(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🎵 Команды RadioBOSS бота",
        color=discord.Color.blue()
    )
    embed.add_field(
        name="🎧 Запросы песен",
        value=(
            "`/play [запрос]` — поиск на YouTube + выбор версии\n"
            "`/rb_search [запрос]` — поиск в базе RadioBOSS\n"
            "`/rb_request [запрос]` — найти и добавить из базы RadioBOSS\n"
            "`!request [запрос]` — запрос через текстовую команду\n"
            "`!rb_request [запрос]` — запрос из базы RadioBOSS"
        ),
        inline=False
    )
    embed.add_field(
        name="🎙️ Ведущие",
        value=(
            "`!on_air` — кто сейчас в эфире\n"
            "Кнопки в панели ведущего — заступить/покинуть эфир"
        ),
        inline=False
    )
    embed.add_field(
        name="⚙️ Администрирование",
        value=(
            "`!dj_setup @user ip port pass` — зарегистрировать ведущего\n"
            "`!dj_remove @user` — удалить ведущего\n"
            "`!dj_list` — список ведущих\n"
            "`!setup_panel` — создать панель ведущего\n"
            "`!setup_requests` — установить канал запросов\n"
            "`!setup_log` — установить канал логов\n"
            "`!setup_role dj/vip/moderator/admin @role` — настроить роль"
        ),
        inline=False
    )
    embed.add_field(
        name="📊 Статистика",
        value=(
            "`/stats` — твоя статистика запросов\n"
            "`/top` — топ треков и слушателей\n"
            "`/remove_request` — удалить трек из очереди (модератор)"
        ),
        inline=False
    )
    embed.set_footer(text="Для запросов с сообщением: !request Metallica Enter Sandman | Привет!")
    await interaction.response.send_message(embed=embed)

# ─── Статус бота ─────────────────────────────────────────────────────────────

async def get_current_track() -> Optional[str]:
    """Получить текущий трек из RadioBOSS активного ведущего"""
    try:
        import xml.etree.ElementTree as ET
        data = load_data()
        active_dj_id = data.get("active_dj")
        if not active_dj_id or active_dj_id not in data["djs"]:
            return None
        dj = data["djs"][active_dj_id]
        api_url = f"http://{dj['server']}:{dj['port']}/?pass={dj['password']}&action=playbackinfo"
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=5)) as response:  # type: ignore
                if response.status != 200:
                    return None
                text = await response.text(encoding="utf-8")
        root = ET.fromstring(text)
        track = root.find(".//CurrentTrack/TRACK")
        if track is not None:
            artist = track.get("ARTIST", "")
            title = track.get("TITLE", "")
            if artist and title:
                return f"{artist} - {title}"
        return None
    except Exception:
        return None

async def update_status_loop():
    """Обновлять статус бота каждые 30 секунд"""
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            track = await get_current_track()
            if track:
                await bot.change_presence(
                    activity=discord.Activity(
                        type=discord.ActivityType.listening,
                        name=track
                    )
                )
            else:
                data = load_data()
                active_dj_id = data.get("active_dj")
                if active_dj_id and active_dj_id in data["djs"]:
                    dj_name = data["djs"][active_dj_id]["name"]
                    await bot.change_presence(
                        activity=discord.Activity(
                            type=discord.ActivityType.watching,
                            name=f"эфир {dj_name}"
                        )
                    )
                else:
                    await bot.change_presence(
                        activity=discord.Activity(
                            type=discord.ActivityType.watching,
                            name="радио | /help"
                        )
                    )
        except Exception as e:
            print(f"Status update error: {e}")
        await asyncio.sleep(30)

# ─── Команды ролей ───────────────────────────────────────────────────────────

@bot.command(name="setup_role")
@commands.has_permissions(administrator=True)
async def setup_role(ctx, role_type: str, role: discord.Role):
    """
    Настроить роль для бота
    Использование: !setup_role dj/vip/moderator/admin @role
    """
    role_type = role_type.lower()
    if role_type not in ("dj", "vip", "moderator", "admin"):
        await ctx.send("❌ Тип роли: `dj`, `vip`, `moderator`, `admin`")
        return

    data = load_data()
    if "roles" not in data:
        data["roles"] = {"dj": None, "vip": None, "moderator": None, "admin": None}
    data["roles"][role_type] = str(role.id)
    save_data(data)

    role_names = {"dj": "DJ", "vip": "VIP Listener", "moderator": "Moderator", "admin": "Admin"}
    await ctx.send(f"✅ Роль **{role_names[role_type]}** установлена: {role.mention}")

@bot.command(name="roles_list")
@commands.has_permissions(administrator=True)
async def roles_list(ctx):
    """Показать настроенные роли"""
    data = load_data()
    roles = data.get("roles", {})
    embed = discord.Embed(title="🎭 Роли бота", color=discord.Color.blue())
    role_names = {"dj": "🎙️ DJ", "vip": "⭐ VIP Listener", "moderator": "🛡️ Moderator", "admin": "👑 Admin"}
    for key, label in role_names.items():
        role_id = roles.get(key)
        value = f"<@&{role_id}>" if role_id else "Не настроена"
        embed.add_field(name=label, value=value, inline=True)
    await ctx.send(embed=embed)

# ─── Статистика ──────────────────────────────────────────────────────────────

@bot.tree.command(name="stats", description="Твоя статистика запросов")
@app_commands.guild_only()
async def slash_stats(interaction: discord.Interaction):
    stats = load_stats()
    uid = str(interaction.user.id)
    user_stats = stats["users"].get(uid, {"count": 0})

    embed = discord.Embed(
        title=f"📊 Статистика {interaction.user.display_name}",
        color=discord.Color.blue()
    )
    embed.add_field(name="Всего запросов", value=str(user_stats["count"]), inline=True)
    embed.set_thumbnail(url=interaction.user.display_avatar.url)

    # Топ треков пользователя — ищем по всем трекам
    # (упрощённо — общий топ треков)
    top_tracks = sorted(stats["tracks"].items(), key=lambda x: x[1], reverse=True)[:3]
    if top_tracks:
        top_text = "\n".join(f"{i}. {t} — {c} раз" for i, (t, c) in enumerate(top_tracks, 1))
        embed.add_field(name="🎵 Топ треков на радио", value=top_text, inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="top", description="Топ треков и слушателей")
@app_commands.guild_only()
async def slash_top(interaction: discord.Interaction):
    stats = load_stats()

    embed = discord.Embed(title="🏆 Топ радио", color=discord.Color.gold())

    # Топ слушателей
    top_users = sorted(stats["users"].items(), key=lambda x: x[1]["count"], reverse=True)[:5]
    if top_users:
        users_text = "\n".join(
            f"{i}. {u['name']} — {u['count']} запросов"
            for i, (_, u) in enumerate(top_users, 1)
        )
        embed.add_field(name="👥 Топ слушателей", value=users_text, inline=False)
    else:
        embed.add_field(name="👥 Топ слушателей", value="Пока нет данных", inline=False)

    # Топ треков
    top_tracks = sorted(stats["tracks"].items(), key=lambda x: x[1], reverse=True)[:5]
    if top_tracks:
        tracks_text = "\n".join(
            f"{i}. {t} — {c} раз"
            for i, (t, c) in enumerate(top_tracks, 1)
        )
        embed.add_field(name="🎵 Топ треков", value=tracks_text, inline=False)
    else:
        embed.add_field(name="🎵 Топ треков", value="Пока нет данных", inline=False)

    await interaction.response.send_message(embed=embed)

# ─── Команды модератора ──────────────────────────────────────────────────────

@bot.tree.command(name="remove_request", description="Удалить трек из очереди RadioBOSS (модератор)")
@app_commands.describe(position="Позиция трека в очереди (1, 2, 3...)")
@app_commands.guild_only()
async def slash_remove_request(interaction: discord.Interaction, position: int):
    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("❌ Только на сервере.", ephemeral=True)
        return

    data = load_data()
    if not is_moderator(interaction.user, data):
        await interaction.response.send_message("❌ Нужна роль Moderator или Admin.", ephemeral=True)
        return

    active_dj_id = data.get("active_dj")
    if not active_dj_id or active_dj_id not in data["djs"]:
        await interaction.response.send_message("❌ Нет активного ведущего.", ephemeral=True)
        return

    dj = data["djs"][active_dj_id]
    await interaction.response.defer(ephemeral=True)

    # Получаем очередь
    queue = await get_songrequest_queue(dj["server"], dj["port"], dj["password"])
    if not queue:
        await interaction.followup.send("❌ Очередь пуста.", ephemeral=True)
        return

    if position < 1 or position > len(queue):
        await interaction.followup.send(f"❌ Позиция должна быть от 1 до {len(queue)}.", ephemeral=True)
        return

    track = queue[position - 1]

    # Очищаем всю очередь и добавляем обратно без удалённого трека
    # RadioBOSS не имеет API удаления из songrequest — очищаем и перезаписываем
    try:
        import xml.etree.ElementTree as ET
        # Получаем полный список с filename
        api_url = f"http://{dj['server']}:{dj['port']}/?pass={dj['password']}&action=songrequestlist"
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=5)) as response:  # type: ignore
                text = await response.text(encoding="utf-8")
        root = ET.fromstring(text)
        all_requests = []
        for i, req in enumerate(root.findall("Request")):
            if req.get("played", "False") == "False":
                all_requests.append({
                    "filename": req.get("filename", ""),
                    "message": req.get("message", "")
                })

        # Очищаем очередь
        clear_url = f"http://{dj['server']}:{dj['port']}/?pass={dj['password']}&action=songrequestclear"
        async with aiohttp.ClientSession() as session:
            await session.get(clear_url, timeout=aiohttp.ClientTimeout(total=5))  # type: ignore

        # Добавляем обратно без удалённого
        removed = all_requests.pop(position - 1)
        for req in all_requests:
            enc_fn = quote(req["filename"])
            enc_msg = quote(req["message"]) if req["message"] else ""
            re_url = f"http://{dj['server']}:{dj['port']}/?pass={dj['password']}&action=songrequest&filename={enc_fn}&message={enc_msg}"
            async with aiohttp.ClientSession() as session:
                await session.get(re_url, timeout=aiohttp.ClientTimeout(total=5))  # type: ignore

        await interaction.followup.send(
            f"✅ Трек **{track['name']}** удалён из очереди.", ephemeral=True
        )

        # Лог
        log_channel_id = data.get("log_channel")
        if log_channel_id and interaction.guild:
            ch = interaction.guild.get_channel(int(log_channel_id))
            if ch and isinstance(ch, discord.TextChannel):
                embed = discord.Embed(
                    title="🗑️ Трек удалён из очереди",
                    description=f"**{track['name']}**",
                    color=discord.Color.red()
                )
                embed.add_field(name="Удалил", value=interaction.user.mention, inline=True)
                embed.set_footer(text=datetime.now().strftime("%H:%M:%S"))
                await ch.send(embed=embed)

    except Exception as e:
        await interaction.followup.send(f"❌ Ошибка: {e}", ephemeral=True)

if __name__ == "__main__":
    try:
        bot.run(BOT_TOKEN)
    except Exception as e:
        print(f"Error starting bot: {e}")
