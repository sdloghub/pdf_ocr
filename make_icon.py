from pathlib import Path
from PIL import Image, ImageDraw

assets = Path(__file__).parent / 'assets'
assets.mkdir(exist_ok=True)
scale = 2
im = Image.new('RGBA', (1024 * scale, 1024 * scale), (0, 0, 0, 0))
draw = ImageDraw.Draw(im)
def box(coords): return tuple(int(v * scale) for v in coords)
def rounded(coords, radius, fill): draw.rounded_rectangle(box(coords), radius=radius*scale, fill=fill)
rounded((48, 48, 976, 976), 208, '#102C54')
rounded((252, 162, 778, 847), 48, '#0B2346')
rounded((224, 144, 750, 818), 48, '#F3F8FF')
draw.polygon([tuple(box((x,y))) for x,y in [(604,144),(750,290),(604,290)]], fill='#C5DDF7')
rounded((284, 350, 666, 368), 9, '#7A9ABA')
rounded((284, 408, 612, 426), 9, '#7A9ABA')
rounded((284, 466, 650, 484), 9, '#7A9ABA')
rounded((284, 524, 580, 542), 9, '#7A9ABA')
# Cyan scan brackets communicate OCR.
for points in [[(182,306),(182,256),(232,256)],[(704,256),(784,256),(784,306)],[(182,548),(182,598),(232,598)],[(784,548),(784,598),(734,598)]]:
    draw.line([tuple(box(p)) for p in points], fill='#43DFEB', width=14*scale, joint='curve')
draw.line(box((184, 444, 784, 444)), fill='#43DFEB', width=7*scale)
# Magnifier: searchable PDF.
draw.ellipse(box((526, 563, 850, 887)), fill='#102C54')
draw.ellipse(box((550, 587, 826, 863)), fill='#38D9CC')
draw.ellipse(box((584, 621, 792, 829)), fill='#123D66')
draw.line(box((793, 830, 895, 932)), fill='#38D9CC', width=54*scale)
# Simple text mark in the lens.
rounded((630, 673, 746, 687), 7, '#E3FCFF')
rounded((630, 717, 725, 731), 7, '#E3FCFF')
rounded((630, 761, 746, 775), 7, '#E3FCFF')
im = im.resize((1024, 1024), Image.Resampling.LANCZOS)
im.save(assets/'app-icon.png')
im.save(assets/'app-icon.icns', format='ICNS')
