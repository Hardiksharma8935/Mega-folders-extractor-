import os
import asyncio
import collections
import shutil
import uuid
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.request import HTTPXRequest

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID")) if os.getenv("ADMIN_ID") else None

approved_users = set([ADMIN_ID]) if ADMIN_ID else set()
all_users = set() 
extraction_queue = collections.deque()

# --- State Management ---
bot_state = {
    "process": None,
    "is_cancelled": False,
    "is_processing": False
}

def format_mega_url(url: str) -> str:
    url = url.strip()
    if '/folder/' in url and '#' in url:
        parts = url.split('/folder/')[1].split('#')
        return f"https://mega.nz/#F!{parts[0]}!{parts[1]}"
    return url

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
    if not bot_state["is_processing"]:
        await update.message.reply_text("⚠️ Koi active task nahi chal raha hai.")
        return
        
    bot_state["is_cancelled"] = True 
    if bot_state["process"]:
        try:
            bot_state["process"].kill() 
        except:
            pass
    await update.message.reply_text("🛑 Process ko rokne ki request bhej di gayi hai...")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    all_users.add(user.id)
    if user.id in approved_users:
        await update.message.reply_text(f"👋 Hello {user.first_name}! send megafolder link.")
    else:
        await update.message.reply_text("🔒 Aap approved nahi hain. Request bhej di gayi hai dm for joining @uflowx.")
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
            await update.message.reply_text(f"⏳ Abhi ek extraction chal raha hai. Aapko queue me daal diya hai (Position: {len(extraction_queue)}).")
    else:
        await update.message.reply_text("⚠️ Valid MEGA link bhejo.")

async def process_queue(context: ContextTypes.DEFAULT_TYPE):
    bot_state["is_processing"] = True
    
    while extraction_queue:
        bot_state["is_cancelled"] = False
        user_id, mega_link, update = extraction_queue.popleft()
        
        try:
            status_msg = await context.bot.send_message(chat_id=user_id, text="⏳ Extraction start ho raha hai... (/cancel dabayein rokne ke liye)")
            
            clean_url = format_mega_url(mega_link)
            task_id = str(uuid.uuid4())
            download_dir = f"./downloads/{task_id}"
            os.makedirs(download_dir, exist_ok=True)
            
            # --- PHASE 1: DOWNLOAD ---
            bot_state["process"] = await asyncio.create_subprocess_exec(
                "megatools", "dl", "--path", download_dir, clean_url,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            await bot_state["process"].communicate()
            
            if bot_state["is_cancelled"]:
                await status_msg.edit_text("🛑 Extraction Cancel kar diya gaya.")
                continue
                
            if bot_state["process"].returncode == 0:
                await status_msg.edit_text("📂 Download complete! Uploading to Telegram...")
                
                # --- PHASE 2: UPLOAD (With Crash Protection) ---
                for root, _, files in os.walk(download_dir):
                    for file in files:
                        if bot_state["is_cancelled"]: break
                            
                        file_path = os.path.join(root, file)
                        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
                        
                        # 50MB limit check (Ye bot ko 413 error se bachayega)
                        if file_size_mb > 49.5: 
                            await context.bot.send_message(chat_id=user_id, text=f"⚠️ Skipped `{file}`\n❌ Telegram bots 50MB se badi file nahi bhej sakte (Ye {file_size_mb:.1f}MB ki hai).")
                            continue
                            
                        await status_msg.edit_text(f"📤 Uploading: `{file}`")
                        
                        # Upload try-except block (Ye bot ko freeze hone se bachayega)
                        try:
                            with open(file_path, 'rb') as doc:
                                await context.bot.send_document(chat_id=user_id, document=doc, read_timeout=120, write_timeout=120)
                        except Exception as e:
                            await context.bot.send_message(chat_id=user_id, text=f"❌ `{file}` bhejte waqt error aaya: {str(e)}")
                        
                        # Server ko saans lene ka time do taaki doosre users ignore na hon
                        await asyncio.sleep(1) 
                        
                if bot_state["is_cancelled"]:
                    await context.bot.send_message(chat_id=user_id, text="🛑 Upload process cancel kar diya gaya.")
                else:
                    await context.bot.send_message(chat_id=user_id, text="✅ Sabhi files successfully bhej di gayi hain!")
            else:
                await status_msg.edit_text("❌ Download failed. Link check karein.")
                
        except Exception as global_err:
            await context.bot.send_message(chat_id=user_id, text=f"💥 System Error: {str(global_err)}")
            
        finally:
            # Ye block hamesha chalega chahe kuch bhi error ho (Preventing Freeze)
            bot_state["process"] = None
            shutil.rmtree(download_dir, ignore_errors=True)
            await asyncio.sleep(1)
            
    # Jab queue khali ho jaye tabhi bot free hoga
    bot_state["is_processing"] = False 

def main():
    # Timeout limits aur badha diye hain heavy files ke liye
    request = HTTPXRequest(connect_timeout=60.0, read_timeout=180.0, write_timeout=180.0)
    application = Application.builder().token(BOT_TOKEN).request(request).concurrent_updates(True).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CommandHandler("broadcast", broadcast))
    application.add_handler(CallbackQueryHandler(button_click))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("Bot is running with Crash Protection v3...")
    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
    
