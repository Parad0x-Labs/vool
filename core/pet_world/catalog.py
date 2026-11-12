"""Closed, free building vocabulary and the explicit Phase 1 decisions."""
from __future__ import annotations

from core.companion_art_fragment import COMPANION_ROSTER
from core.pet_world.standard import MAX_HEIGHT, SIZE, contract

MAX_DECORATIONS = 96
MAX_ROSTER = 4
MAX_BYTES = 256 * 1024
VIEW = 'isometric-pixels'
ENTRY_POINTS = ('sidebar', 'toybox')
ALL_COSMETICS_FREE = True
REAL_USE_GROWTH = True
DIG_MODE = 'finished-cosmetics'
MONEY_ENABLED = False
FRIENDS = 'signed-files-phase-2'
COLLECT_AGES = False
CHAT_MODE = 'preset-reactions'
ONLINE_PLAZA = False
ROSTER = (*COMPANION_ROSTER, 'lumen-fox', 'dusk-lynx')

_COLORS = {
    'grass': ('#95bd78', '#67875c', '#415c44'), 'soil': ('#ad8868', '#886348', '#614937'),
    'stone': ('#aab5bd', '#798995', '#52616c'), 'wood': ('#c39b70', '#9a714c', '#694c38'),
    'sand': ('#ead69b', '#c5ac71', '#927952'), 'water': ('#87c9d7', '#5896ad', '#3b718e'),
    'glass': ('#c1e1e0', '#86b6bd', '#5e8e9b'), 'brick': ('#cf9889', '#a76d65', '#784d4d'),
    'snow': ('#eff3ed', '#c0d3d1', '#92adaf'), 'moss': ('#acc58d', '#7c986c', '#536f50'),
    'slate': ('#9c9ebc', '#70758e', '#4c536e'), 'clay': ('#d4ab9f', '#aa7d77', '#785858'),
    'flower': ('#d6b9d9', '#9e87b0', '#726482'), 'light': ('#f2dfa1', '#d2ac6a', '#a17849'),
    'cloud': ('#e1dcec', '#b0a7c8', '#817699'), 'track': ('#c3b9a1', '#948976', '#655d56'),
}
BLOCKS = {key: {'id': key, 'colors': list(colors), 'walkable': key != 'water'} for key, colors in _COLORS.items()}
_FOOTPRINTS = {'house': (2, 2), 'pond': (2, 2), 'bridge': (2, 1), 'moon-gate': (2, 1)}
_BLOCKING = {'tree', 'pond', 'fence', 'house', 'desk', 'bamboo', 'stone-lion', 'planter', 'tea-table'}
DECORATIONS = {key: {'id': key, 'size': list(_FOOTPRINTS.get(key, (1, 1))), 'blocking': key in _BLOCKING}
               for key in ('bench', 'tree', 'flowers', 'pond', 'fence', 'house', 'desk', 'lamp', 'lantern',
                           'moon-gate', 'wind-bell', 'bamboo', 'banner', 'stone-lion', 'paper-crane',
                           'bridge', 'planter', 'cushion', 'tea-table', 'star')}


def catalog():
    return {'standard': contract(), 'size': SIZE, 'max_height': MAX_HEIGHT, 'blocks': BLOCKS, 'decorations': DECORATIONS,
            'pets': list(ROSTER), 'all_free': ALL_COSMETICS_FREE, 'view': VIEW,
            'dig': DIG_MODE, 'online': ONLINE_PLAZA, 'collect_ages': COLLECT_AGES,
            'chat': CHAT_MODE, 'friends': FRIENDS, 'money': MONEY_ENABLED}
