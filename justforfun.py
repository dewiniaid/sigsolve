"""Quick and dirty script to build a image of all of the tiles"""
import PIL.Image
import pathlib
import random

dest = PIL.Image.new('RGB', (640, 480), None)

samples = list(pathlib.Path("data").glob("*/tile*.png"))

x = 0
y = 0
while y < dest.height:
    file = random.choice(samples)
    print(f"({x}, {y}) {file}")
    src = PIL.Image.open(file).convert('RGB')
    dest.paste(src, (x, y))
    x += src.width
    if x > dest.width:
        x = 0
        y += src.height
dest.save('samples.png')




