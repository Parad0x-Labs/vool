"""Versioned world units shared by authoring, validation and presentation."""
STANDARD_ID = 'vool-yard.v1'
SIZE = 24
MAX_HEIGHT = 8
TILE = (32, 16, 8)
VIEWPORT = (832, 640)
ATLAS_CELL = 48
PET_SCALE = 2


def contract():
    return {'id': STANDARD_ID, 'chunk': [SIZE, SIZE], 'max_height': MAX_HEIGHT,
            'tile': list(TILE), 'viewport': list(VIEWPORT), 'atlas_cell': ATLAS_CELL,
            'pet_scale': PET_SCALE, 'rotations': 4, 'axes': ['x', 'y', 'up']}
