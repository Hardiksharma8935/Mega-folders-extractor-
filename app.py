import os
import asyncio
import collections
import re
from mega import Mega
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID")) if os.getenv("ADMIN_ID") else None

approved_users = set([ADMIN_ID]) if ADMIN_ID else set()
pending_approvals = {}
extraction_queue = collections.deque()
is_processing = False

mega = Mega()
m = mega.login()

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
        
        # One-Click Inline Button ke sath request bhejna Admin ko
        if ADMIN_ID:
            keyboard = [[InlineKeyboardButton("✅ Approve Now", callback_data=f"approve_{user.id}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"👤 **New User Request:**\nName: {user.first_name}\nID: `{user.id}`",
                reply_markup=reply_markup
            )

# --- Button Click (Callback Query) Handler ---
async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if update.effective_user.id != ADMIN_ID:
        return

    # Callback data se user_id nikalna
    data = query.data
    if data.startswith("approve_"):
        user_id = int(data.split("_")[1])
        approved_users.add(user_id)
        
        # Admin ke chat me text update karna
        await query.edit_message_text(text=f"✅ User `{user_id}` ko successfully approve kar diya gaya hai!")
        
        # User ko alert bhejna
        if user_id in pending_approvals:
            try:
                await context.bot.send_message(chat_id=user_id, text="🎉 Good News! Admin ne aapka request approve kar diya hai. Ab aap MEGA link bhej sakte hain.")
                del pending_approvals[user_id]
            except Exception as e:
                print(f"User ko text bhejne me error: {e}")

# --- Message & Link Handler ---
async def handle_message(update: Update, update_context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in approved_users:
        await update.message.reply_text("❌ Pehle `/start` dabayein aur Admin ke approval ka wait karein.")
        return

    text = update.message.text
    if "mega.nz" in text:
        extraction_queue.append((user_id, text, update))
        position = len(extraction_queue)
        if is_processing or position > 1:
            await update.message.reply_text(f"⏳ Abhi kisi aur ka extraction chal raha hai. Aapko **Queue Position #{position - 1}** par rakha gaya hai.")
        else:
            asyncio.create_task(process_queue(update_context))
    else:
        await update.message.reply_text("⚠️ Please ek valid MEGA folder link bhejiye.")

# --- Core Mega Extractor Logic (Fixed Attribute Error) ---
async def extract_mega_folder(url: str, update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = await update.message.reply_text("🔍 Link check kiya ja raha hai aur files dhundhi ja rahi hain...")
    try:
        loop = asyncio.get_running_loop()
        
        # Fixed: m.get_files_from_link ko hata kar public URL attributes extract karne ka sahi tarika
        # Mega library me import_public_url use hota hai nodes read karne ke liye
        try:
            public_node = await loop.run_in_executor(None, m.import_public_url, url)
            files = await loop.run_in_executor(None, m.get_files)
        except Exception:
            # Agar folder structural data alag hai toh details dict format se uthayenge
            details = await loop.run_in_executor(None, m.get_public_url_info, url)
            files = details.get('f', []) if isinstance(details, dict) else []

        if not files:
            await status_msg.edit_text("❌ Folder khali hai, encrypted hai ya link active nahi hai.")
            return

        media_exts = ('.mp4', '.mkv', '.avi', '.mov', '.mp3', '.jpg', '.jpeg', '.png')
        
        # Files structure format handler
        media_files = []
        if isinstance(files, dict):
            media_files = [f for f in files.values() if isinstance(f, dict) and 'name' in f and str(f['name']).lower().endswith(media_exts)]
        elif isinstance(files, list):
            media_files = [f for f in files if isinstance(f, dict) and 'h' in f and str(f.get('n', '')).lower().endswith(media_exts)]

        total_files = len(media_files)
        if total_files == 0:
            await status_msg.edit_text("❌ Is folder me koi compatible media files nahi mili.")
            return

        await status_msg.edit_text(f"📊 **Total Media Files Found:** {total_files}\n🚀 Extraction shuru ho raha hai...")
        success_count = 0
        
        for index, file in enumerate(media_files, start=1):
            file_name = file.get('name', file.get('n', f"File_{index}"))
            
            await status_msg.edit_text(f"📥 **Processing:** {index}/{total_files}\n🔹 `{file_name}`")
            
            try:
                # File download pipeline
                file_path = await loop.run_in_executor(None, m.download_file_by_link, url, file)
                if file_path and os.path.exists(file_path):
                    with open(file_path, 'rb') as doc:
                        await update.message.reply_document(document=doc, filename=file_name)
                    os.remove(file_path)
                    success_count += 1
            except Exception as fe:
                print(f"Error skipping file {file_name}: {fe}")
                continue

        await update.message.reply_text(f"🏁 **Extraction Complete!**\nTotal {success_count} files successfully bhej di gayi hain.")
    except Exception as e:
        await status_msg.edit_text(f"❌ Extraction Error: {str(e)}")

# --- Main Boot ---
def main():
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    # Inline button click handle karne ke liye handler
    application.add_handler(CallbackQueryHandler(button_click))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("Bot is successfully running on Railway...")
    application.run_polling()

if __name__ == '__main__':
    main()
    
