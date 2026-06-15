import os
import sys
import logging
import asyncio
import shutil
from mega import Mega
from pyrogram import Client, filters
from pyrogram.types import Message
from tenacity import retry, stop_after_attempt, wait_fixed

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
    logger.critical("❌ Variables missing in GitHub Secrets!")
    sys.exit(1)

bot = Client("MegaFastExtractor", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
IS_PROCESSING = False
CURRENT_PROCESS = {}
VALID_MEDIA = ('.mp4', '.mkv', '.avi', '.mov', '.flv', '.webm', '.jpg', '.jpeg', '.png')

@bot.on_message(filters.command("start"))
async def start_command(client, message: Message):
    if message.from_user.id != ADMIN_ID: return
    await message.reply_text("🎬 **GitHub Actions API-Linked Extractor Live!**\n\nSend me a Mega folder link.")

@bot.on_message(filters.command("cancel"))
async def cancel_command(client, message: Message):
    if message.from_user.id == ADMIN_ID:
        CURRENT_PROCESS[message.from_user.id] = True
        await message.reply_text("🛑 Stopping active loop...")

@bot.on_message(filters.text & ~filters.command(["start", "cancel"]))
async def handle_mega_link(client, message: Message):
    global IS_PROCESSING
    if message.from_user.id != ADMIN_ID or "mega.nz" not in message.text: return
    if IS_PROCESSING: return await message.reply_text("⚠️ Wait for current task or /cancel.")

    status_msg = await message.reply_text("🔍 **Parsing Mega Folder via API Node...**")
    IS_PROCESSING = True
    try:
        await process_one_by_one(client, message, message.text.strip(), status_msg)
    except Exception as e:
        logger.error(f"Global Error: {e}")
    finally:
        IS_PROCESSING = False

async def process_one_by_one(client, message, url, status_msg):
    user_id = message.from_user.id
    CURRENT_PROCESS[user_id] = False
    base_dir = f"/tmp/mega_stream_{message.id}"
    os.makedirs(base_dir, exist_ok=True)
    
    # Using mega.py API to extract folder names instantly without daemon bugs
    try:
        mega_api = Mega()
        folder_nodes = mega_api.parse_url(url)
        # Handle if it's a single file link or folder
        files_data = folder_nodes.get('f', []) if isinstance(folder_nodes, dict) else []
        
        target_files = []
        if files_data:
            for node in files_data:
                name = node.get('a', {}).get('n', '')
                if name and any(name.lower().endswith(ext) for ext in VALID_MEDIA):
                    target_files.append(name)
        else:
            # Fallback if single file link
            single_name = folder_nodes.get('a', {}).get('n', '') if isinstance(folder_nodes, dict) else ''
            if single_name: target_files.append(single_name)

        total_files = len(target_files)
        if total_files == 0:
            await status_msg.edit_text("❌ No valid media items found or link is private/empty.")
            return

        await status_msg.edit_text(f"📦 **API Handshake Connected!**\nFound `{total_files}` files. Streaming started...")

        for index, clean_file_name in enumerate(target_files, start=1):
            if CURRENT_PROCESS.get(user_id, False):
                await status_msg.edit_text("🛑 Process cancelled by user.")
                break

            await status_msg.edit_text(
                f"📥 **Downloading ({index}/{total_files}):**\n`{clean_file_name}`\n\n"
                f"Status: Downloading block via Mega Engine..."
            )

            # Retry mechanism for mega-get to survive daemon wakeups
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
                await status_msg.edit_text(f"📤 **Uploading ({index}/{total_files}):**\n`{clean_file_name}`")
                
                if clean_file_name.lower().endswith(('.jpg', '.jpeg', '.png')):
                    await message.reply_photo(photo=local_file_path, caption=f"📸 `{clean_file_name}`")
                else:
                    await message.reply_video(video=local_file_path, caption=f"🎥 File: `{index}/{total_files}`\n\n📝 `{clean_file_name}`")
                
                os.remove(local_file_path)
            else:
                logger.warning(f"Could not download: {clean_file_name}")

        await status_msg.edit_text("✅ **All files processed successfully!** Workspace cleared.")

    except Exception as e:
        await status_msg.edit_text(f"❌ Handshake Error: {str(e)}")
    finally:
        if os.path.exists(base_dir): shutil.rmtree(base_dir)

if __name__ == "__main__":
    bot.run()
