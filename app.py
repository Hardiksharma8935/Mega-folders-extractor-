import os
import asyncio
import collections
import shutil
import uuid
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.request import HTTPXRequest

# --- Config ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID")) if os.getenv("ADMIN_ID") else None

approved_users = set([ADMIN_ID]) if ADMIN_ID else set()
all_users = set() 
pending_approvals = {}
extraction_queue = collections.deque()
current_process = None
is_processing = False

# --- Utilities ---
def format_mega_url(url: str) -> str:
    url = url.strip()
    if '/folder/' in url and '#' in url:
        parts = url.split('/folder/')[1].split('#')
        return f"https://mega.nz/#F!{parts[0]}!{parts[1]}"
    return url

# --- Handlers ---
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    msg = " ".join(context.args)
    if not msg:
        await update.message.reply_text("❌ Format: /broadcast <message>")
        return
    count = 0
    for user_id in all_users:
        try:
            await context.bot.send_message(chat_id=user_id, text=f"📢 **Admin Message:**\n\n{msg}")
            count += 1
        except: continue
    await update.message.reply_text(f"✅ Message {count} users ko bhej diya gaya hai.")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global current_process
    if current_process:
        current_process.kill()
        current_process = None
        await update.message.reply_text("🛑 Extraction cancel kar diya gaya hai.")
    else:
        await update.message.reply_text("⚠️ Koi active download nahi chal raha hai.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    all_users.add(user.id)
    if user.id in approved_users:
        await update.message.reply_text(f"👋 Hello {user.first_name}! Link bhejo.")
    else:
        pending_approvals[user.id] = update
        await update.message.reply_text("🔒 Aap approved nahi hain. Request bhej di gayi hai.")
        if ADMIN_ID:
            keyboard = [[InlineKeyboardButton("✅ Approve", callback_data=f"approve_{user.id}")]]
            await context.bot.send_message(ADMIN_ID, f"👤 User: {user.first_name}", reply_markup=InlineKeyboardMarkup(keyboard))

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if update.effective_user.id != ADMIN_ID: return
    data = query.data
    if data.startswith("approve_"):
        user_id = int(data.split("_")[1])
        approved_users.add(user_id)
        await query.edit_message_text(text=f"✅ User `{user_id}` approved!")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in approved_users: return
    text = update.message.text.strip()
    if "mega.nz" in text:
        extraction_queue.append((user_id, text, update))
        if not is_processing: await process_queue(context)
        else: await update.message.reply_text("⏳ Queue me add ho gaya hai.")
    else:
        await update.message.reply_text("⚠️ Valid link bhejo.")

async def process_queue(context: ContextTypes.DEFAULT_TYPE):
    global is_processing, current_process
    while extraction_queue:
        is_processing = True
        user_id, mega_link, update = extraction_queue.popleft()
        await update.message.reply_text("⏳ Extraction start ho raha hai... (/cancel dabayein rokne ke liye)")
        
        clean_url = format_mega_url(mega_link)
        task_id = str(uuid.uuid4())
        download_dir = f"./downloads/{task_id}"
        os.makedirs(download_dir, exist_ok=True)
        
        try:
            current_process = await asyncio.create_subprocess_exec(
                "megatools", "dl", "--path", download_dir, clean_url,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            await current_process.communicate()
            
            if current_process.returncode == 0:
                await update.message.reply_text("📂 Download complete! Uploading...")
                for root, _, files in os.walk(download_dir):
                    for file in files:
                        await update.message.reply_document(open(os.path.join(root, file), 'rb'))
            else:
                await update.message.reply_text("❌ Download failed.")
        finally:
            current_process = None
            shutil.rmtree(download_dir, ignore_errors=True)
            is_processing = False

def main():
    request = HTTPXRequest(connect_timeout=30.0, read_timeout=30.0, write_timeout=30.0)
    application = Application.builder().token(BOT_TOKEN).request(request).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CommandHandler("broadcast", broadcast))
    application.add_handler(CallbackQueryHandler(button_click))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("Bot is running perfectly...")
    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
    
