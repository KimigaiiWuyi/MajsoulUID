from pathlib import Path

from PIL import Image

TEXT_PATH = Path(__file__).parent / 'texture2d'


def get_footer():
    return Image.open(TEXT_PATH / 'footer.png')


def get_bg():
    return Image.open(TEXT_PATH / 'bg.jpg')


def add_footer(img: Image.Image, w: int = 0) -> Image.Image:
    footer = get_footer()
    w = img.size[0] if not w else w
    if w != footer.size[0]:
        footer = footer.resize(
            (w, int(footer.size[1] * w / footer.size[0])),
        )
    x, y = (
        int((img.size[0] - footer.size[0]) / 2),
        img.size[1] - footer.size[1] - 10,
    )
    img.paste(footer, (x, y), footer)
    return img
