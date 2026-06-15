import os
import sys
import logging
import asyncio
import shutil
import re
from pyrogram import Client, filters
from pyrogram.types import Message

# --- LOGGING SETUP ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# --- ENVIRONMENT VARIABLES ---
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

@bot.on_message(filters.command("start"))
async def start_command(client, message: Message):
    if message.from_user.id != ADMIN_ID:
        return await message.reply_text("❌ Access Denied.")
    await message.reply_text(
        "🎬 **GitHub Actions Super-Fast Extractor Live!**\n\n"
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

    status_msg = await message.reply_text("⚡ **Waking up Mega-CMD Background Daemon...**")
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
    
    # CRITICAL FIX: Giving 15 seconds warm-up time to Mega daemon so it never rejects handshake
    await asyncio.sleep(15)
    
    await status_msg.edit_text("🔍 **Scanning Mega Folder Directory Remotely...**")
    
    # Remote scan via mega-find
    cmd_find = ["mega-find", url]
    process_find = await asyncio.create_subprocess_exec(
        *cmd_find, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process_find.communicate()
    all_elements = stdout.decode('utf-8', errors='ignore').split('\n')
    
    target_files = []
    for line in all_elements:
        line = line.strip()
        line = re.sub(r'\x1b\[[0-9;]*m', '', line) # Clean terminal colors
        if line and any(line.lower().endswith(ext) for ext in VALID_MEDIA):
            # Extracting only the filename from path
            clean_name = line.split('/')[-1] if '/' in line else line
            if clean_name not in target_files:
                target_files.append(clean_name)
            
    total_files = len(target_files)
    if total_files == 0:
        # Fallback: trying to fetch structure if mega-find was too fast
        await status_msg.edit_text("🔄 **Retrying Connection Handshake (Attempt 2)...**")
        await asyncio.sleep(10)
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
            await status_msg.edit_text("❌ Mega cluster handshake failed. Please try sending the link once again.")
            if os.path.exists(base_dir): shutil.rmtree(base_dir)
            return
        
    await status_msg.edit_text(f"📦 **Handshake Successful!**\nFound `{total_files}` files. Starting Stream Loop...")
    
    for index, clean_file_name in enumerate(target_files, start=1):
        if CURRENT_PROCESS.get(user_id, False):
            try: await status_msg.edit_text("🛑 **Process cancelled by user.**")
            except: pass
            break
            
        try:
            await status_msg.edit_text(
                f"📥 **Downloading ({index}/{total_files}):**\n"
                f"`{clean_file_name}`\n\n"
                f"Status: Fetching from Mega Server... ⚡\n"
                f"⚙️ Core: [ Node-{index} ]"
            )
        except: pass
        
        # Downloading ONLY this single file block
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
            try:
                await status_msg.edit_text(
                    f"📤 **Uploading ({index}/{total_files}):**\n"
                    f"`{clean_file_name}`\n\n"
                    f"Status: Uploading to Telegram...\n"
                    f"⚙️ Core: [ Node-{index} ]"
                )
            except: pass
            
            # Send file
            try:
                if clean_file_name.lower().endswith(('.jpg', '.jpeg', '.png')):
                    await message.reply_photo(photo=local_file_path, caption=f"📸 `{clean_file_name}`")
                else:
                    await message.reply_video(video=local_file_path, caption=f"🎥 File: `{index}/{total_files}`\n📝 `{clean_file_name}`")
            except Exception as upload_err:
                logger.error(f"Upload failed: {upload_err}")
            
            # INSTANT DELETE FROM CONTAINER
            os.remove(local_file_path)
        else:
            logger.warning(f"Skipped item: {clean_file_name}")
            
    try: await status_msg.edit_text("✅ **All files processed successfully sequentially!**")
    except: pass
    if os.path.exists(base_dir): shutil.rmtree(base_dir)

if __name__ == "__main__":
    bot.run()
    
