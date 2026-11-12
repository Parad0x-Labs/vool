"""Original character art shared by the Dawn Circuit skin and the Pet Yard."""
from collections import defaultdict

EXPRESSIONS = ('idle', 'blink', 'love', 'happy', 'surprise', 'spar', 'work', 'wave', 'nap', 'unknown')
MASCOTS = ('lumen-fox', 'dusk-lynx')


def mascot_pixels(kind, expression='idle', palette=None):
    """A closed 48-square drawing grammar. No imported characters or image assets."""
    if kind not in MASCOTS or expression not in EXPRESSIONS:
        raise ValueError('Unknown mascot or expression')
    p = palette or {}
    deep = kind == 'dusk-lynx'
    outline = '#242036'
    fur = p.get('border' if deep else 'accent', '#b8a7ed' if deep else '#ffd0ae')
    shade = p.get('accent' if deep else 'border', '#71628e' if deep else '#bf826f')
    face = '#fff5e8'
    pink = '#ed90b1'
    pixels = [['' for _ in range(48)] for _ in range(48)]

    def rect(x, y, w, h, color):
        for yy in range(max(0, y), min(48, y+h)):
            for xx in range(max(0, x), min(48, x+w)):
                pixels[yy][xx] = color

    # Tail, feet, tunic, collar, raised or resting paws.
    rect(34, 29, 9, 12, outline)
    rect(37, 27, 7, 9, fur)
    rect(40, 25, 5, 6, face)
    rect(13, 40, 8, 5, outline)
    rect(27, 40, 8, 5, outline)
    rect(13, 32, 22, 10, outline)
    rect(15, 32, 18, 8, shade)
    rect(21, 34, 6, 6, fur)
    rect(10, 29 if expression == 'wave' else 34, 5, 7, outline)
    rect(11, 27 if expression == 'wave' else 33, 3, 5, face)
    rect(33, 29 if expression == 'spar' else 34, 5, 7, outline)
    rect(34, 27 if expression == 'spar' else 33, 3, 5, face)
    # Tall ears and a broad, stepped silhouette.
    rect(10, 5, 9, 18, outline)
    rect(29, 5, 9, 18, outline)
    rect(12, 8, 5, 12, fur)
    rect(31, 8, 5, 12, fur)
    rect(14, 11, 2, 6, pink)
    rect(32, 11, 2, 6, pink)
    if deep:
        rect(9, 3, 3, 7, outline)
        rect(36, 3, 3, 7, outline)
    rect(10, 15, 28, 16, outline)
    rect(8, 19, 32, 9, outline)
    rect(12, 14, 24, 18, fur)
    rect(10, 20, 28, 8, fur)
    rect(12, 21, 24, 9, face)
    rect(15, 29, 18, 3, face)
    rect(12, 16, 7, 4, shade)
    rect(29, 16, 7, 4, shade)
    # Cheek marks and expressive eyes have deliberately different silhouettes.
    rect(11, 26, 4, 2, pink)
    rect(33, 26, 4, 2, pink)
    for x in (15, 27):
        if expression in ('blink', 'nap', 'happy', 'wave'):
            rect(x, 23, 6, 2, outline)
            if expression in ('happy', 'wave'):
                rect(x+1, 21, 4, 2, outline)
        elif expression == 'love':
            rect(x, 20, 2, 4, pink)
            rect(x+4, 20, 2, 4, pink)
            rect(x+1, 22, 4, 4, pink)
            rect(x+2, 26, 2, 1, pink)
        else:
            rect(x, 19 if expression == 'surprise' else 20, 6, 8 if expression == 'surprise' else 6, outline)
            rect(x+1, 20, 2, 3, '#ffffff')
            rect(x+3, 24, 2, 1, shade)
    if expression == 'surprise':
        rect(21, 27, 6, 5, outline)
        rect(23, 28, 2, 3, pink)
        rect(40, 10, 2, 7, pink)
        rect(40, 19, 2, 2, pink)
    elif expression in ('happy', 'love', 'wave'):
        rect(20, 27, 8, 4, outline)
        rect(22, 29, 4, 2, pink)
    elif expression == 'spar':
        rect(20, 28, 8, 2, outline)
        rect(14, 18, 7, 1, outline)
        rect(27, 18, 7, 1, outline)
    elif expression == 'unknown':
        rect(22, 28, 4, 1, outline)
        rect(40, 10, 4, 2, shade)
        rect(42, 12, 2, 3, shade)
        rect(40, 15, 3, 2, shade)
        rect(40, 19, 2, 2, shade)
    else:
        rect(22, 28, 4, 1, outline)
    rect(14, 32, 20, 3, outline)
    rect(22, 33, 4, 3, pink if not deep else '#87ddd2')
    if expression == 'work':
        rect(19, 36, 18, 6, outline)
        rect(21, 37, 14, 3, '#87ddd2')
    if expression == 'nap':
        rect(39, 9, 2, 2, shade)
        rect(42, 4, 3, 3, shade)
    return pixels


def mascot_svg(kind, expression='idle', palette=None):
    paths = defaultdict(list)
    pixels = mascot_pixels(kind, expression, palette)
    used = [[False]*48 for _ in range(48)]
    for y, row in enumerate(pixels):
        x = 0
        while x < 48:
            color = row[x]
            if not color or used[y][x]:
                x += 1
                continue
            end = x+1
            while end < 48 and row[end] == color and not used[y][end]:
                end += 1
            bottom = y+1
            while bottom < 48 and all(pixels[bottom][xx] == color and not used[bottom][xx] for xx in range(x, end)):
                bottom += 1
            for yy in range(y, bottom):
                for xx in range(x, end):
                    used[yy][xx] = True
            paths[color].append(f'M{x} {y}h{end-x}v{bottom-y}H{x}Z')
            x = end
    return ''.join(f"<path fill='{color}' d='{''.join(parts)}'/>" for color, parts in paths.items())


