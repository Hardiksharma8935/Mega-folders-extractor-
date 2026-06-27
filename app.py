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
            except Exception as e:
                print(f"Error alerting user: {e}")

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

# --- Core Mega Extractor Logic (Fixed URL Parsing) ---
async def extract_mega_folder(url: str, update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = await update.message.reply_text("🔍 Link parse kiya ja raha hai...")
    try:
        loop = asyncio.get_running_loop()
        
        # Safe URL processing to bypass 'Url key missing' error
        # Agar URL me `#` se split hai toh folder id aur key ko custom handle karenge
        processed_url = url
        if "#" in url and "/folder/" in url:
            # Standard structural formatting for mega.py library
            processed_url = url.replace("#", "!")

        await status_msg.edit_text("📂 Files list fetch ki ja rahi hai...")
        
        # Public URL details read karne ka fail-safe system
        try:
            files = await loop.run_in_executor(None, m.get_files_from_link, processed_url)
        except Exception:
            # Backup method agar standard call fail ho jaye
            public_node = await loop.run_in_executor(None, m.import_public_url, processed_url)
            files = await loop.run_in_executor(None, m.get_files)

        if not files:
            await status_msg.edit_text("❌ Folder khali hai, encrypted hai ya URL sahi nahi hai.")
            return

        media_exts = ('.mp4', '.mkv', '.avi', '.mov', '.mp3', '.jpg', '.jpeg', '.png', '.pdf', '.zip')
        
        # Normalize files data dictionary/list filter
        media_files = []
        if isinstance(files, dict):
            for f_id, f_data in files.items():
                if isinstance(f_data, dict) and 'name' in f_data:
                    if str(f_data['name']).lower().endswith(media_exts):
                        media_files.append(f_data)
                elif isinstance(f_data, dict) and 'n' in f_data:
                    if str(f_data['n']).lower().endswith(media_exts):
                        # standardizer mapping
                        f_data['name'] = f_data['n']
                        media_files.append(f_data)

        total_files = len(media_files)
        if total_files == 0:
            await status_msg.edit_text("❌ Is folder me koi compatible files nahi mili.")
            return

        await status_msg.edit_text(f"📊 **Total Files Found:** {total_files}\n🚀 Extraction shuru ho raha hai...")
        success_count = 0
        
        for index, file in enumerate(media_files, start=1):
            file_name = file.get('name', f"File_{index}")
            await status_msg.edit_text(f"📥 **Downloading & Sending:** {index}/{total_files}\n🔹 `{file_name}`")
            
            try:
                # Execution in thread pool to prevent blocking telegram polling loop
                file_path = await loop.run_in_executor(None, m.download_file_by_link, processed_url, file)
                
                # Check agar default path par nahi mili toh system check karega
                if not file_path or not os.path.exists(file_path):
                    if os.path.exists(file_name):
                        file_path = file_name

                if file_path and os.path.exists(file_path):
                    with open(file_path, 'rb') as doc:
                        await update.message.reply_document(document=doc, filename=file_name)
                    os.remove(file_path)
                    success_count += 1
            except Exception as fe:
                print(f"Skipping corrupt file {file_name}: {fe}")
                continue

        await update.message.reply_text(f"🏁 **Extraction Complete!**\nTotal {success_count} files successfully bhej di gayi hain.")
    except Exception as e:
        await status_msg.edit_text(f"❌ Extraction Error: {str(e)}")

# --- Main Boot ---
def main():
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_click))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("Bot is successfully running on Railway...")
    application.run_polling()

if __name__ == '__main__':
    main()
    
