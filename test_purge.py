import subprocess
from PIL import Image, PngImagePlugin
from pathlib import Path

# Cria imagem com metadados fake de IA e EXIF
img = Image.new('RGB', (100, 100), color='red')
meta = PngImagePlugin.PngInfo()
meta.add_text("prompt", "masterpiece, ultra realistic 8k, cyberpunk girl")
meta.add_text("Software", "Stable Diffusion / ComfyUI / C2PA Manifest")
meta.add_text("parameters", "Steps: 30, Sampler: Euler a, Seed: 133742")

test_in = Path("test_ai.png")
test_out = Path("test_clean.png")
img.save(test_in, pnginfo=meta)

print("Imagem de teste criada com metadados de IA!")

# Roda exiftool purge
exif_bin = r"C:\Users\gusta\AppData\Local\Programs\ExifTool\exiftool.exe"
subprocess.run([exif_bin, "-all=", "-all:all=", "-overwrite_original", str(test_in)], check=True)

# Checa se restou algum metadado
result = subprocess.run([exif_bin, "-j", str(test_in)], capture_output=True, text=True)
print("\nMetadados restantes após o expurgo:")
print(result.stdout)
