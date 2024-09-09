
import telegram
from telegram import Update, InputFile
from telegram.ext import ApplicationBuilder, MessageHandler, filters, CommandHandler, ContextTypes
import aiohttp
import io
import re
import os
from urllib.parse import urlparse
import asyncio
from moviepy.editor import VideoFileClip
from PIL import Image

TOKEN = '7381557233:AAGOsHX_BIoranuVWO_HEYIII98LVyTiBuc'  # Replace with your actual Telegram bot token

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
    return os.path.splitext(urlparse(url).path)[1]

async def fetch_file(session, url):
    """Fetch the file asynchronously and return its content, size, and content type."""
    async with session.get(url) as response:
        response.raise_for_status()
        content_type = response.headers.get('Content-Type', '')
        file_size = int(response.headers.get('Content-Length', 0))
        file_data = await response.read()
        return file_data, content_type, file_size

def convert_to_mp4(input_file: io.BytesIO, input_format: str) -> io.BytesIO:
    """Convert media files to mp4 using moviepy."""
    output_file = io.BytesIO()
    input_file.seek(0)

    if input_format in ['.mov', '.gif', '.webp']:
        with VideoFileClip(input_file, format=input_format) as clip:
            clip.write_videofile(output_file, codec='libx264')
    else:
        output_file = input_file  # No conversion needed

    output_file.seek(0)
    return output_file

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
        return  # Return if update does not contain a message

    chat_id = update.message.chat_id
    if chat_id in upload_sessions:
        # Check if the message contains a document (e.g., a .txt file)
        if update.message.document:
            document = update.message.document
            if document.mime_type == 'text/plain':
                # Download the .txt file
                file = await context.bot.get_file(document.file_id)
                file_content = await file.download_as_bytearray()

                # Decode and split links
                links = file_content.decode('utf-8').splitlines()
                upload_sessions[chat_id].extend(links)
                await update.message.reply_text("Links from the .txt file received. Use /stop to end the session.")
            else:
                await update.message.reply_text("Only .txt files are supported for upload.")
        else:
            # Process as text message containing URLs
            media_links = update.message.text.split()

            # Validate that the message contains URLs
            if not any(re.match(r'https?://', link) for link in media_links):
                await update.message.reply_text("Please enter URL(s).")
                await asyncio.sleep(5)
                await update.message.delete()
                return

            upload_sessions[chat_id].extend(media_links)
            await update.message.reply_text("Links received. Use /stop to end the session.")
        
        await asyncio.sleep(5)
        await update.message.delete()
        
        # Process the collected links
        if len(upload_sessions[chat_id]) > 0:
            # Send the starting message and delete it after 5 seconds
            starting_message = await update.message.reply_text("Starting Uploading...")
            await asyncio.sleep(5)
            await starting_message.delete()

            async with aiohttp.ClientSession() as session:
                for link in upload_sessions[chat_id]:
                    link = link.strip()
                    if link:
                        try:
                            file_data, content_type, file_size = await fetch_file(session, link)

                            if file_size > MAX_FILE_SIZE_BYTES:
                                await context.bot.send_message(chat_id=chat_id, text=f"File from {link} is larger than 50 MB and cannot be uploaded.")
                                continue

                            file_extension = get_file_extension(link, content_type)
                            media_filename = sanitize_filename(link.split("/")[-1]) + file_extension
                            media_file = io.BytesIO(file_data)

                            # Convert videos and animated formats to mp4
                            if file_extension in ['.mov', '.gif', '.webp']:
                                media_file = convert_to_mp4(media_file, file_extension)
                                media_filename = media_filename.rsplit('.', 1)[0] + '.mp4'  # Change extension to mp4

                            # Prepare the media file for uploading
                            media_input_file = InputFile(media_file, filename=media_filename)

                            # Send the media file to the chat or channel
                            target_chat_id = channel_ids.get(chat_id, chat_id)  # Use channel ID if set, else use user's chat ID
                            await context.bot.send_document(chat_id=target_chat_id, document=media_input_file)

                        except aiohttp.ClientError as e:
                            await context.bot.send_message(chat_id=chat_id, text=f"Failed to upload {link}: {e}")

            # Clear the upload session after processing
            upload_sessions[chat_id] = []

            # Send a completion message and delete it after 20 seconds
            completion_message = await context.bot.send_message(chat_id=chat_id, text="All uploads completed!")
            await asyncio.sleep(20)
            await completion_message.delete()

async def set_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set the channel ID for uploading files."""
    chat_id = update.message.chat_id
    if len(context.args) == 1:
        channel_id = context.args[0]
        channel_ids[chat_id] = channel_id
        await update.message.reply_text(f"Channel set to {channel_id}. Share your file now.")
    elif len(context.args) == 0:
        # No arguments provided
        await update.message.reply_text("Please provide the channel ID or username after /set_channel.")
    else:
        await update.message.reply_text("Usage: /set_channel <channel_id>")

# Set up the bot and command handlers
if __name__ == '__main__':
    application = ApplicationBuilder().token(TOKEN).build()

    # Command to start the upload session
    application.add_handler(CommandHandler('upload', start_upload))

    # Command to end the upload session
    application.add_handler(CommandHandler('stop', stop_upload))

    # Command to set the channel ID
    application.add_handler(CommandHandler('set_channel', set_channel))

    # Handler for messages containing file links or a .txt file
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND | filters.Document.MimeType("text/plain"), handle_message))

    application.run_polling()


