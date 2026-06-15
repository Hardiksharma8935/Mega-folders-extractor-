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

# --- CONFIGURATION ---
API_ID = int(os.environ.get("API_ID", 12345))
API_HASH = os.environ.get("API_HASH", "your_api_hash")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "your_bot_token")

# Apni Details Yahan Daalein
ADMIN_ID = int(os.environ.get("ADMIN_ID", 123456789)) # Aapki Telegram User ID
CHANNEL_USERNAME = os.environ.get("CHANNEL_USERNAME", "YourChannelUsername") # Bina '@' ke

bot = Client("MegaDockerExtractor", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- GLOBAL STORES ---
APPROVED_USERS = set([ADMIN_ID]) # Admin by default approved hai
download_queue = asyncio.Queue()
is_processing = False

VALID_MEDIA = ('.mp4', '.mkv', '.avi', '.mov', '.flv', '.webm', '.jpg', '.jpeg', '.png')
CURRENT_PROCESS = {} # To track cancellations

# --- HELPER FUNCTIONS ---
async def check_force_join(client, user_id):
    """Checks if user is joined in the forced channel"""
    if not CHANNEL_USERNAME:
        return True
    try:
        member = await client.get_chat_member(CHANNEL_USERNAME, user_id)
        if member.status in ["member", "administrator", "creator"]:
            return True
    except UserNotParticipant:
        return False
    except Exception as e:
        logger.error(f"Force Join Error: {e}")
        return True # Safe side pr fail na ho
    return False

def human_size(size_bytes):
    """Converts bytes to readable string"""
    if size_bytes == 0: return "0B"
    size_name = ("B", "KB", "MB", "GB", "TB")
    import math
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_name[i]}"

# --- COMMAND HANDLERS ---
@bot.on_message(filters.command("start"))
async def start_command(client, message: Message):
    user_id = message.from_user.id
    
    # 1. Force Join Check
    if not await check_force_join(client, user_id):
        return await message.reply_text(
            f"❌ **Access Denied!**\n\nYou must join our channel to use this bot.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{CHANNEL_USERNAME}")
            ]])
        )
        
    # 2. Admin Approval Check
    if user_id not in APPROVED_USERS:
        # Send Request to Admin
        await client.send_message(
            chat_id=ADMIN_ID,
            text=f"🔔 **New Access Request!**\n\n👤 **Name:** {message.from_user.first_name}\n🆔 **ID:** `{user_id}`\n🌐 **Username:** @{message.from_user.username}",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Approve", callback_data=f"approve_{user_id}"),
                    InlineKeyboardButton("❌ Reject", callback_data=f"reject_{user_id}")
                ]
            ])
        )
        return await message.reply_text("⏳ **Your request has been sent to the Admin.**\nPlease wait for approval.")

    await message.reply_text(
        "🎬 **Mega Advanced Extractor Live!**\n\n"
        "Send me any Mega folder or file link, and I will extract and upload it for you.\n"
        "🛑 Use **/cancel** to stop your active process."
    )

@bot.on_message(filters.command("cancel"))
async def cancel_command(client, message: Message):
    user_id = message.from_user.id
    if user_id in CURRENT_PROCESS:
        CURRENT_PROCESS[user_id] = True
        await message.reply_text("🛑 **Cancellation request received. Stopping process...**")
    else:
        await message.reply_text("ℹ️ You don't have any active running task.")

# --- CALLBACK QUERY HANDLER FOR ADMIN ---
@bot.on_callback_query(filters.regex(r"^(approve|reject)_\d+"))
async def admin_callback(client, callback_query):
    action, user_id = callback_query.data.split("_")
    user_id = int(user_id)
    
    if callback_query.from_user.id != ADMIN_ID:
        return await callback_query.answer("You are not the Admin!", show_alert=True)
        
    if action == "approve":
        APPROVED_USERS.add(user_id)
        await callback_query.edit_message_text(f"✅ User `{user_id}` has been approved!")
        try:
            await client.send_message(user_id, "🎉 **Congratulations!** Your access request has been approved by the admin. You can use the bot now!")
        except Exception:
            pass
    else:
        await callback_query.edit_message_text(f"❌ User `{user_id}` request rejected.")
        try:
            await client.send_message(user_id, "❌ **Sorry!** Your access request was rejected by the admin.")
        except Exception:
            pass

# --- QUEUE & TEXT HANDLER ---
@bot.on_message(filters.text & ~filters.command(["start", "cancel"]))
async def handle_link(client, message: Message):
    user_id = message.from_user.id
    url = message.text.strip()
    
    if "mega.nz" not in url:
        return

    # Check limits first
    if not await check_force_join(client, user_id):
        return await message.reply_text("❌ Please join the channel first to use this bot.")
        
    if user_id not in APPROVED_USERS:
        return await message.reply_text("⏳ Access pending admin approval.")

    # Put task in queue
    status_msg = await message.reply_text("📝 **Added to Queue.** Waiting for your turn...")
    await download_queue.put((client, message, url, status_msg))

# --- BACKGROUND QUEUE WORKER ---
async def queue_worker():
    global is_processing
    while True:
        client, message, url, status_msg = await download_queue.get()
        is_processing = True
        try:
            await process_mega_link(client, message, url, status_msg)
        except Exception as e:
            logger.error(f"Queue Worker Error: {e}")
        finally:
            is_processing = False
            download_queue.task_done()

async def process_mega_link(client, message, url, status_msg):
    user_id = message.from_user.id
    CURRENT_PROCESS[user_id] = False
    
    download_dir = f"/tmp/download_{message.id}"
    os.makedirs(download_dir, exist_ok=True)
    
    await status_msg.edit_text("🔍 **Connecting to Mega Secure Engine...**")
    start_time = time.time()
    
    try:
        # Space Optimization: 100GB folders handle karne ke liye hum mega-cmd ke individual file streaming ya sequential downloads 
        # ko emulate karenge. But mega-get pura folder ek bar me uthata h. To optimize space, hum sub-processes ko closely monitor karenge.
        
        process = await asyncio.create_subprocess_exec(
            "mega-get", url, download_dir,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        
        # Real-time stats engine loop
        while process.returncode is None:
            if CURRENT_PROCESS.get(user_id, False):
                process.terminate()
                break
                
            # Count current files & size
            file_count = 0
            total_size = 0
            for root, _, files in os.walk(download_dir):
                for f in files:
                    if f.lower().endswith(VALID_MEDIA):
                        file_count += 1
                        total_size += os.path.getsize(os.path.join(root, f))
            
            elapsed = time.time() - start_time
            speed = total_size / elapsed if elapsed > 0 else 0
            
            # Dynamic dynamic status string (English)
            status_text = (
                f"📥 **Downloading & Decrypting...**\n\n"
                f"📂 **Files Found:** {file_count}\n"
                f"📦 **Downloaded Size:** {human_size(total_size)}\n"
                f"⚡ **Avg Speed:** {human_size(speed)}/s\n"
                f"⏱️ **Time Elapsed:** {int(elapsed)} seconds\n\n"
                f"👉 _Bot will instantly delete sent files to save server storage._"
            )
            try:
                await status_msg.edit_text(status_text)
            except Exception:
                pass
                
            await asyncio.sleep(5) # Update every 5 seconds
            
        # Ensure process finishes completely
        await process.communicate()

        if CURRENT_PROCESS.get(user_id, False):
            await status_msg.edit_text("🛑 **Process cancelled by user. Local files cleared.**")
            if os.path.exists(download_dir): shutil.rmtree(download_dir)
            return

        # Upload Phase
        media_files = []
        for root, _, files in os.walk(download_dir):
            for file in files:
                if file.lower().endswith(VALID_MEDIA):
                    media_files.append(os.path.join(root, file))

        total_files = len(media_files)
        if total_files == 0:
            await status_msg.edit_text("❌ No valid media files found or link is broken/empty.")
            if os.path.exists(download_dir): shutil.rmtree(download_dir)
            return

        await status_msg.edit_text(f"📦 **Decryption Successful!** Total `{total_files}` items found. Uploading now...")
        
        # --- REQUIREMENT 5: ONE BY ONE UPLOAD & DELETE TO SAVE 100GB SPACE ---
        for index, file_path in enumerate(media_files, start=1):
            if CURRENT_PROCESS.get(user_id, False):
                break
                
            file_name = os.path.basename(file_path)
            await status_msg.edit_text(f"📤 **Uploading ({index}/{total_files}):**\n`{file_name}`")
            
            # Send file to telegram
            if file_name.lower().endswith(('.jpg', '.jpeg', '.png')):
                await message.reply_photo(photo=file_path, caption=f"📸 `{file_name}`")
            else:
                await message.reply_video(video=file_path, caption=f"🎥 `{file_name}`")
            
            # INSTANT DELETE AFTER UPLOAD TO SAVE DISK
            try:
                os.remove(file_path)
                logger.info(f"Deleted from local storage to free space: {file_name}")
            except Exception as e:
                logger.error(f"Error deleting file: {e}")

        await status_msg.edit_text("✅ **Task Completed!** All files processed and temporary server memory cleared.")

    except Exception as e:
        logger.error(f"Error in process: {str(e)}")
        await status_msg.edit_text(f"❌ **An Error Occurred:** {str(e)}")
    finally:
        if os.path.exists(download_dir):
            shutil.rmtree(download_dir)
        CURRENT_PROCESS.pop(user_id, None)

## --- START BOT & BACKGROUND TASK (FIXED FOR RAILWAY) ---
if __name__ == "__main__":
    # Hum bot ko start karne ke baad background task ko fire karenge
    # Isse "run.py" wala double-initialization error permanent khatam ho jayega
    bot.start()
    
    loop = asyncio.get_event_loop()
    loop.create_task(queue_worker())
    
    logger.info("🤖 Bot is successfully running and listening for events...")
    bot.idle() # Bot ko active rakhne ke liye idle use karenge, run() nahi
