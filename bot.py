import os
import sys
import shutil
import asyncio
import logging
from pathlib import Path
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from dotenv import load_dotenv

# Configurações de logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.resolve()
load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Encontrar binários no PATH (funciona em Linux/Alpine/iSH, Windows e macOS)
EXIFTOOL_BIN = os.getenv("EXIFTOOL_PATH") or shutil.which("exiftool") or "exiftool"
FFMPEG_BIN = os.getenv("FFMPEG_PATH") or shutil.which("ffmpeg") or "ffmpeg"

TEMP_DIR = BASE_DIR / "temp"
TEMP_DIR.mkdir(exist_ok=True)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tiff", ".tif", ".bmp", ".heic", ".avif", ".gif"}
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".m4v", ".wmv"}

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "🧹 *Removedor de Metadados & IA (Rodando no iPhone)*\n\n"
        "Envie qualquer imagem ou vídeo (como foto ou documento).\n\n"
        "✨ *Remoção profunda:* C2PA, EXIF, GPS, Prompts de IA, tags de software.\n"
        "Preserva 100% da qualidade original sem recodificação!"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")

async def clean_image(input_path: Path, output_path: Path) -> tuple[bool, str]:
    shutil.copy2(input_path, output_path)
    purge_cmd = [
        EXIFTOOL_BIN,
        "-all=",
        "-all:all=",
        "-CommonURLS=",
        "-overwrite_original",
        str(output_path)
    ]
    proc = await asyncio.create_subprocess_exec(
        *purge_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0 and b"image files updated" not in stdout and b"image files unchanged" not in stdout:
        err_msg = stderr.decode(errors="ignore") or stdout.decode(errors="ignore")
        return False, f"Erro no ExifTool: {err_msg}"
    return True, "Sucesso"

async def clean_video(input_path: Path, output_path: Path) -> tuple[bool, str]:
    ffmpeg_cmd = [
        FFMPEG_BIN,
        "-y",
        "-i", str(input_path),
        "-map_metadata", "-1",
        "-map_metadata:s:v", "-1",
        "-map_metadata:s:a", "-1",
        "-c", "copy",
        str(output_path)
    ]
    proc = await asyncio.create_subprocess_exec(
        *ffmpeg_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        err_msg = stderr.decode(errors="ignore")
        return False, f"Erro no FFmpeg: {err_msg}"

    exif_cmd = [
        EXIFTOOL_BIN,
        "-all=",
        "-all:all=",
        "-overwrite_original",
        str(output_path)
    ]
    proc2 = await asyncio.create_subprocess_exec(
        *exif_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    await proc2.communicate()
    return True, "Sucesso"

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return

    file_id = None
    original_name = None
    is_video = False
    is_photo = False

    if msg.document:
        file_id = msg.document.file_id
        original_name = msg.document.file_name or "arquivo"
        ext = Path(original_name).suffix.lower()
        if ext in VIDEO_EXTENSIONS:
            is_video = True
        elif ext in IMAGE_EXTENSIONS:
            is_photo = True
        else:
            mime = msg.document.mime_type or ""
            if "video" in mime:
                is_video = True
                if not ext:
                    original_name += ".mp4"
            else:
                is_photo = True
                if not ext:
                    original_name += ".png"
    elif msg.video:
        file_id = msg.video.file_id
        original_name = msg.video.file_name or f"video_{msg.message_id}.mp4"
        is_video = True
    elif msg.photo:
        largest_photo = max(msg.photo, key=lambda p: p.file_size or 0)
        file_id = largest_photo.file_id
        original_name = f"photo_{msg.message_id}.jpg"
        is_photo = True

    if not file_id:
        return

    status_msg = await msg.reply_text("⏳ *Processando mídia no iPhone (removendo C2PA, EXIF, Prompts)...*", parse_mode="Markdown")
    await context.bot.send_chat_action(chat_id=msg.chat_id, action=ChatAction.UPLOAD_DOCUMENT)

    input_file = TEMP_DIR / f"in_{msg.message_id}_{original_name}"
    stem = Path(original_name).stem
    suffix = Path(original_name).suffix or (".mp4" if is_video else ".jpg")
    output_file = TEMP_DIR / f"{stem}_purified{suffix}"

    try:
        tg_file = await context.bot.get_file(file_id)
        await tg_file.download_to_drive(custom_path=str(input_file))

        if is_video:
            success, err = await clean_video(input_file, output_file)
        else:
            success, err = await clean_image(input_file, output_file)

        if not success or not output_file.exists():
            await status_msg.edit_text(f"❌ Falha: `{err}`", parse_mode="Markdown")
            return

        with open(output_file, "rb") as f:
            await msg.reply_document(
                document=f,
                filename=f"{stem}_clean{suffix}",
                caption="🛡️ *Arquivo purificado com sucesso!*\n• Metadados C2PA/AI removidos\n• Qualidade original preservada.",
                parse_mode="Markdown"
            )
        await status_msg.delete()

    except Exception as e:
        logger.exception("Erro ao processar")
        await status_msg.edit_text(f"❌ Erro: `{str(e)}`", parse_mode="Markdown")
    finally:
        if input_file.exists():
            try:
                os.remove(input_file)
            except Exception:
                pass
        if output_file.exists():
            try:
                os.remove(output_file)
            except Exception:
                pass

def main():
    if not BOT_TOKEN:
        print("[ERRO] Defina TELEGRAM_BOT_TOKEN")
        sys.exit(1)

    print("Bot rodando!")
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", start_command))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, handle_media))
    app.run_polling()

if __name__ == "__main__":
    main()
