import telegram
from telegram import Update, InputFile
from telegram.ext import ApplicationBuilder, MessageHandler, filters, CommandHandler, ContextTypes
import aiohttp
import io
import re
import os
from urllib.parse import urlparse
import asyncio
import ffmpeg
import tempfile

# Fetch the token from environment variables
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')  # Use environment variable for the token

MAX_FILE_SIZE_MB = 50
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

# Dictionary to keep track of user upload sessions and channel IDs
upload_sessions = {}
channel_ids = {}

def sanitize_filename(filename: str) -> str:
    """Sanitize the filename to remove invalid characters."""
    return re.sub(r'[<>:"/\\|?*]', '', filename)

def get_file_extension(url: str, content_type: str) -> str:
    """Get the file extension based on the content type of the response."""
    if 'image/jpeg' in content_type:
        return '.jpg'
    elif 'image/png' in content_type:
        return '.png'
    elif 'video/mp4' in content_type:
        return '.mp4'
    elif 'video/quicktime' in content_type:
        return '.mov'
    elif 'image/gif' in content_type:
        return '.gif'
    elif 'image/webp' in content_type:
        return '.webp'
    elif 'video/webm' in content_type:
        return '.webm'
    return os.path.splitext(urlparse(url).path)[1]

async def fetch_file(session, url):
    """Fetch the file asynchronously and return its content and size."""
    async with session.get(url) as response:
        response.raise_for_status()
        content_type = response.headers.get('Content-Type', '')
        file_size = int(response.headers.get('Content-Length', 0))
        file_data = await response.read()
        return file_data, content_type, file_size

def convert_to_mp4(input_data, input_format):
    """Convert video files to .mp4 format using ffmpeg-python."""
    input_buffer = io.BytesIO(input_data)
    input_buffer.seek(0)

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_temp_file = os.path.join(temp_dir, "input_temp." + input_format)
            output_temp_file = os.path.join(temp_dir, "output_temp.mp4")

            with open(input_temp_file, 'wb') as f:
                f.write(input_data)

            (
                ffmpeg
                .input(input_temp_file)
                .output(output_temp_file, vcodec='libx264', acodec='aac', strict='experimental')
                .run(overwrite_output=True)
            )

            with open(output_temp_file, 'rb') as f:
                output_data = f.read()

            return output_data

    except ffmpeg.Error as e:
        print(f"FFmpeg error: {e.stderr.decode()}")
        return None
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /start is issued."""
    await update.message.reply_text("Hello! The bot is running and ready to receive commands.")

async def start_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the upload session for the user."""
    chat_id = update.message.chat_id
    upload_sessions[chat_id] = []
    await update.message.reply_text("Upload session started. Send file links or a .txt file containing links to be uploaded. Use /stop to end the session.")
    await asyncio.sleep(5)
    await update.message.delete()

async def stop_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop the upload session for the user."""
    chat_id = update.message.chat_id
    if chat_id in upload_sessions:
        upload_sessions[chat_id] = []
        del upload_sessions[chat_id]
        await update.message.reply_text("Upload session ended.")
    else:
        await update.message.reply_text("No active upload session found.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle messages containing file links or a .txt file during an active upload session."""
    if update.message is None:
        return

    chat_id = update.message.chat_id
    if chat_id in upload_sessions:
        if update.message.document:
            document = update.message.document
            if document.mime_type == 'text/plain':
                file = await context.bot.get_file(document.file_id)
                file_content = await file.download_as_bytearray()

                try:
                    links = file_content.decode('utf-8').splitlines()
                    valid_links = [link.strip() for link in links if re.match(r'https?://', link.strip())]

                    if valid_links:
                        upload_sessions[chat_id].extend(valid_links)
                        await update.message.reply_text(f"Received {len(valid_links)} valid link(s) from the .txt file. Use /stop to end the session.")
                    else:
                        await update.message.reply_text("No valid URLs found in the .txt file.")
                
                except UnicodeDecodeError:
                    await update.message.reply_text("Failed to decode the .txt file. Please ensure it is in UTF-8 format.")

            else:
                await update.message.reply_text("Only .txt files are supported for upload.")
        else:
            media_links = update.message.text.split()
            valid_links = [link.strip() for link in media_links if re.match(r'https?://', link.strip())]
            if not valid_links:
                await update.message.reply_text("Please enter valid URL(s).")
                await asyncio.sleep(5)
                await update.message.delete()
                return

            upload_sessions[chat_id].extend(valid_links)
            await update.message.reply_text(f"Received {len(valid_links)} link(s). Use /stop to end the session.")
        
        await asyncio.sleep(5)
        await update.message.delete()
        
        if len(upload_sessions[chat_id]) > 0:
            starting_message = await update.message.reply_text("Starting Uploading...")
            await asyncio.sleep(5)
            await starting_message.delete()

            async with aiohttp.ClientSession() as session:
                for link in upload_sessions[chat_id]:
                    link = link.strip()
                    if link:
                        try:
                            file_data, content_type, file_size = await fetch_file(session, link)

                            if file_size == 0:
                                await context.bot.send_message(chat_id=chat_id, text=f"File from {link} is empty and cannot be uploaded.")
                                continue

                            if file_size > MAX_FILE_SIZE_BYTES:
                                await context.bot.send_message(chat_id=chat_id, text=f"File from {link} is larger than 50 MB and cannot be uploaded.")
                                continue

                            file_extension = get_file_extension(link, content_type)
                            media_filename = sanitize_filename(link.split("/")[-1])

                            if file_extension in ['.mov', '.gif', '.webp', '.webm']:
                                file_data = convert_to_mp4(file_data, file_extension.lstrip('.'))
                                if file_data is None:
                                    await context.bot.send_message(chat_id=chat_id, text=f"Conversion failed for {link}.")
                                    continue
                                media_filename += '.mp4'
                            else:
                                media_filename += file_extension

                            if not file_data or len(file_data) == 0:
                                await context.bot.send_message(chat_id=chat_id, text=f"File from {link} is empty after processing and cannot be uploaded.")
                                continue

                            media_file = InputFile(io.BytesIO(file_data), filename=media_filename)
                            target_chat_id = channel_ids.get(chat_id, chat_id)
                            await context.bot.send_document(chat_id=target_chat_id, document=media_file)
                            print(f"Successfully sent the file: {media_filename} to chat ID: {target_chat_id}")

                        except Exception as e:
                            await context.bot.send_message(chat_id=chat_id, text=f"Failed to upload {link}: {e}")
                            print(f"Error occurred: {e}")

            upload_sessions[chat_id] = []

            completion_message = await context.bot.send_message(chat_id=chat_id, text="All uploads completed!")
            await asyncio.sleep(20)
            await completion_message.delete()

async def set_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set or check the channel ID for uploading files."""
    chat_id = update.message.chat_id
    if len(context.args) == 0:
        if chat_id in channel_ids:
            await update.message.reply_text(f"The current channel is set to `{channel_ids[chat_id]}`. Please share the txt or links to upload the media files.")
        else:
            await update.message.reply_text("No channel is currently set. Please provide the channel ID or username after /set_channel.")
    elif len(context.args) == 1:
        channel_id = context.args[0]
        channel_ids[chat_id] = channel_id
        await update.message.reply_text(f"Channel set to `{channel_id}`. Share your file now.")
    else:
        await update.message.reply_text("Usage: /set_channel <channel_id>")

if __name__ == '__main__':
    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('upload', start_upload))
    application.add_handler(CommandHandler('stop', stop_upload))
    application.add_handler(CommandHandler('set_channel', set_channel))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND | filters.Document.MimeType("text/plain"), handle_message))

    application.run_polling()
