"""Workspace-contained, data-only pixel pet authoring and pack export."""
from __future__ import annotations

import argparse
import base64
import json
import re
from io import BytesIO
from pathlib import Path

from core import pet_cosmetics as c
from core.pet_world.model import integer, keys, loads, require


def build(source):
    """Rectangles are pixels, never executable drawing instructions."""
    from PIL import Image, ImageDraw

    keys(source, ('id', 'name', 'author', 'license', 'description', 'palette', 'base', 'states', 'bindings'))
    palette = source['palette']
    require(type(palette) is dict and 1 <= len(palette) <= 32)
    require(all(type(k) is str and c.IDENTIFIER.fullmatch(k) and type(v) is str
                and re.fullmatch('#[a-fA-F0-9]{8}', v) for k, v in palette.items()))
    require(type(source['states']) is dict and set(c.STATES) <= set(source['states']) <= set(c.EXPRESSIONS))

    def draw(rects, target):
        require(type(rects) is list and len(rects) <= 128)
        d = ImageDraw.Draw(target)
        for rect in rects:
            require(type(rect) is list and len(rect) == 5)
            x, y, w, h, color = rect
            require(integer(x, 0, 47) and integer(y, 0, 47) and integer(w, 1, 48-x)
                    and integer(h, 1, 48-y) and type(color) is str and color in palette)
            d.rectangle((x, y, x+w-1, y+h-1), fill=palette[color])

    frames, images = {}, []
    for state, overlays in source['states'].items():
        require(type(overlays) is list and 1 <= len(overlays) <= 8)
        indices = []
        for overlay in overlays:
            image = Image.new('RGBA', (48, 48))
            draw(source['base'], image)
            draw(overlay, image)
            indices.append(len(images))
            images.append(image)
        frames[state] = {direction: indices[:] for direction in c.DIRECTIONS}
    width = min(8, len(images))
    atlas = Image.new('RGBA', (48*width, 48*((len(images)+width-1)//width)))
    for index, image in enumerate(images):
        atlas.paste(image, ((index % width)*48, (index//width)*48))
    output = BytesIO()
    atlas.save(output, 'PNG')
    pack = {key: source[key] for key in ('id', 'name', 'author', 'license', 'description', 'bindings')}
    pack.update(schema='vool.pet.v1', version=1, frames=frames, clips={},
                atlas={'cell': 48, 'png': base64.b64encode(output.getvalue()).decode()})
    raw = json.dumps(pack, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    c.validate_pack(raw)
    return raw, output.getvalue()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.workspace.resolve(strict=True)
    require(root.is_dir())
    source = (root/args.source).resolve(strict=True)
    output = (root/args.output).resolve()
    require(source.is_relative_to(root) and output.is_relative_to(root) and source.is_file())
    require(source.stat().st_size <= 256*1024, 'size')
    preview = output.with_suffix('.png')
    require(output != preview and not output.exists() and not preview.exists(), 'exists', 409)
    raw, art = build(loads(source.read_text('utf-8')))
    # Exclusive creation preserves existing user work, including races with another writer.
    with preview.open('xb') as target:
        target.write(art)
    try:
        with output.open('x', encoding='utf-8') as target:
            target.write(raw)
    except BaseException:
        preview.unlink()
        raise
    print(json.dumps({'pack': str(args.output), 'preview': str(args.output.with_suffix('.png'))}))


if __name__ == '__main__':
    main()
