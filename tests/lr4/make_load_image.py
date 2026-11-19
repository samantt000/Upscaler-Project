"""Тестовое изображение для замеров NFR-001/NFR-003: худший допустимый случай 2000×2000 px.

    python tests/lr4/make_load_image.py results/load_2000.jpg
"""
import sys

from PIL import Image, ImageFilter

path = sys.argv[1] if len(sys.argv) > 1 else "load_2000.jpg"
img = Image.effect_noise((2000, 2000), 48).convert("RGB").filter(ImageFilter.GaussianBlur(1))
img.save(path, "JPEG", quality=90)
print(path)
