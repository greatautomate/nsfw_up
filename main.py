import asyncio
import os
import warnings
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import PeerIdInvalid, UsernameNotOccupied, ChatAdminRequired
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import logging
import tempfile
import shutil

# === COMPLETE ERROR SUPPRESSION ===
logging.getLogger('pyrogram').setLevel(logging.CRITICAL)
logging.getLogger('asyncio').setLevel(logging.CRITICAL)
logging.getLogger('apscheduler').setLevel(logging.WARNING)
warnings.filterwarnings('ignore', category=RuntimeWarning, module='asyncio')

# Configure clean main logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Monkey patch to suppress pyrogram errors
original_handle_updates = Client.handle_updates
async def patched_handle_updates(self, updates):
    try:
        await original_handle_updates(self, updates)
    except (ValueError, KeyError):
        pass
    except Exception as e:
        if "Peer id invalid" not in str(e) and "ID not found" not in str(e):
            logger.error(f"Unexpected error: {e}")
Client.handle_updates = patched_handle_updates

class TelegramDualAccountBotWithDownload:
    def __init__(self):
        self.api_id = os.getenv('API_ID')
        self.api_hash = os.getenv('API_HASH')

        # Session strings
        self.string_session_account1 = os.getenv('ACCOUNT1_SESSION')
        self.string_session_account2 = os.getenv('ACCOUNT2_SESSION')

        # Bot and channel settings
        self.bot_username = os.getenv('BOT_USERNAME')
        self.upload_channel = os.getenv('UPLOAD_CHANNEL')  # Changed from FORWARD_CHANNEL

        # Messages
        self.message_account1 = os.getenv('MESSAGE_ACCOUNT1', 'Hello from Account 1! 🚀')
        self.message_account2 = os.getenv('MESSAGE_ACCOUNT2', 'Hello from Account 2! 🎯')

        # Options
        self.add_counter = os.getenv('ADD_COUNTER', 'false').lower() == 'true'
        self.add_timestamp = os.getenv('ADD_TIMESTAMP', 'false').lower() == 'true'
        self.execution_mode = os.getenv('EXECUTION_MODE', 'concurrent').lower()
        self.send_on_start = os.getenv('SEND_ON_START', 'false').lower() == 'true'

        # Download & Upload settings
        self.auto_download_enabled = os.getenv('AUTO_DOWNLOAD', 'true').lower() == 'true'
        self.download_videos_only = os.getenv('DOWNLOAD_VIDEOS_ONLY', 'true').lower() == 'true'
        self.add_caption = os.getenv('ADD_CAPTION', 'true').lower() == 'true'
        self.custom_caption = os.getenv('CUSTOM_CAPTION', '🎥 Downloaded from TeraBox Bot')

        # Validate required variables
        if not all([self.api_id, self.api_hash, self.bot_username]):
            raise ValueError("Missing required: API_ID, API_HASH, BOT_USERNAME")

        if not all([self.string_session_account1, self.string_session_account2]):
            raise ValueError("Both ACCOUNT1_SESSION and ACCOUNT2_SESSION are required")

        if self.auto_download_enabled and not self.upload_channel:
            raise ValueError("UPLOAD_CHANNEL is required when AUTO_DOWNLOAD is enabled")

        # Initialize clients
        self.client_account1 = Client(
            "account1_session",
            api_id=self.api_id,
            api_hash=self.api_hash,
            session_string=self.string_session_account1
        )

        self.client_account2 = Client(
            "account2_session",
            api_id=self.api_id,
            api_hash=self.api_hash,
            session_string=self.string_session_account2
        )

        self.scheduler = AsyncIOScheduler()

        # Channel access status
        self.channel_access_account1 = False
        self.channel_access_account2 = False

        # Create temp directory for downloads
        self.temp_dir = tempfile.mkdtemp()

        # Setup message handlers for auto-downloading
        if self.auto_download_enabled:
            self.setup_message_handlers()

        # Log configuration
        logger.info("🎭 Telegram Dual Account Bot with Download & Upload Starting...")
        logger.info(f"⚙️ Mode: {self.execution_mode.upper()} | Target: {self.bot_username}")
        logger.info(f"🚀 Send on Start: {'YES' if self.send_on_start else 'NO'}")
        logger.info(f"📥 Auto Download: {'YES' if self.auto_download_enabled else 'NO'}")
        if self.auto_download_enabled:
            logger.info(f"📤 Upload Channel: {self.upload_channel}")
            logger.info(f"🎥 Videos Only: {'YES' if self.download_videos_only else 'NO'}")
            logger.info(f"📝 Add Caption: {'YES' if self.add_caption else 'NO'}")

    async def ensure_channel_access(self, client: Client, account_name: str):
        """Ensure the client can access the upload channel"""
        try:
            # Try to get channel info
            chat = await client.get_chat(self.upload_channel)
            logger.info(f"✅ {account_name}: Channel access confirmed - {chat.title}")
            return True

        except PeerIdInvalid:
            logger.warning(f"⚠️ {account_name}: Channel not accessible, trying to join...")
            try:
                # Try to join the channel if it's public
                await client.join_chat(self.upload_channel)
                logger.info(f"✅ {account_name}: Successfully joined {self.upload_channel}")
                return True
            except Exception as join_error:
                logger.error(f"❌ {account_name}: Could not join channel - {join_error}")
                return False

        except Exception as e:
            logger.error(f"❌ {account_name}: Channel access error - {e}")
            return False

    def setup_message_handlers(self):
        """Setup message handlers for both accounts"""

        # Handler for Account 1
        @self.client_account1.on_message(
            filters.chat(self.bot_username) & 
            (filters.video | filters.document | filters.photo | filters.text)
        )
        async def handle_bot_response_account1(client: Client, message: Message):
            await self.handle_bot_response(client, message, "Account 1")

        # Handler for Account 2
        @self.client_account2.on_message(
            filters.chat(self.bot_username) & 
            (filters.video | filters.document | filters.photo | filters.text)
        )
        async def handle_bot_response_account2(client: Client, message: Message):
            await self.handle_bot_response(client, message, "Account 2")

    async def download_and_upload_media(self, client: Client, message: Message, account_name: str):
        """Download media from bot and upload to channel"""
        downloaded_file = None
        try:
            # Ensure channel access
            if account_name == "Account 1" and not self.channel_access_account1:
                self.channel_access_account1 = await self.ensure_channel_access(client, account_name)
            elif account_name == "Account 2" and not self.channel_access_account2:
                self.channel_access_account2 = await self.ensure_channel_access(client, account_name)

            # Check if we have access
            has_access = (self.channel_access_account1 if account_name == "Account 1" 
                         else self.channel_access_account2)

            if not has_access:
                logger.warning(f"⚠️ {account_name}: No channel access, skipping download")
                return

            # Determine media type and download
            if message.video:
                logger.info(f"📥 {account_name}: Downloading video...")
                downloaded_file = await message.download(file_name=f"{self.temp_dir}/")
                await self.upload_video(client, downloaded_file, message, account_name)

            elif message.document:
                # Check if it's a video document
                if message.document.mime_type and message.document.mime_type.startswith('video/'):
                    logger.info(f"📥 {account_name}: Downloading video document...")
                    downloaded_file = await message.download(file_name=f"{self.temp_dir}/")
                    await self.upload_video(client, downloaded_file, message, account_name)
                elif not self.download_videos_only:
                    logger.info(f"📥 {account_name}: Downloading document...")
                    downloaded_file = await message.download(file_name=f"{self.temp_dir}/")
                    await self.upload_document(client, downloaded_file, message, account_name)

            elif message.photo and not self.download_videos_only:
                logger.info(f"📥 {account_name}: Downloading photo...")
                downloaded_file = await message.download(file_name=f"{self.temp_dir}/")
                await self.upload_photo(client, downloaded_file, message, account_name)

        except Exception as e:
            logger.error(f"❌ {account_name}: Download/Upload error - {e}")
        finally:
            # Clean up downloaded file
            if downloaded_file and os.path.exists(downloaded_file):
                try:
                    os.remove(downloaded_file)
                    logger.info(f"🗑️ {account_name}: Cleaned up temporary file")
                except:
                    pass

    async def upload_video(self, client: Client, file_path: str, original_message: Message, account_name: str):
        """Upload video to channel"""
        try:
            caption = self.generate_caption(original_message) if self.add_caption else None

            await client.send_video(
                chat_id=self.upload_channel,
                video=file_path,
                caption=caption,
                supports_streaming=True
            )

            logger.info(f"📤 {account_name}: Video uploaded successfully to {self.upload_channel}")

        except Exception as e:
            logger.error(f"❌ {account_name}: Video upload failed - {e}")

    async def upload_document(self, client: Client, file_path: str, original_message: Message, account_name: str):
        """Upload document to channel"""
        try:
            caption = self.generate_caption(original_message) if self.add_caption else None

            await client.send_document(
                chat_id=self.upload_channel,
                document=file_path,
                caption=caption
            )

            logger.info(f"📤 {account_name}: Document uploaded successfully to {self.upload_channel}")

        except Exception as e:
            logger.error(f"❌ {account_name}: Document upload failed - {e}")

    async def upload_photo(self, client: Client, file_path: str, original_message: Message, account_name: str):
        """Upload photo to channel"""
        try:
            caption = self.generate_caption(original_message) if self.add_caption else None

            await client.send_photo(
                chat_id=self.upload_channel,
                photo=file_path,
                caption=caption
            )

            logger.info(f"📤 {account_name}: Photo uploaded successfully to {self.upload_channel}")

        except Exception as e:
            logger.error(f"❌ {account_name}: Photo upload failed - {e}")

    def generate_caption(self, original_message: Message):
        """Generate caption for uploaded media"""
        caption = self.custom_caption

        # Add original caption if exists
        if original_message.caption:
            caption += f"\n\n📝 Original: {original_message.caption}"

        # Add timestamp
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        caption += f"\n⏰ Downloaded: {timestamp}"

        return caption

    async def handle_bot_response(self, client: Client, message: Message, account_name: str):
        """Handle responses from the bot"""
        try:
            # Check if message should be downloaded
            should_download = False
            message_type = "text"

            if message.video:
                should_download = True
                message_type = "video"
            elif message.document:
                if message.document.mime_type and message.document.mime_type.startswith('video/'):
                    should_download = True
                    message_type = "video document"
                elif not self.download_videos_only:
                    should_download = True
                    message_type = "document"
            elif message.photo and not self.download_videos_only:
                should_download = True
                message_type = "photo"

            if should_download:
                logger.info(f"🎯 {account_name}: Detected {message_type} from bot")
                await self.download_and_upload_media(client, message, account_name)
            else:
                # For text messages or non-media, just log
                if message.text:
                    logger.info(f"💬 {account_name}: Bot sent text: {message.text[:50]}...")
                else:
                    logger.info(f"⏭️ {account_name}: Skipped {message_type} (not matching criteria)")

        except Exception as e:
            logger.error(f"❌ {account_name}: Response handling error - {e}")

    def format_message(self, base_message, message_number, account_name):
        """Format message based on settings"""
        message = base_message

        if self.add_counter:
            message += f" - Message {message_number}/3"

        if self.add_timestamp:
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            message += f" at {timestamp}"

        return message

    async def send_messages_account1(self):
        """Send 3 messages from Account 1"""
        try:
            logger.info("🚀 Account 1: Sending messages...")

            for i in range(3):
                message_text = self.format_message(self.message_account1, i+1, "Account 1")

                await self.client_account1.send_message(
                    chat_id=self.bot_username,
                    text=message_text
                )

                if i < 2:
                    await asyncio.sleep(10)

            logger.info("✅ Account 1: All messages sent successfully")

        except Exception as e:
            logger.error(f"❌ Account 1 Error: {e}")

    async def send_messages_account2(self):
        """Send 3 messages from Account 2"""
        try:
            logger.info("🎯 Account 2: Sending messages...")

            for i in range(3):
                message_text = self.format_message(self.message_account2, i+1, "Account 2")

                await self.client_account2.send_message(
                    chat_id=self.bot_username,
                    text=message_text
                )

                if i < 2:
                    await asyncio.sleep(10)

            logger.info("✅ Account 2: All messages sent successfully")

        except Exception as e:
            logger.error(f"❌ Account 2 Error: {e}")

    async def execute_concurrent_mode(self):
        """Both accounts send messages simultaneously"""
        logger.info("🔄 Executing CONCURRENT mode...")

        task1 = asyncio.create_task(self.send_messages_account1())
        task2 = asyncio.create_task(self.send_messages_account2())

        await asyncio.gather(task1, task2, return_exceptions=True)

    async def execute_sequential_mode(self):
        """Accounts send messages one after another"""
        logger.info("⏭️ Executing SEQUENTIAL mode...")

        await self.send_messages_account1()
        logger.info("⏱️ Waiting 30 seconds...")
        await asyncio.sleep(30)
        await self.send_messages_account2()

    async def daily_message_routine(self):
        """Main routine to send messages"""
        logger.info("🎬 Starting daily message routine...")

        if self.execution_mode == 'sequential':
            await self.execute_sequential_mode()
        else:
            await self.execute_concurrent_mode()

        logger.info("✨ Daily routine completed successfully")
        if self.auto_download_enabled:
            logger.info("📥 Auto-download is active - waiting for bot responses...")

    async def start(self):
        """Start clients and scheduler"""
        try:
            # Start clients
            await self.client_account1.start()
            await self.client_account2.start()
            logger.info("🔗 Both accounts connected successfully")

            # Test channel access
            if self.auto_download_enabled:
                logger.info("🔍 Testing channel access...")
                self.channel_access_account1 = await self.ensure_channel_access(self.client_account1, "Account 1")
                self.channel_access_account2 = await self.ensure_channel_access(self.client_account2, "Account 2")

            # Schedule daily execution
            self.scheduler.add_job(
                self.daily_message_routine,
                'cron',
                hour=9,
                minute=0,
                timezone='UTC'
            )

            # Execute immediately if enabled
            if self.send_on_start:
                logger.info("🧪 Testing mode activated")
                await self.daily_message_routine()

            self.scheduler.start()
            logger.info("📅 Scheduler started - Daily messages at 9:00 AM UTC")
            logger.info("🤖 Bot is running smoothly...")

            # Keep running to handle incoming messages
            while True:
                await asyncio.sleep(3600)

        except KeyboardInterrupt:
            logger.info("🛑 Bot stopped by user")
        except Exception as e:
            logger.error(f"💥 Critical error: {e}")
        finally:
            # Cleanup
            try:
                await self.client_account1.stop()
                await self.client_account2.stop()
                # Clean up temp directory
                if os.path.exists(self.temp_dir):
                    shutil.rmtree(self.temp_dir)
                logger.info("🛑 All clients stopped and cleaned up")
            except:
                pass

async def main():
    bot = TelegramDualAccountBotWithDownload()
    await bot.start()

if __name__ == "__main__":
    asyncio.run(main())
