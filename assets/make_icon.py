"""Regenerate the simple application icon with Pillow (development only)."""
from pathlib import Path
from PIL import Image, ImageDraw

image = Image.new('RGBA', (256, 256), (0, 0, 0, 0))
draw = ImageDraw.Draw(image)
draw.rounded_rectangle((8, 8, 248, 248), radius=52, fill='#122337')
draw.ellipse((48, 46, 208, 208), outline='#557589', width=13)
draw.arc((48, 46, 208, 208), 25, 135, fill='#53dec6', width=15)
draw.ellipse((139, 139, 177, 177), fill='#aaffdd')
draw.line((89, 117, 111, 97, 148, 117, 165, 98), fill='white', width=13)
draw.line((126, 108, 109, 141, 82, 143), fill='white', width=13)
draw.line((111, 135, 138, 154, 126, 181), fill='white', width=13)
draw.ellipse((127, 63, 151, 87), fill='white')
image.save(Path(__file__).parent / 'app.ico', sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
