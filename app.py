import os
import asyncio
import collections
import shutil
import uuid
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.request import HTTPXRequest
from mega import Mega

# --- Config ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID")) if os.getenv("ADMIN_ID") else None

approved_users = set([ADMIN_ID]) if ADMIN_ID else set()
all_users = set() 
extraction_queue = collections.deque()

bot_state = {
    "is_cancelled": False,
    "is_processing": False
}

# --- Helper Functions ---
def get_dir_size(path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            fp = os.path.join(root, f)
            if not os.path.islink(fp):
                total += os.path.getsize(fp)
    return total

async def split_video_ffmpeg(input_file, output_dir):
    base_name = os.path.basename(input_file)
    name, ext = os.path.splitext(base_name)
    output_pattern = os.path.join(output_dir, f"{name}_part%03d{ext}")
    
    cmd = [
        "ffmpeg", "-i", input_file,
        "-c", "copy", "-map", "0",
        "-segment_time", "90", 
        "-f", "segment",
        "-reset_timestamps", "1",
        output_pattern
    ]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    await proc.communicate()
    
    parts = sorted([os.path.join(output_dir, f) for f in os.listdir(output_dir) if f.startswith(name + "_part")])
    return parts

# --- Core Tracking & Upload Logic ---
async def track_progress(status_msg, directory, total_size, file_idx, total_files, file_name):
    start_time = time.time()
    try:
        while True:
            await asyncio.sleep(4)
            current_size = get_dir_size(directory)
            elapsed = time.time() - start_time
            speed = current_size / elapsed if elapsed > 0 else 0
            
            if speed > 0 and total_size > 0:
                eta_seconds = (total_size - current_size) / speed
                eta_str = f"{int(eta_seconds // 60)}m {int(eta_seconds % 60)}s"
            else:
                eta_str = "Calculating..."
                
            speed_mb = speed / (1024 * 1024)
            curr_mb = current_size / (1024 * 1024)
            tot_mb = total_size / (1024 * 1024) if total_size else 0
            
            text = f"🔄 **File {file_idx} of {total_files}**\n📄 `{file_name}`\n\n"
            text += f"⬇️ Downloaded: {curr_mb:.1f} MB / {tot_mb:.1f} MB\n"
            text += f"🚀 Speed: {speed_mb:.1f} MB/s\n"
            text += f"⏳ ETA: {eta_str}"
            
            try: await status_msg.edit_text(text)
            except: pass
    except asyncio.CancelledError:
        pass

async def upload_and_split(context, user_id, file_path, file_name, status_msg):
    file_size = os.path.getsize(file_path)
    limit = 49.5 * 1024 * 1024 
    ext = os.path.splitext(file_name)[1].lower()
    
    if file_size <= limit:
        if ext in ['.mp4', '.mkv', '.webm', '.avi']:
            await context.bot.send_video(chat_id=user_id, video=open(file_path, 'rb'), read_timeout=300, write_timeout=300)
        else:
            await context.bot.send_document(chat_id=user_id, document=open(file_path, 'rb'), read_timeout=300, write_timeout=300)
    else:
        await status_msg.edit_text(f"✂️ `{file_name}` badi hai. Split ho rahi hai...")
        
        if ext in ['.mp4', '.mkv']:
            split_dir = file_path + "_splits"
            os.makedirs(split_dir, exist_ok=True)
            parts = await split_video_ffmpeg(file_path, split_dir)
            
            for p_idx, part in enumerate(parts, 1):
                if bot_state["is_cancelled"]: break
                await status_msg.edit_text(f"📤 Uploading Part {p_idx} of {len(parts)} for `{file_name}`...")
                try: await context.bot.send_video(chat_id=user_id, video=open(part, 'rb'), read_timeout=300, write_timeout=300)
                except Exception: pass
            shutil.rmtree(split_dir, ignore_errors=True)
        else:
            with open(file_path, 'rb') as f:
                part_num = 1
                while True:
                    if bot_state["is_cancelled"]: break
                    chunk = f.read(int(limit))
                    if not chunk: break
                    
                    part_name = f"{file_path}.{part_num:03d}"
                    with open(part_name, 'wb') as p: p.write(chunk)
                    await status_msg.edit_text(f"📤 Uploading Chunk {part_num} for `{file_name}`...")
                    try: await context.bot.send_document(chat_id=user_id, document=open(part_name, 'rb'), read_timeout=300, write_timeout=300)
                    except Exception: pass
                    os.remove(part_name)
                    part_num += 1

# --- Main Processing Queue ---
def download_mega_node(m, node, is_folder, dest):
    if is_folder: m.download(node, dest_path=dest)
    else: m.download_url(node, dest_path=dest)

async def process_queue(context: ContextTypes.DEFAULT_TYPE):
    bot_state["is_processing"] = True
    
    while extraction_queue:
        bot_state["is_cancelled"] = False
        user_id, mega_link, update = extraction_queue.popleft()
        
        try:
            status_msg = await context.bot.send_message(chat_id=user_id, text="🔍 Folder scan ho raha hai. Please wait...")
            
            mega = Mega()
            m = await asyncio.to_thread(mega.login)
            is_folder = '/folder/' in mega_link or '#F!' in mega_link
            
            files_to_download = []
            if is_folder:
                nodes = await asyncio.to_thread(m.get_nodes_in_shared_folder, mega_link)
                for n_id, n_info in nodes.items():
                    if n_info['t'] == 0: 
                        files_to_download.append((n_id, n_info))
            else:
                files_to_download.append(mega_link)
                
            total_files = len(files_to_download)
            await status_msg.edit_text(f"✅ Folder mein {total_files} files mili. Ek-ek karke process start ho raha hai...")
            
            for i, file_node in enumerate(files_to_download, 1):
                if bot_state["is_cancelled"]:
                    await context.bot.send_message(chat_id=user_id, text="🛑 Task cancel kar diya gaya.")
                    break
                    
                file_name = file_node[1]['a']['n'] if is_folder else "Single File"
                file_size = file_node[1]['s'] if is_folder else 0
                
                task_id = str(uuid.uuid4())
                download_dir = f"./downloads/{task_id}"
                os.makedirs(download_dir, exist_ok=True)
                
                download_task = asyncio.to_thread(download_mega_node, m, file_node, is_folder, download_dir)
                tracker_task = asyncio.create_task(track_progress(status_msg, download_dir, file_size, i, total_files, file_name))
                
                await download_task
                tracker_task.cancel()
                
                downloaded_files = os.listdir(download_dir)
                if downloaded_files:
                    actual_file = downloaded_files[0]
                    actual_path = os.path.join(download_dir, actual_file)
                    
                    await status_msg.edit_text(f"📤 Downloading complete! Uploading {i} of {total_files}...\n📄 `{actual_file}`")
                    await upload_and_split(context, user_id, actual_path, actual_file, status_msg)
                else:
                    await context.bot.send_message(user_id, f"❌ Failed to download file {i}.")
                
                shutil.rmtree(download_dir, ignore_errors=True)
                
            if not bot_state["is_cancelled"]:
                await context.bot.send_message(chat_id=user_id, text="🎉 Sabhi files successfully send aur server se delete ho gayi!")
                
        except Exception as e:
            error_msg = str(e)
            if "Bandwidth" in error_msg or "Quota" in error_msg:
                error_msg = "MEGA account ki free bandwidth limit khatam ho gayi hai. Thodi der baad try karein."
            await context.bot.send_message(chat_id=user_id, text=f"💥 Error: {error_msg}")
            
    bot_state["is_processing"] = False 

# --- Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    all_users.add(user.id)
    if user.id in approved_users:
        await update.message.reply_text(f"👋 Hello {user.first_name}! MEGA link bhejo.")
    else:
        await update.message.reply_text("🔒 You have not been approved . DM owner @uflowx.")
        if ADMIN_ID:
            keyboard = [[InlineKeyboardButton("✅ Approve", callback_data=f"approve_{user.id}")]]
            await context.bot.send_message(ADMIN_ID, f"👤 User: {user.first_name}\nID: `{user.id}`", reply_markup=InlineKeyboardMarkup(keyboard))

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not bot_state["is_processing"]:
        await update.message.reply_text("⚠️ Koi active task nahi hai.")
        return
    bot_state["is_cancelled"] = True 
    await update.message.reply_text("🛑 Cancel command receive hui. Current file upload hone ke baad process stop ho jayega...")

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
            await update.message.reply_text(f"⏳ Aap queue me lag gaye hain. Aapki position: {len(extraction_queue)}. Pehle wala task khatam hote hi aapka start ho jayega.")
    else:
        await update.message.reply_text("⚠️ Valid MEGA link bhejo.")

def main():
    print("Bot starting up...")
    request = HTTPXRequest(connect_timeout=60.0, read_timeout=300.0, write_timeout=300.0)
    application = Application.builder().token(BOT_TOKEN).request(request).concurrent_updates(True).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CommandHandler("broadcast", broadcast))
    application.add_handler(CallbackQueryHandler(button_click))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
            
