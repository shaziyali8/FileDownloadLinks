from telethon import TelegramClient, events
import aiohttp
import io
import re
import os
import time
from urllib.parse import urlparse, quote

# Fetch the token and API credentials from environment variables
API_ID = int(os.getenv('TELEGRAM_API_ID'))
API_HASH = os.getenv('TELEGRAM_API_HASH')
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

MAX_FILE_SIZE_MB = 50
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

# Dictionary to keep track of user upload sessions and channel IDs
upload_sessions = {}
channel_ids = {}

# Initialize the Telethon client
client = TelegramClient('bot_session', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

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

def encode_url(url: str) -> str:
    """Encode the URL to make it safe for requests."""
    return quote(url, safe='/:?=&')

async def fetch_file(session, url):
    """Fetch the file asynchronously and return its content and size."""
    async with session.get(url) as response:
        response.raise_for_status()
        content_type = response.headers.get('Content-Type', '')
        file_size = int(response.headers.get('Content-Length', 0))
        file_data = await response.read()
        return file_data, content_type, file_size

@client.on(events.NewMessage(pattern='/start'))
async def start(event):
    """Send a message when the command /start is issued."""
    await event.respond("Hello! The bot is running and ready to receive commands.")

@client.on(events.NewMessage(pattern='/upload'))
async def start_upload(event):
    """Start the upload session for the user."""
    chat_id = event.chat_id
    upload_sessions[chat_id] = []
    await event.respond("Upload session started. Send file links or a .txt file containing links to be uploaded. Use /stop to end the session.")
    await asyncio.sleep(5)

@client.on(events.NewMessage(pattern='/stop'))
async def stop_upload(event):
    """Stop the upload session for the user."""
    chat_id = event.chat_id
    if chat_id in upload_sessions:
        upload_sessions[chat_id] = []
        del upload_sessions[chat_id]
        await event.respond("Upload session ended.")
    else:
        await event.respond("No active upload session found.")

@client.on(events.NewMessage)
async def handle_message(event):
    """Handle messages containing file links or a .txt file during an active upload session."""
    chat_id = event.chat_id
    if chat_id in upload_sessions:
        if event.message.document:
            document = event.message.document
            if document.mime_type == 'text/plain':
                file = await client.download_media(document)
                with open(file, 'r', encoding='utf-8') as f:
                    try:
                        links = f.read().splitlines()
                        valid_links = [link.strip() for link in links if re.match(r'https?://', link.strip())]

                        if valid_links:
                            upload_sessions[chat_id].extend(valid_links)
                            await event.respond(f"Received {len(valid_links)} valid link(s) from the .txt file. Use /stop to end the session.")
                        else:
                            await event.respond("No valid URLs found in the .txt file.")
                    
                    except UnicodeDecodeError:
                        await event.respond("Failed to decode the .txt file. Please ensure it is in UTF-8 format.")

            else:
                await event.respond("Only .txt files are supported for upload.")
        else:
            media_links = event.message.text.split()
            valid_links = [link.strip() for link in media_links if re.match(r'https?://', link.strip())]
            if not valid_links:
                await event.respond("Please enter valid URL(s).")
                return

            upload_sessions[chat_id].extend(valid_links)
            await event.respond(f"Received {len(valid_links)} link(s). Use /stop to end the session.")
        
        if len(upload_sessions[chat_id]) > 0:
            await event.respond("Starting Uploading...")
            async with aiohttp.ClientSession() as session:
                for link in upload_sessions[chat_id]:
                    link = link.strip()
                    if link:
                        try:
                            # Ensure the URL is full and correctly encoded
                            encoded_link = encode_url(link)

                            # Fetch the file using the encoded URL
                            file_data, content_type, file_size = await fetch_file(session, encoded_link)

                            if file_size == 0:
                                await client.send_message(chat_id, f"File from {link} is empty and cannot be uploaded.")
                                continue

                            if file_size > MAX_FILE_SIZE_BYTES:
                                await client.send_message(chat_id, f"File from {link} is larger than 50 MB and cannot be uploaded.")
                                continue

                            file_extension = get_file_extension(link, content_type)
                            media_filename = sanitize_filename(link.split("/")[-1])

                            media_file = io.BytesIO(file_data)
                            media_file.seek(0)

                            # Send the media file to the chat or channel
                            target_chat_id = channel_ids.get(chat_id, chat_id)
                            
                            # Check file type and use the appropriate send method
                            if file_extension in ['.mp4', '.mov', '.webm']:
                                await client.send_file(target_chat_id, media_file, filename=media_filename, supports_streaming=True)
                            elif file_extension in ['.jpg', '.jpeg', '.png']:
                                await client.send_file(target_chat_id, media_file, filename=media_filename)
                            else:
                                await client.send_file(target_chat_id, media_file, filename=media_filename)

                            print(f"Successfully sent the file: {media_filename} to chat ID: {target_chat_id}")

                        except Exception as e:
                            await client.send_message(chat_id, f"Failed to upload {link}: {e}")
                            print(f"Error occurred: {e}")

            upload_sessions[chat_id] = []
            await client.send_message(chat_id, "All uploads completed!")

@client.on(events.NewMessage(pattern='/set_channel'))
async def set_channel(event):
    """Set or check the channel ID for uploading files."""
    chat_id = event.chat_id
    args = event.message.message.split()

    # Check if the command has no additional arguments
    if len(args) == 1:
        # No arguments provided; check if a channel is set
        if chat_id in channel_ids:
            channel_id = channel_ids[chat_id]
            try:
                # Fetch the channel name using the channel ID
                channel_info = await client.get_entity(channel_id)
                channel_name = channel_info.title
                await event.respond(f"The current channel is set to `{channel_name}` (ID: `{channel_id}`).")
            except Exception as e:
                await event.respond(f"Unable to fetch channel name: {e}")
        else:
            await event.respond("No channel is currently set. Please provide the channel ID or username after /set_channel.")
    elif len(args) == 2:
        # Command with one argument; set the channel ID
        channel_id = args[1]
        channel_ids[chat_id] = channel_id
        try:
            # Fetch the channel name using the channel ID
            channel_info = await client.get_entity(channel_id)
            channel_name = channel_info.title
            await event.respond(f"Channel set to `{channel_name}` (ID: `{channel_id}`). Share your file now.")
        except Exception as e:
            await event.respond(f"Unable to fetch channel name: {e}")
    else:
        await event.respond("Usage: /set_channel <channel_id>")


def main():
    client.run_until_disconnected()

if __name__ == '__main__':
    main()
