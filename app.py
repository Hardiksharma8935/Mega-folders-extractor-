import os
import asyncio
import collections
import shutil
import uuid
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID")) if os.getenv("ADMIN_ID") else None

approved_users = set([ADMIN_ID]) if ADMIN_ID else set()
pending_approvals = {}
extraction_queue = collections.deque()
is_processing = False

# --- Queue Processor ---
async def process_queue(context: ContextTypes.DEFAULT_TYPE):
    global is_processing
    if is_processing or not extraction_queue:
        return

    is_processing = True
    user_id, mega_link, update = extraction_queue.popleft()
    await update.message.reply_text("⏳ Aapka number aa gaya hai! Extraction start ho raha hai...")
    await extract_mega_folder(mega_link, update, context)
    is_processing = False
    asyncio.create_task(process_queue(context))

# --- Command Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id in approved_users:
        await update.message.reply_text(f"👋 Hello {user.first_name}! Main taiyar hoon. Mujhe MEGA folder ka link bhejo.")
    else:
        pending_approvals[user.id] = update
        await update.message.reply_text("🔒 Aapko is bot ko use karne ki permission nahi hai. Admin ko approval request bhej di gayi hai.")
        
        if ADMIN_ID:
            keyboard = [[InlineKeyboardButton("✅ Approve Now", callback_data=f"approve_{user.id}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"👤 **New User Request:**\nName: {user.first_name}\nID: `{user.id}`",
                reply_markup=reply_markup
            )

# --- Button Click Handler ---
async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if update.effective_user.id != ADMIN_ID:
        return

    data = query.data
    if data.startswith("approve_"):
        user_id = int(data.split("_")[1])
        approved_users.add(user_id)
        await query.edit_message_text(text=f"✅ User `{user_id}` ko successfully approve kar diya gaya hai!")
        if user_id in pending_approvals:
            try:
                await context.bot.send_message(chat_id=user_id, text="🎉 Good News! Admin ne aapka request approve kar diya hai. Ab aap MEGA link bhej sakte hain.")
                del pending_approvals[user_id]
            except Exception:
                pass

# --- Message Handler ---
async def handle_message(update: Update, update_context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in approved_users:
        await update.message.reply_text("❌ Pehle `/start` dabayein aur Admin ke approval ka wait karein.")
        return

    text = update.message.text.strip()
    if "mega.nz" in text:
        extraction_queue.append((user_id, text, update))
        position = len(extraction_queue)
        if is_processing or position > 1:
            await update.message.reply_text(f"⏳ Abhi kisi aur ka extraction chal raha hai. Aapko **Queue Position #{position - 1}** par rakha gaya hai.")
        else:
            asyncio.create_task(process_queue(update_context))
    else:
        await update.message.reply_text("⚠️ Please ek valid MEGA folder link bhejiye.")

# --- Naya Smart URL Fixer ---
def format_mega_url(url: str) -> str:
    url = url.strip()
    # Naye /folder/ format ko purane format me badalta hai taaki megatools samajh sake
    if '/folder/' in url and '#' in url:
        parts = url.split('/folder/')[1].split('#')
        return f"https://mega.nz/#F!{parts[0]}!{parts[1]}"
    if '/file/' in url and '#' in url:
        parts = url.split('/file/')[1].split('#')
        return f"https://mega.nz/#!{parts[0]}!{parts[1]}"
    return url

# --- Core Mega Extractor Logic (Native Megatools) ---
async def extract_mega_folder(url: str, update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = await update.message.reply_text("🔍 Link setup kiya ja raha hai...")
    
    clean_url = format_mega_url(url)
    task_id = str(uuid.uuid4())
    download_dir = f"./downloads/{task_id}"
    os.makedirs(download_dir, exist_ok=True)
    
    try:
        await status_msg.edit_text("⏳ Server folder download kar raha hai... (Isme file ke size ke hisab se waqt lag sakta hai)")
        
        # Railway ke asli Linux engine (megatools) ka istemal
        process = await asyncio.create_subprocess_exec(
            "megatools", "dl", "--path", download_dir, clean_url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            err = stderr.decode().strip() or stdout.decode().strip()
            await status_msg.edit_text(f"❌ Download failed: {err}")
            return
        
        await status_msg.edit_text("📂 Download complete! Ab ek-ek karke Telegram par bhej raha hoon...")
        
        # Supported formats
        media_exts = ('.mp4', '.mkv', '.avi', '.mov', '.mp3', '.jpg', '.jpeg', '.png', '.pdf', '.zip')
        success_count = 0
        
        # Folder scan karke files bhejna
        for root, _, files in os.walk(download_dir):
            for file_name in files:
                if file_name.lower().endswith(media_exts):
                    file_path = os.path.join(root, file_name)
                    
                    await status_msg.edit_text(f"📤 Uploading to Telegram:\n🔹 `{file_name}`")
                    
                    try:
                        with open(file_path, 'rb') as doc:
                            await update.message.reply_document(document=doc, filename=file_name)
                        success_count += 1
                    except Exception as e:
                        print(f"Error sending file {file_name}: {e}")
        
        if success_count > 0:
            await update.message.reply_text(f"🏁 **Extraction Complete!**\nTotal {success_count} files aapko bhej di gayi hain.")
        else:
            await status_msg.edit_text("❌ Is MEGA folder me koi media files (.mp4, .jpg, etc.) nahi mili.")
            
    except Exception as e:
        await status_msg.edit_text(f"❌ Server Error: {str(e)}")
    finally:
        # Server par kachra jama na ho isliye temporary files delete karna
        shutil.rmtree(download_dir, ignore_errors=True)

# --- Main Boot ---
def main():
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_click))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("Bot is successfully running on Railway with Megatools engine...")
    application.run_polling()

if __name__ == '__main__':
    main()
    
