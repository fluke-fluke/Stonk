import os
import tempfile
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
import yt_dlp
from collections import deque
import asyncio

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

SONG_QUEUES = {}

def get_cookies_file():
    cookies_content = os.getenv("YOUTUBE_COOKIES")
    if not cookies_content:
        print("❌ ไม่พบ YOUTUBE_COOKIES environment variable")
        return None
    print(f"✅ พบ YOUTUBE_COOKIES ขนาด {len(cookies_content)} ตัวอักษร")
    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
    tmp.write(cookies_content)
    tmp.close()
    return tmp.name

async def search_ytdlp_async(query, ydl_opts):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: _extract(query, ydl_opts))

def _extract(query, ydl_opts):
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(query, download=False)

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"{bot.user} is online!")


@bot.tree.command(name="play", description="Play a song or add it to the queue.")
@app_commands.describe(song_query="Search query or URL")
async def play(interaction: discord.Interaction, song_query: str):
    await interaction.response.defer()

    if interaction.user.voice is None:
        await interaction.followup.send("You must be in a voice channel.")
        return

    voice_channel = interaction.user.voice.channel
    voice_client = interaction.guild.voice_client

    try:
        if voice_client is None:
            voice_client = await voice_channel.connect()
        elif voice_channel != voice_client.channel:
            await voice_client.move_to(voice_channel)
    except Exception as e:
        await interaction.followup.send(f"ไม่สามารถเชื่อมต่อ voice channel ได้: {e}")
        return

    cookies_file = get_cookies_file()

    ydl_options = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "youtube_include_dash_manifest": False,
        "youtube_include_hls_manifest": False,
    }

    if cookies_file:
        ydl_options["cookiefile"] = cookies_file

    try:
        is_url = song_query.startswith("http://") or song_query.startswith("https://")
        query = song_query if is_url else "ytsearch1: " + song_query

        results = await asyncio.wait_for(
            search_ytdlp_async(query, ydl_options),
            timeout=30
        )

        if is_url:
            first_track = results
            audio_url = first_track["url"]
            title = first_track.get("title", "Untitled")
        else:
            tracks = results.get("entries", [])
            if not tracks:
                await interaction.followup.send("ไม่พบเพลงที่ค้นหาครับ")
                return
            first_track = tracks[0]
            audio_url = first_track["url"]
            title = first_track.get("title", "Untitled")

    except asyncio.TimeoutError:
        await interaction.followup.send("ค้นหานานเกินไป ลองใหม่อีกครั้งครับ")
        return
    except Exception as e:
        await interaction.followup.send(f"เกิดข้อผิดพลาดในการค้นหา: {e}")
        return
    finally:
        if cookies_file and os.path.exists(cookies_file):
            os.unlink(cookies_file)

    guild_id = str(interaction.guild_id)
    if SONG_QUEUES.get(guild_id) is None:
        SONG_QUEUES[guild_id] = deque()

    SONG_QUEUES[guild_id].append((audio_url, title))

    if voice_client.is_playing() or voice_client.is_paused():
        await interaction.followup.send(f"เพิ่มในคิว: **{title}**")
    else:
        await interaction.followup.send(f"กำลังเล่น: **{title}**")
        await play_next_song(voice_client, guild_id, interaction.channel)


@bot.tree.command(name="skip", description="Skips the current playing song")
async def skip(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    if voice_client and (voice_client.is_playing() or voice_client.is_paused()):
        voice_client.stop()
        await interaction.response.send_message("ข้ามเพลงแล้วครับ")
    else:
        await interaction.response.send_message("ไม่มีเพลงที่กำลังเล่นอยู่ครับ")


@bot.tree.command(name="pause", description="Pause the currently playing song.")
async def pause(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client

    if voice_client is None:
        return await interaction.response.send_message("บอทไม่ได้อยู่ใน voice channel ครับ")

    if not voice_client.is_playing():
        return await interaction.response.send_message("ไม่มีเพลงที่กำลังเล่นอยู่ครับ")

    voice_client.pause()
    await interaction.response.send_message("หยุดชั่วคราวแล้วครับ")


@bot.tree.command(name="resume", description="Resume the currently paused song.")
async def resume(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client

    if voice_client is None:
        return await interaction.response.send_message("บอทไม่ได้อยู่ใน voice channel ครับ")

    if not voice_client.is_paused():
        return await interaction.response.send_message("ไม่ได้หยุดอยู่ครับ")

    voice_client.resume()
    await interaction.response.send_message("เล่นต่อแล้วครับ")


@bot.tree.command(name="stop", description="Stop playback and clear the queue.")
async def stop(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client

    if not voice_client or not voice_client.is_connected():
        return await interaction.response.send_message("บอทไม่ได้เชื่อมต่ออยู่ครับ")

    guild_id_str = str(interaction.guild_id)
    if guild_id_str in SONG_QUEUES:
        SONG_QUEUES[guild_id_str].clear()

    if voice_client.is_playing() or voice_client.is_paused():
        voice_client.stop()

    await voice_client.disconnect()
    await interaction.response.send_message("หยุดเล่นและออกจาก voice channel แล้วครับ")


@bot.tree.command(name="queue", description="Show the current song queue.")
async def queue(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    q = SONG_QUEUES.get(guild_id)

    if not q:
        return await interaction.response.send_message("คิวว่างอยู่ครับ")

    lines = [f"{i+1}. {title}" for i, (_, title) in enumerate(q)]
    await interaction.response.send_message("**คิวเพลง:**\n" + "\n".join(lines))


async def play_next_song(voice_client, guild_id, channel):
    if SONG_QUEUES.get(guild_id):
        audio_url, title = SONG_QUEUES[guild_id].popleft()

        ffmpeg_options = {
            "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            "options": "-vn -c:a libopus -b:a 96k",
        }

        try:
            source = discord.FFmpegOpusAudio(
                audio_url,
                **ffmpeg_options
            )
        except Exception as e:
            await channel.send(f"เกิดข้อผิดพลาดในการโหลดเพลง: {e}")
            asyncio.run_coroutine_threadsafe(play_next_song(voice_client, guild_id, channel), bot.loop)
            return

        def after_play(error):
            if error:
                print(f"Error: {error}")
            asyncio.run_coroutine_threadsafe(play_next_song(voice_client, guild_id, channel), bot.loop)

        voice_client.play(source, after=after_play)
        await channel.send(f"กำลังเล่น: **{title}**")
    else:
        await voice_client.disconnect()
        SONG_QUEUES[guild_id] = deque()


bot.run(TOKEN)
