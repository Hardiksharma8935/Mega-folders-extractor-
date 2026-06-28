import os
import asyncio
import collections
import subprocess
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- CONFIG ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID")) # Apni Telegram ID yahan dalna

approved_users = {ADMIN_ID}
extraction_queue = collections.deque()
bot_state = {"is_processing": False}

async def run_cmd(cmd):
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = await proc.communicate()
    return stdout.decode().strip(), stderr.decode().strip()

async def process_queue(context):
    bot_state["is_processing"] = True
    while extraction_queue:
        user_id, link = extraction_queue.popleft()
        msg = await context.bot.send_message(user_id, "🔍 Scanning MEGA link...")
        
        # MEGA Folder scan (recursive)
        stdout, stderr = await run_cmd(["megals", "-R", link])
        
        if not stdout:
            await msg.edit_text("❌ Error: No files found or Invalid Link!")
            continue

        files = [f for f in stdout.split('\n') if f.strip() and not f.endswith('/')]
        await msg.edit_text(f"✅ {len(files)} files found. Starting process...")
        
        for i, file_path in enumerate(files, 1):
            # Download Logic... (wahi purana logic)
            # ... (yahan baki download aur upload ka code rahega)
            pass
            
        await context.bot.send_message(user_id, "🎉 All tasks completed!")
    bot_state["is_processing"] = False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in approved_users:
        await update.message.reply_text("🔒 Aap approved nahi hain.")
        return
    await update.message.reply_text("👋 Hello! MEGA link bhejo.")

async def handle_message(update, context):
    user_id = update.effective_user.id
    if user_id not in approved_users: return
    
    link = update.message.text
    if "mega.nz" in link:
        extraction_queue.append((user_id, link))
        await update.message.reply_text("⏳ Added to queue.")
        if not bot_state["is_processing"]:
            asyncio.create_task(process_queue(context))
    else:
        await update.message.reply_text("⚠️ Please send a valid MEGA link.")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == '__main__':
    main()
    
