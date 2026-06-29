import os
import asyncio
import collections
import shutil
import uuid
import subprocess
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.request import HTTPXRequest

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID")) if os.getenv("ADMIN_ID") else None

# In-memory approval list (Note: Railway restart hone par ye list reset ho jati hai)
approved_users = set([ADMIN_ID]) if ADMIN_ID else set()
extraction_queue = collections.deque()
bot_state = {"is_processing": False}

# --- 10 Minute Auto Delete Function ---
async def auto_delete_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, delay: int = 600):
    await asyncio.sleep(delay)
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass 

def format_mega_url(url: str) -> str:
    url = url.strip()
    if '/folder/' in url and '#' in url:
        parts = url.split('/folder/')[1].split('#')
        return f"https://mega.nz/#F!{parts[0]}!{parts[1]}"
    return url

async def run_cmd(cmd):
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = await proc.communicate()
    return stdout.decode().strip(), stderr.decode().strip()

# --- Approval Request Logic ---
async def request_approval(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    # Send message to User
    user_keyboard = [[InlineKeyboardButton("Contact Admin", url="https://t.me/uflowx")]]
    await update.message.reply_text(
        "🚫 You have not been approved by the Admin.\nPlease contact him for confirmation.", 
        reply_markup=InlineKeyboardMarkup(user_keyboard)
    )

    # Send Notification to Admin
    if ADMIN_ID:
        admin_keyboard = [
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"approve_{user_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"reject_{user_id}")
            ]
        ]
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"🔔 **New User Request!**\n\n👤 Name: {update.effective_user.first_name}\n🆔 ID: `{user_id}`\n\nApprove this user?",
            reply_markup=InlineKeyboardMarkup(admin_keyboard),
            parse_mode='Markdown'
        )

# --- Button Callback Handler (For Admin) ---
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    # Check if the person clicking is the Admin
    if update.effective_user.id != ADMIN_ID:
        await query.answer("You are not authorized to do this.", show_alert=True)
        return

    action, uid_str = data.split("_")
    uid = int(uid_str)

    if action == "approve":
        approved_users.add(uid)
        await query.edit_message_text(f"✅ User `{uid}` has been **Approved**!", parse_mode='Markdown')
        try:
            await context.bot.send_message(chat_id=uid, text="🎉 **Congratulations!** You have been approved by the Admin. You can now send MEGA links.")
        except Exception:
            pass # User might have blocked the bot

    elif action == "reject":
        await query.edit_message_text(f"❌ User `{uid}` has been **Rejected**.", parse_mode='Markdown')
        try:
            await context.bot.send_message(chat_id=uid, text="❌ Your request has been rejected by the Admin.")
        except Exception:
            pass

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id not in approved_users:
        await request_approval(update, context, user_id)
        return

    sent_msg = await update.message.reply_text(f"👋 Hello {update.effective_user.first_name}! Send MEGA link.")
    asyncio.create_task(auto_delete_message(context, user_id, sent_msg.message_id, 120))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id not in approved_users:
        await request_approval(update, context, user_id)
        return
    
    text = update.message.text.strip()
    if "mega.nz" in text:
        extraction_queue.append((user_id, text))
        
        if not bot_state["is_processing"]: 
            asyncio.create_task(process_queue(context)) 
        else: 
            msg = await update.message.reply_text(f"⏳ Task added to Queue. Your position: {len(extraction_queue)}.\nIt will start automatically.")
            asyncio.create_task(auto_delete_message(context, user_id, msg.message_id, 120))
    else:
        msg = await update.message.reply_text("⚠️ Valid MEGA link bhejo.")
        asyncio.create_task(auto_delete_message(context, user_id, msg.message_id, 60))

async def process_queue(context: ContextTypes.DEFAULT_TYPE):
    bot_state["is_processing"] = True
    
    while extraction_queue:
        user_id, mega_link = extraction_queue.popleft()
        
        try:
            status_msg = await context.bot.send_message(chat_id=user_id, text="📥 Downloading from MEGA... Please wait.")
            clean_url = format_mega_url(mega_link)
            
            task_id = str(uuid.uuid4())
            download_dir = f"./downloads/{task_id}"
            os.makedirs(download_dir, exist_ok=True)
            
            # --- DOWNLOAD PHASE ---
            await run_cmd(["megatools", "dl", "--path", download_dir, clean_url])
            
            all_files = []
            for root, _, files in os.walk(download_dir):
                for file in files:
                    all_files.append(os.path.join(root, file))
            
            if not all_files:
                await status_msg.edit_text("❌ No media found. Either link is empty, or the folder is too large (Server disk/MEGA Bandwidth Exceeded).")
                shutil.rmtree(download_dir, ignore_errors=True)
                continue
                
            await status_msg.edit_text(f"✅ {len(all_files)} files downloaded! Uploading to Telegram...")

            # --- UPLOAD PHASE ---
            for i, file_path in enumerate(all_files, 1):
                actual_file_name = os.path.basename(file_path)
                file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
                
                ext = actual_file_name.lower().split('.')[-1] if '.' in actual_file_name else ""
                is_video = ext in ['mp4', 'mkv', 'avi', 'mov', 'webm']
                
                await status_msg.edit_text(f"📤 Uploading ({i}/{len(all_files)}):\n`{actual_file_name}`")
                
                try:
                    if file_size_mb > 49.5:
                        if is_video:
                            split_dir = f"{file_path}_splits"
                            os.makedirs(split_dir, exist_ok=True)
                            await run_cmd(["ffmpeg", "-i", file_path, "-c", "copy", "-map", "0", "-segment_time", "600", "-f", "segment", "-reset_timestamps", "1", f"{split_dir}/part%03d_{actual_file_name}"])
                            
                            for part_file in sorted(os.listdir(split_dir)):
                                p_path = os.path.join(split_dir, part_file)
                                sent_msg = await context.bot.send_video(chat_id=user_id, video=open(p_path, 'rb'), read_timeout=300, write_timeout=300)
                                asyncio.create_task(auto_delete_message(context, user_id, sent_msg.message_id, 600))
                                os.remove(p_path)
                        else:
                            with open(file_path, 'rb') as f:
                                part_num = 1
                                while True:
                                    chunk = f.read(49 * 1024 * 1024)
                                    if not chunk: break
                                    part_name = f"{file_path}.part{part_num}"
                                    with open(part_name, 'wb') as p: p.write(chunk)
                                    
                                    sent_msg = await context.bot.send_document(chat_id=user_id, document=open(part_name, 'rb'), read_timeout=300, write_timeout=300)
                                    asyncio.create_task(auto_delete_message(context, user_id, sent_msg.message_id, 600))
                                    os.remove(part_name)
                                    part_num += 1
                    else:
                        if is_video:
                            sent_msg = await context.bot.send_video(chat_id=user_id, video=open(file_path, 'rb'), read_timeout=300, write_timeout=300)
                        else:
                            sent_msg = await context.bot.send_document(chat_id=user_id, document=open(file_path, 'rb'), read_timeout=300, write_timeout=300)
                        
                        asyncio.create_task(auto_delete_message(context, user_id, sent_msg.message_id, 600))

                except Exception as e:
                    print(f"Error uploading {actual_file_name}: {e}")
                
                os.remove(file_path)

            shutil.rmtree(download_dir, ignore_errors=True)
            
            final_msg = await context.bot.send_message(chat_id=user_id, text="✅ **All media processed!**\n\n⚠️ **Please save all media, it will be deleted after 10 minutes.**")
            asyncio.create_task(auto_delete_message(context, user_id, final_msg.message_id, 600))
            await status_msg.delete()
                
        except Exception as e:
            await context.bot.send_message(chat_id=user_id, text="❌ An error occurred during extraction.")
            
    bot_state["is_processing"] = False 

def main():
    request = HTTPXRequest(connect_timeout=60.0, read_timeout=300.0, write_timeout=300.0)
    application = Application.builder().token(BOT_TOKEN).request(request).concurrent_updates(True).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(button_callback)) # Handle Admin Buttons
    
    print("Bot is running with Admin Approvals & Auto-Delete...")
    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
                            
