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

# Carregar variáveis do .env
BASE_DIR = Path(__file__).parent.resolve()
load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Detectar executáveis instalados
EXIFTOOL_PATH = os.getenv("EXIFTOOL_PATH")
FFMPEG_PATH = os.getenv("FFMPEG_PATH")

def find_binary(name: str, fallback_paths: list[str]) -> str:
    # Checa PATH do sistema
    found = shutil.which(name)
    if found:
        return found
    # Checa fallbacks comuns
    for p in fallback_paths:
        expanded = os.path.expandvars(p)
        if os.path.exists(expanded):
            return expanded
    return name

EXIFTOOL_BIN = EXIFTOOL_PATH or find_binary(
    "exiftool",
    [
        r"C:\Users\%USERNAME%\AppData\Local\Programs\ExifTool\exiftool.exe",
        r"C:\Program Files\ExifTool\exiftool.exe",
    ]
)

FFMPEG_BIN = FFMPEG_PATH or find_binary(
    "ffmpeg",
    [
        r"C:\Users\%USERNAME%\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin\ffmpeg.exe",
        r"C:\ffmpeg\bin\ffmpeg.exe",
    ]
)

TEMP_DIR = BASE_DIR / "temp"
TEMP_DIR.mkdir(exist_ok=True)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tiff", ".tif", ".bmp", ".heic", ".avif", ".gif"}
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".m4v", ".wmv"}

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "🧹 *Removedor Universal de Metadados & Proveniência de IA*\n\n"
        "Envie qualquer *foto, imagem ou vídeo* (preferencialmente como **Arquivo/Documento** para preservar 100% da resolução nativa).\n\n"
        "✨ *O que eu destruo sem perder 1 pixel ou bitrate:*\n"
        "• Assinaturas C2PA e Content Credentials (identificadores de IA)\n"
        "• Chunks de prompts (Midjourney, Stable Diffusion, ComfyUI, DALL-E)\n"
        "• EXIF completo (GPS, data/hora, marca da câmera, lente, modelo)\n"
        "• Blocos XMP, IPTC, ICC e dados ocultos de software de edição\n"
        "• Tags de renderizadores e containers de vídeo (Sora, Runway, Kling, etc.)\n\n"
        "Apenas mande o arquivo e devolverei ele 100% puro!"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")

async def clean_image(input_path: Path, output_path: Path) -> tuple[bool, str]:
    """
    Remove absolutamente todos os metadados usando ExifTool.
    -all= remove EXIF, IPTC, XMP, ICC, maker notes e chunks de texto de IA.
    Sobrescreve mantendo os dados da imagem (bitstreams) intocados.
    """
    cmd = [
        EXIFTOOL_BIN,
        "-all=",                 # Remove todas as tags conhecidas
        "-tagsfromfile", "@",    # Prepara cópia
        "-ColorSpaceTags",       # Mantém o espaço de cor caso queira evitar dessaturação, mas opcional
        "-overwrite_original",
        "-api", "largefilesupport=1",
        str(output_path)
    ]
    # Primeiro copia para o output
    shutil.copy2(input_path, output_path)

    # Executa ExifTool de forma purificadora profunda
    # -all= remove EXIF, IPTC, XMP, C2PA, PNG chunks (tEXt, zTXt, iTXt com prompts)
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
    """
    Remove metadados de vídeo com FFmpeg em modo Stream Copy (Zero recodificação, 100% qualidade idêntica),
    seguido por ExifTool para varrer átomos residuais de contêiner.
    """
    # 1. FFmpeg stream copy sem metadata e sem data tracks
    ffmpeg_cmd = [
        FFMPEG_BIN,
        "-y",
        "-i", str(input_path),
        "-map_metadata", "-1",       # Remove global metadata
        "-map_metadata:s:v", "-1",   # Remove video stream metadata
        "-map_metadata:s:a", "-1",   # Remove audio stream metadata
        "-c", "copy",                # Stream copy: NENHUMA PERDA DE QUALIDADE
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

    # 2. Passo secundário: ExifTool no vídeo resultante para erradicar marcas de IA restantes
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

    # Determinar arquivo
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
            # Tentar pelo mime_type
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
        # Pega a foto de maior resolução
        largest_photo = max(msg.photo, key=lambda p: p.file_size or 0)
        file_id = largest_photo.file_id
        original_name = f"photo_{msg.message_id}.jpg"
        is_photo = True

    if not file_id:
        return

    # Mensagem de processamento
    status_msg = await msg.reply_text("⏳ *Baixando e expurgando metadados (C2PA, EXIF, Prompts, GPS)...*", parse_mode="Markdown")
    await context.bot.send_chat_action(chat_id=msg.chat_id, action=ChatAction.UPLOAD_DOCUMENT)

    input_file = TEMP_DIR / f"in_{msg.message_id}_{original_name}"
    stem = Path(original_name).stem
    suffix = Path(original_name).suffix or (".mp4" if is_video else ".jpg")
    output_file = TEMP_DIR / f"{stem}_purified{suffix}"

    try:
        # 1. Download
        tg_file = await context.bot.get_file(file_id)
        await tg_file.download_to_drive(custom_path=str(input_file))

        # 2. Limpeza
        if is_video:
            success, err = await clean_video(input_file, output_file)
        else:
            success, err = await clean_image(input_file, output_file)

        if not success or not output_file.exists():
            await status_msg.edit_text(f"❌ Falha ao processar arquivo:\n`{err}`", parse_mode="Markdown")
            return

        # 3. Enviar de volta como DOCUMENTO (para não comprimir nem reintroduzir nada do Telegram)
        await status_msg.edit_text("📤 *Enviando arquivo limpo e sem compressão...*", parse_mode="Markdown")

        with open(output_file, "rb") as f:
            await msg.reply_document(
                document=f,
                filename=f"{stem}_clean{suffix}",
                caption="🛡️ *Arquivo purificado com sucesso!*\n• Metadados C2PA/AI removidos\n• Prompts e tags eliminados\n• 100% qualidade idêntica preservada.",
                parse_mode="Markdown"
            )

        await status_msg.delete()

    except Exception as e:
        logger.exception("Erro ao processar mídia")
        await status_msg.edit_text(f"❌ Ocorreu um erro: `{str(e)}`", parse_mode="Markdown")

    finally:
        # Limpar arquivos temporários
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
        print("[ERRO] Variável TELEGRAM_BOT_TOKEN não encontrada no .env!")
        print("Crie o arquivo .env contendo: TELEGRAM_BOT_TOKEN=seu_token_aqui")
        sys.exit(1)

    print("Iniciando Removedor de Metadados Telegram Bot...")
    print(f"ExifTool: {EXIFTOOL_BIN}")
    print(f"FFmpeg: {FFMPEG_BIN}")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", start_command))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, handle_media))

    print("Bot online e aguardando arquivos!")
    app.run_polling()

if __name__ == "__main__":
    main()
