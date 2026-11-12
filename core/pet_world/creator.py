"""Workspace-contained declarative world authoring with a geometry-map preview."""
from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path

from core.pet_world import worlds
from core.pet_world.catalog import BLOCKS, SIZE
from core.pet_world.model import integer, keys, loads, require, starter, validate, validate_column
from core.pet_world.standard import TILE, VIEWPORT


def build(source):
    keys(source, ('name', 'description', 'base', 'fills', 'decorations', 'pets'))
    validate_column(source['base'])
    require(type(source['fills']) is list and len(source['fills']) <= 128)
    yard = starter()
    yard.update(name=source['name'], columns=[deepcopy(source['base']) for _ in range(SIZE*SIZE)],
                decorations=source['decorations'], pets=source['pets'])
    for fill in source['fills']:
        keys(fill, ('x', 'y', 'width', 'depth', 'stack'))
        x, y, width, depth = (fill[k] for k in ('x', 'y', 'width', 'depth'))
        require(integer(x, 0, SIZE-1) and integer(y, 0, SIZE-1)
                and integer(width, 1, SIZE-x) and integer(depth, 1, SIZE-y))
        validate_column(fill['stack'])
        for row in range(y, y+depth):
            for col in range(x, x+width):
                yard['columns'][row*SIZE+col] = deepcopy(fill['stack'])
    validate(worlds.canonical(yard))
    return worlds.export(worlds.canonical(yard), source['description'])


def preview(raw):
    """A layout map, using the same world units; full character art is in the Yard."""
    from io import BytesIO

    from PIL import Image, ImageDraw

    world = worlds.decode(raw)
    image = Image.new('RGB', VIEWPORT, '#253746')
    draw = ImageDraw.Draw(image)
    w, h, z = TILE
    def point(x, y, level):
        return (VIEWPORT[0]//2+(x-y)*w//2, 160+(x+y)*h//2-level*z)
    for x, y in sorted(((x, y) for y in range(SIZE) for x in range(SIZE)), key=lambda p: sum(p)):
        level = 0
        for kind, count in world['columns'][y*SIZE+x]:
            colors = BLOCKS[kind]['colors']
            for _ in range(count):
                level += 1
                px, py = point(x, y, level)
                draw.polygon([(px-w//2, py), (px, py+h//2), (px, py+h//2+z), (px-w//2, py+z)], fill=colors[1])
                draw.polygon([(px, py+h//2), (px+w//2, py), (px+w//2, py+z), (px, py+h//2+z)], fill=colors[2])
                draw.polygon([(px, py-h//2), (px+w//2, py), (px, py+h//2), (px-w//2, py)], fill=colors[0])
    for item in world['decorations']:
        from core.pet_world.model import footprint
        for x, y in footprint(item):
            px, py = point(x, y, sum(r[1] for r in world['columns'][y*SIZE+x]))
            draw.polygon([(px, py-8), (px+16, py), (px, py+8), (px-16, py)], outline='#f2d5a2')
    for pet in world['pets']:
        px, py = point(pet['x'], pet['y'], sum(r[1] for r in world['columns'][pet['y']*SIZE+pet['x']]))
        draw.ellipse((px-4, py-10, px+4, py-2), fill='#f0b5ce', outline='#342747')
    stream = BytesIO()
    image.save(stream, 'PNG')
    return stream.getvalue()


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
    sheet = output.with_suffix('.png')
    require(output.suffix == '.voolworld' and not output.exists() and not sheet.exists(), 'exists', 409)
    raw = build(loads(source.read_text('utf-8')))
    art = preview(raw)
    with sheet.open('xb') as target:
        target.write(art)
    try:
        with output.open('x', encoding='utf-8') as target:
            target.write(raw)
    except BaseException:
        sheet.unlink()
        raise
    print(worlds.canonical({'world': str(args.output), 'preview': str(args.output.with_suffix('.png'))}))


if __name__ == '__main__':
    main()
