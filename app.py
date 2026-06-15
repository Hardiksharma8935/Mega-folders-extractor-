import os
import sys
import logging
import asyncio
import shutil
import time
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import UserNotParticipant

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# --- SAFE ENVIRONMENT LOADING ---
# Agar variable khali hoga to yeh crash nahi karega, balki log me batayega
API_ID_ENV = os.environ.get("API_ID", "").strip()
API_ID = int(API_ID_ENV) if API_ID_ENV.isdigit() else 0
API_HASH = os.environ.get("API_HASH", "").strip()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

ADMIN_ID = int(os.environ.get("ADMIN_ID", "0").strip() or 0)
CHANNEL_USERNAME = os.environ.get("CHANNEL_USERNAME", "").strip()

if not API_ID or not API_HASH or not BOT_TOKEN:
    logger.critical("❌ CRITICAL ERROR: API_ID, API_HASH, or BOT_TOKEN is missing or invalid in Environment Variables!")
    sys.exit(1)

bot = Client("MegaDockerExtractor", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

APPROVED_USERS = set([ADMIN_ID])
download_queue = asyncio.Queue()
is_processing = False
VALID_MEDIA = ('.mp4', '.mkv', '.avi', '.mov', '.flv', '.webm', '.jpg', '.jpeg', '.png')
CURRENT_PROCESS = {}

async def check_force_join(client, user_id):
    if not CHANNEL_USERNAME: return True
    try:
        member = await client.get_chat_member(CHANNEL_USERNAME, user_id)
        if member.status in ["member", "administrator", "creator"]: return True
    except UserNotParticipant: return False
    except Exception as e:
        logger.error(f"Force Join Error: {e}")
        return True
    return False

def human_size(size_bytes):
    if size_bytes == 0: return "0B"
    import math
    size_name = ("B", "KB", "MB", "GB", "TB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_name[i]}"

@bot.on_message(filters.command("start"))
async def start_command(client, message: Message):
    user_id = message.from_user.id
    if not await check_force_join(client, user_id):
        return await message.reply_text(
            f"❌ **Access Denied!**\n\nYou must join our channel to use this bot.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{CHANNEL_USERNAME}")
            ]])
        )
    if user_id not in APPROVED_USERS:
        await client.send_message(
            chat_id=ADMIN_ID,
            text=f"🔔 **New Request!**\n👤 Name: {message.from_user.first_name}\n🆔 ID: `{user_id}`",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Approve", callback_data=f"approve_{user_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"reject_{user_id}")
            ]])
        )
        return await message.reply_text("⏳ Your request sent to Admin. Please wait.")
    await message.reply_text("🎬 **Mega Advanced Extractor Live!**\nSend me a Mega link.")

@bot.on_message(filters.command("cancel"))
async def cancel_command(client, message: Message):
    user_id = message.from_user.id
    if user_id in CURRENT_PROCESS:
        CURRENT_PROCESS[user_id] = True
        await message.reply_text("🛑 Stopping process...")
    else:
        await message.reply_text("ℹ️ No active task found.")

@bot.on_callback_query(filters.regex(r"^(approve|reject)_\d+"))
async def admin_callback(client, callback_query):
    action, user_id = callback_query.data.split("_")
    user_id = int(user_id)
    if callback_query.from_user.id != ADMIN_ID: return
    if action == "approve":
        APPROVED_USERS.add(user_id)
        await callback_query.edit_message_text(f"✅ User `{user_id}` approved!")
        try: await client.send_message(user_id, "🎉 Approved! You can use the bot now.")
        except: pass
    else:
        await callback_query.edit_message_text(f"❌ User `{user_id}` rejected.")

@bot.on_message(filters.text & ~filters.command(["start", "cancel"]))
async def handle_link(client, message: Message):
    user_id = message.from_user.id
    url = message.text.strip()
    if "mega.nz" not in url: return
    if not await check_force_join(client, user_id): return
    if user_id not in APPROVED_USERS: return
    status_msg = await message.reply_text("📝 Added to Queue. Please wait...")
    await download_queue.put((client, message, url, status_msg))

async def queue_worker():
    global is_processing
    while True:
        client, message, url, status_msg = await download_queue.get()
        is_processing = True
        try: await process_mega_link(client, message, url, status_msg)
        except Exception as e: logger.error(f"Queue Error: {e}")
        finally:
            is_processing = False
            download_queue.task_done()

async def process_mega_link(client, message, url, status_msg):
    user_id = message.from_user.id
    CURRENT_PROCESS[user_id] = False
    
    # RAILWAY CRASH FIX: /tmp folder uses virtual RAM memory to prevent disk crash
    download_dir = f"/tmp/download_{message.id}"
    os.makedirs(download_dir, exist_ok=True)
    
    await status_msg.edit_text("🔍 Connecting to Mega Engine...")
    start_time = time.time()
    
    try:
        process = await asyncio.create_subprocess_exec(
            "mega-get", url, download_dir,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        
        while process.returncode is None:
            if CURRENT_PROCESS.get(user_id, False):
                process.terminate()
                break
            file_count = 0
            total_size = 0
            for root, _, files in os.walk(download_dir):
                for f in files:
                    if f.lower().endswith(VALID_MEDIA):
                        file_count += 1
                        total_size += os.path.getsize(os.path.join(root, f))
            elapsed = time.time() - start_time
            speed = total_size / elapsed if elapsed > 0 else 0
            try:
                await status_msg.edit_text(
                    f"📥 **Downloading...**\n📂 Files: {file_count}\n📦 Size: {human_size(total_size)}\n⚡ Speed: {human_size(speed)}/s"
                )
            except: pass
            await asyncio.sleep(5)
            
        await process.communicate()
        if CURRENT_PROCESS.get(user_id, False):
            if os.path.exists(download_dir): shutil.rmtree(download_dir)
            return

        media_files = []
        for root, _, files in os.walk(download_dir):
            for file in files:
                if file.lower().endswith(VALID_MEDIA):
                    media_files.append(os.path.join(root, file))

        if not media_files:
            await status_msg.edit_text("❌ No valid media found.")
            if os.path.exists(download_dir): shutil.rmtree(download_dir)
            return

        await status_msg.edit_text(f"📦 Found {len(media_files)} files. Uploading...")
        for index, file_path in enumerate(media_files, start=1):
            if CURRENT_PROCESS.get(user_id, False): break
            file_name = os.path.basename(file_path)
            await status_msg.edit_text(f"📤 Uploading ({index}/{len(media_files)}):\n`{file_name}`")
            if file_name.lower().endswith(('.jpg', '.jpeg', '.png')):
                await message.reply_photo(photo=file_path, caption=f"📸 `{file_name}`")
            else:
                await message.reply_video(video=file_path, caption=f"🎥 `{file_name}`")
            try: os.remove(file_path)
            except: pass

        await status_msg.edit_text("✅ Task Completed!")
    except Exception as e:
        await status_msg.edit_text(f"❌ Error: {str(e)}")
    finally:
        if os.path.exists(download_dir): shutil.rmtree(download_dir)
        CURRENT_PROCESS.pop(user_id, None)

# --- FIX FOR RAILWAY DOUBLE INITIALIZATION CRASH ---
if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    
    # Pehle background task loop ready karenge
    loop.create_task(queue_worker())
    
    logger.info("🤖 Starting Pyrogram Client Safely...")
    # Phir bot ko idle mode me run karenge jo Railway ke liye custom main loop handle karta hai
    bot.run()
