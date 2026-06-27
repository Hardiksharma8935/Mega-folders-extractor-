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
extraction_queue = collections.deque()

bot_state = {
    "process": None,
    "is_cancelled": False,
    "is_processing": False
}

# --- Helper Functions ---
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
            await context.bot.send_message(chat_id=user_id, text=f"📢 **Admin:**\n\n{msg}")
            count += 1
        except: continue
    await update.message.reply_text(f"✅ Message {count} users ko bhej diya.")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not bot_state["is_processing"]:
        await update.message.reply_text("⚠️ Koi active task nahi hai.")
        return
    bot_state["is_cancelled"] = True 
    if bot_state["process"]:
        try: bot_state["process"].kill()
        except: pass
    await update.message.reply_text("🛑 Process stop kiya ja raha hai...")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    all_users.add(user.id)
    if user.id in approved_users:
        await update.message.reply_text(f"👋 Hello {user.first_name}! MEGA link bhejo.")
    else:
        await update.message.reply_text("🔒 Aap approved nahi hain dm owner @uflowx.")
        if ADMIN_ID:
            keyboard = [[InlineKeyboardButton("✅ Approve", callback_data=f"approve_{user.id}")]]
            await context.bot.send_message(ADMIN_ID, f"👤 User: {user.first_name}\nID: `{user.id}`", reply_markup=InlineKeyboardMarkup(keyboard))

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
        if not bot_state["is_processing"]: 
            asyncio.create_task(process_queue(context)) 
        else: 
            await update.message.reply_text(f"⏳ Queue position: {len(extraction_queue)}.")
    else:
        await update.message.reply_text("⚠️ Valid MEGA link bhejo.")

# --- Core Logic ---
async def process_queue(context: ContextTypes.DEFAULT_TYPE):
    bot_state["is_processing"] = True
    while extraction_queue:
        bot_state["is_cancelled"] = False
        user_id, mega_link, update = extraction_queue.popleft()
        try:
            status_msg = await context.bot.send_message(chat_id=user_id, text="⏳ Starting download...")
            clean_url = format_mega_url(mega_link)
            task_id = str(uuid.uuid4())
            download_dir = f"./downloads/{task_id}"
            os.makedirs(download_dir, exist_ok=True)
            
            # Using megatools (Make sure megatools is installed in your Dockerfile)
            proc = await asyncio.create_subprocess_exec(
                "megatools", "dl", "--path", download_dir, clean_url,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            bot_state["process"] = proc
            await proc.communicate()
            
            if bot_state["process"].returncode == 0:
                await status_msg.edit_text("📂 Download complete! Uploading...")
                for root, _, files in os.walk(download_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        # Split logic simplified
                        with open(file_path, 'rb') as f:
                            part_num = 1
                            while chunk := f.read(49 * 1024 * 1024):
                                part_name = f"{file}.{part_num:03d}"
                                with open(part_name, 'wb') as p: p.write(chunk)
                                await context.bot.send_document(chat_id=user_id, document=open(part_name, 'rb'), read_timeout=300, write_timeout=300)
                                os.remove(part_name); part_num += 1
            else:
                await status_msg.edit_text("❌ Download failed. Check link.")
        except Exception as e:
            await context.bot.send_message(chat_id=user_id, text=f"💥 Error: {str(e)}")
        finally:
            shutil.rmtree(download_dir, ignore_errors=True)
            bot_state["process"] = None
    bot_state["is_processing"] = False 

# --- Main Entry Point ---
def main():
    while True: # Auto-restart loop for 24x7 stability
        try:
            request = HTTPXRequest(connect_timeout=60.0, read_timeout=300.0, write_timeout=300.0)
            application = Application.builder().token(BOT_TOKEN).request(request).concurrent_updates(True).build()
            
            application.add_handler(CommandHandler("start", start))
            application.add_handler(CommandHandler("cancel", cancel))
            application.add_handler(CommandHandler("broadcast", broadcast))
            application.add_handler(CallbackQueryHandler(button_click))
            application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
            
            print("Bot starting...")
            application.run_polling(drop_pending_updates=True)
        except Exception as e:
            print(f"Crash detected: {e}. Restarting in 5s...")
            asyncio.sleep(5)

if __name__ == '__main__':
    main()
    
