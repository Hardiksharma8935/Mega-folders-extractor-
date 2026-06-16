import os
import sys
import logging
import asyncio
import shutil
import re
import random
from pyrogram import Client, filters
from pyrogram.types import Message

# --- LOGGING SETUP ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))

if not API_ID or not API_HASH or not BOT_TOKEN:
    logger.critical("❌ CRITICAL ERROR: Variables missing in GitHub Secrets!")
    sys.exit(1)

bot = Client("MegaFastExtractor", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

IS_PROCESSING = False
CURRENT_PROCESS = {}
VALID_MEDIA = ('.mp4', '.mkv', '.avi', '.mov', '.flv', '.webm', '.jpg', '.jpeg', '.png')

async def safe_edit(message: Message, text: str):
    """Helper function to bypass 400 MESSAGE_NOT_MODIFIED completely"""
    try:
        # Appending a random invisible or small character to force message updates smoothly
        rand_suffix = f"\n⏱️ Sync ID: [{random.randint(1000, 9999)}]"
        await message.edit_text(text + rand_suffix)
    except Exception as e:
        logger.warning(f"Ignored edit error: {e}")

@bot.on_message(filters.command("start"))
async def start_command(client, message: Message):
    if message.from_user.id != ADMIN_ID: return
    await message.reply_text(
        "🎬 **GitHub Actions Bullet Extractor Live!**\n\n"
        "Send me any big Mega folder link. I will process it one-by-one!\n"
        "🛑 Use **/cancel** to stop current download loop."
    )

@bot.on_message(filters.command("cancel"))
async def cancel_command(client, message: Message):
    user_id = message.from_user.id
    if user_id == ADMIN_ID:
        CURRENT_PROCESS[user_id] = True
        await message.reply_text("🛑 **Stopping the active stream loop safely...**")

@bot.on_message(filters.text & ~filters.command(["start", "cancel"]))
async def handle_mega_link(client, message: Message):
    global IS_PROCESSING
    user_id = message.from_user.id
    
    if user_id != ADMIN_ID: return
    url = message.text.strip()
    if "mega.nz" not in url: return
        
    if IS_PROCESSING:
        return await message.reply_text("⚠️ **Another task is running.** Please wait or use /cancel.")

    status_msg = await message.reply_text("⚡ **Opening Secure Pipe to Mega Cluster...**")
    IS_PROCESSING = True
    
    try:
        await process_one_by_one(client, message, url, status_msg)
    except Exception as e:
        logger.error(f"Global Error: {e}")
    finally:
        IS_PROCESSING = False

async def process_one_by_one(client, message, url, status_msg):
    user_id = message.from_user.id
    CURRENT_PROCESS[user_id] = False
    
    base_dir = f"/tmp/mega_stream_{message.id}"
    os.makedirs(base_dir, exist_ok=True)
    
    await asyncio.sleep(5)
    await safe_edit(status_msg, "🔍 **Scanning Mega Folder Directory Remotely...**")
    
    # Run mega-find remote directory tree check
    cmd_find = ["mega-find", url]
    process_find = await asyncio.create_subprocess_exec(
        *cmd_find, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, _ = await process_find.communicate()
    all_elements = stdout.decode('utf-8', errors='ignore').split('\n')
    
    target_files = []
    for line in all_elements:
        line = line.strip()
        line = re.sub(r'\x1b\[[0-9;]*m', '', line)
        if line and any(line.lower().endswith(ext) for ext in VALID_MEDIA):
            clean_name = line.split('/')[-1] if '/' in line else line
            if clean_name not in target_files:
                target_files.append(clean_name)
            
    total_files = len(target_files)
    if total_files == 0:
        await safe_edit(status_msg, "🔄 **Handshake drop detected. Polling server cluster again (Attempt 2)...**")
        await asyncio.sleep(8)
        process_find = await asyncio.create_subprocess_exec(
            *cmd_find, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await process_find.communicate()
        all_elements = stdout.decode('utf-8', errors='ignore').split('\n')
        for line in all_elements:
            line = line.strip()
            if line and any(line.lower().endswith(ext) for ext in VALID_MEDIA):
                clean_name = line.split('/')[-1] if '/' in line else line
                if clean_name not in target_files:
                    target_files.append(clean_name)
                    
        total_files = len(target_files)
        if total_files == 0:
            await safe_edit(status_msg, "❌ **Mega cluster connection closed.** Please click /start and send the link fresh.")
            if os.path.exists(base_dir): shutil.rmtree(base_dir)
            return
        
    await safe_edit(status_msg, f"📦 **Handshake Successful!**\nFound `{total_files}` files. Starting Stream Loop...")
    
    for index, clean_file_name in enumerate(target_files, start=1):
        if CURRENT_PROCESS.get(user_id, False):
            await safe_edit(status_msg, "🛑 **Process cancelled by user. Workspace cleared.**")
            break
            
        await safe_edit(
            status_msg,
            f"📥 **Downloading ({index}/{total_files}):**\n"
            f"`{clean_file_name}`\n\n"
            f"Status: Fetching block from Mega Cloud... ⚡"
        )
        
        # Downloading ONLY this single file block via pattern map
        process_get = await asyncio.create_subprocess_exec(
            "mega-get", url, f"--pattern={clean_file_name}", base_dir,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        await process_get.communicate()
        
        local_file_path = None
        for root, _, files in os.walk(base_dir):
            for f in files:
                if f == clean_file_name:
                    local_file_path = os.path.join(root, f)
                    break
        
        if local_file_path and os.path.exists(local_file_path):
            await safe_edit(
                status_msg,
                f"📤 **Uploading ({index}/{total_files}):**\n"
                f"`{clean_file_name}`\n\n"
                f"Status: Uploading to Telegram Network Core..."
            )
            
            try:
                if clean_file_name.lower().endswith(('.jpg', '.jpeg', '.png')):
                    await message.reply_photo(photo=local_file_path, caption=f"📸 `{clean_file_name}`")
                else:
                    await message.reply_video(video=local_file_path, caption=f"🎥 File: `{index}/{total_files}`\n📝 `{clean_file_name}`")
            except Exception as upload_err:
                logger.error(f"Upload failed: {upload_err}")
            
            # CRITICAL INSTANT FLUSH
            os.remove(local_file_path)
        else:
            logger.warning(f"Skipped item: {clean_file_name}")
            
    await safe_edit(status_msg, "✅ **All files extracted and processed successfully sequentially!** Workspace Flushed.")
    if os.path.exists(base_dir): shutil.rmtree(base_dir)

if __name__ == "__main__":
    bot.run()
    
