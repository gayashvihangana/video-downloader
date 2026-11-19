"""Draw the app icon (red rounded square with a white download arrow).

Run: python build/make_icon.py  ->  assets/icon.ico
"""

import os

from PIL import Image, ImageDraw

SIZE = 512
RED = (229, 72, 77, 255)
WHITE = (255, 255, 255, 255)

image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
draw = ImageDraw.Draw(image)

draw.rounded_rectangle((16, 16, SIZE - 16, SIZE - 16), radius=112, fill=RED)

# Arrow: shaft + head
draw.rounded_rectangle((226, 110, 286, 300), radius=26, fill=WHITE)
draw.polygon([(146, 250), (366, 250), (256, 362)], fill=WHITE)

# Tray line under the arrow
draw.rounded_rectangle((136, 384, 376, 418), radius=17, fill=WHITE)

project = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
target = os.path.join(project, "assets", "icon.ico")
os.makedirs(os.path.dirname(target), exist_ok=True)

image.save(
    target,
    sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
)
print("saved", target)
