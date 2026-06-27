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
# Broadcast ke liye users ki list
all_users = set() 
pending_approvals = {}
extraction_queue = collections.deque()
current_process = None # Chal rahi process ko track karne ke liye
is_processing = False

# --- Broadcast Command ---
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
    await update.message.reply_text(f"✅ Message sent to {count} users.")

# --- Cancel Command ---
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global current_process
    if current_process:
        current_process.kill()
        current_process = None
        await update.message.reply_text("🛑 Extraction cancel kar diya gaya hai.")
    else:
        await update.message.reply_text("⚠️ Koi active extraction nahi chal raha hai.")

# --- Start & Handler modified to add users to broadcast list ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    all_users.add(user.id) # User list me add
    if user.id in approved_users:
        await update.message.reply_text("👋 Hello! Mujhe MEGA link bhejo.")
    else:
        # ... (baaki approval code wahi rahega)
        pending_approvals[user.id] = update
        await update.message.reply_text("🔒 Request admin ko bhej di gayi hai.")
        if ADMIN_ID:
            keyboard = [[InlineKeyboardButton("✅ Approve", callback_data=f"approve_{user.id}")]]
            await context.bot.send_message(ADMIN_ID, f"👤 Request from {user.first_name}", reply_markup=InlineKeyboardMarkup(keyboard))

# --- Core Extraction modified for cancellation ---
async def extract_mega_folder(url: str, update: Update, context: ContextTypes.DEFAULT_TYPE):
    global current_process
    status_msg = await update.message.reply_text("🔍 Link setup ho raha hai... (Cancel ke liye: /cancel)")
    
    clean_url = format_mega_url(url)
    task_id = str(uuid.uuid4())
    download_dir = f"./downloads/{task_id}"
    os.makedirs(download_dir, exist_ok=True)
    
    try:
        current_process = await asyncio.create_subprocess_exec(
            "megatools", "dl", "--path", download_dir, clean_url,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await current_process.communicate()
        
        if current_process.returncode != 0:
            if "Killed" in str(stderr): return # Cancelled
            await status_msg.edit_text("❌ Download failed.")
            return

        # ... (Upload logic wahi rahega)
        
    finally:
        current_process = None
        shutil.rmtree(download_dir, ignore_errors=True)

# --- Main Boot ---
def main():
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CommandHandler("broadcast", broadcast))
    application.add_handler(CallbackQueryHandler(button_click))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.run_polling()
    
