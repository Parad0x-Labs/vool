"""Inert pet packs and local reaction clips; shared by Toybox and the creator."""
from __future__ import annotations

import array
import base64
import hashlib
import json
import math
import re
import sys
import wave
from io import BytesIO

from core.pet_world.catalog import ROSTER
from core.pet_world.model import CUSTOM_ID, YardError, integer, keys, loads, plain_name, require

PACK_BYTES = 4*1024*1024
PNG_BYTES = 1024*1024
WAV_BYTES = 256*1024
CELL = 48
STATES = ('idle', 'starting', 'thinking', 'tool', 'waiting', 'approval', 'success', 'failure', 'cancelled', 'unknown', 'retry')
EMOTES = ('love', 'happy', 'surprise', 'spar')
EXPRESSIONS = (*STATES, *EMOTES, 'nap', 'work', 'wave')
EVENTS = (*STATES, 'read', 'search', 'code', 'test', 'git', 'run', *EMOTES)
DIRECTIONS = ('front', 'left', 'right')
BUILTIN_SOUNDS = tuple(f'builtin:{voice}-{language}-{emotion}' for voice in ('bright', 'deep')
                       for language in ('en', 'ja', 'zh') for emotion in EMOTES)
SHA = re.compile('[a-f0-9]{64}')
IDENTIFIER = re.compile('[a-z][a-z0-9-]{0,63}')


def text(value, maximum):
    require(type(value) is str and bool(value) and plain_name(value, maximum))
    return value


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def unpack(value, maximum):
    require(type(value) is str and len(value) <= (maximum+2)//3*4, 'size', 413)
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, UnicodeError) as exc:
        raise YardError('invalid') from exc
    require(len(raw) <= maximum, 'size', 413)
    return raw


def png(raw):
    from PIL import Image

    require(type(raw) is bytes and len(raw) <= PNG_BYTES and raw.startswith(b'\x89PNG\r\n\x1a\n'), 'atlas')
    try:
        with Image.open(BytesIO(raw)) as image:
            width, height = image.size
            require(width % CELL == height % CELL == 0 and 1 <= width//CELL <= 16
                    and 1 <= height//CELL <= 32 and width*height <= 1_000_000, 'atlas')
            image.load()
            output = BytesIO()
            image.convert('RGBA').save(output, format='PNG', compress_level=9)
    except (OSError, ValueError, Image.DecompressionBombError):
        require(False, 'atlas')
    result = output.getvalue()
    require(len(result) <= PNG_BYTES, 'atlas')
    return result, width, height


def wav(raw):
    """Canonical mono PCM. Browser imports resample user files before this door."""
    require(type(raw) is bytes and 44 <= len(raw) <= WAV_BYTES, 'audio')
    try:
        with wave.open(BytesIO(raw), 'rb') as source:
            require(source.getnchannels() == 1 and source.getsampwidth() == 2
                    and source.getframerate() == 24000 and source.getcomptype() == 'NONE'
                    and 2400 <= source.getnframes() <= 120000, 'audio')
            frames = source.readframes(source.getnframes())
            require(len(frames) == source.getnframes()*2, 'audio')
    except (wave.Error, EOFError, OSError):
        require(False, 'audio')
    samples = array.array('h', frames)
    if sys.byteorder != 'little':
        samples.byteswap()
    peak = max(abs(n) for n in samples)/32768
    rms = math.sqrt(sum(n*n for n in samples)/len(samples))/32768
    require(rms >= .0001, 'audio')
    factor = min(1, .45/max(peak, .0001), .12/rms)
    normalized = array.array('h', (round(n*factor) for n in samples))
    if sys.byteorder != 'little':
        normalized.byteswap()
    output = BytesIO()
    with wave.open(output, 'wb') as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(24000)
        target.writeframes(normalized.tobytes())
    return output.getvalue(), len(samples)/24000


def frame_map(value, cells):
    require(type(value) is dict and set(STATES) <= set(value) <= set(EXPRESSIONS), 'frames')
    for directions in value.values():
        keys(directions, DIRECTIONS)
        for frames in directions.values():
            require(type(frames) is list and 1 <= len(frames) <= 8
                    and all(integer(n, 0, cells-1) for n in frames), 'frames')
    return value


def bindings(value, expressions, sounds):
    require(type(value) is dict and set(value) <= set(EVENTS), 'bindings')
    for event, binding in value.items():
        keys(binding, ('expression', 'sound'))
        require(type(binding['expression']) is str and binding['expression'] in expressions
                and type(binding['sound']) is str and binding['sound'] in sounds, 'bindings')
        if event in ('unknown', 'failure', 'cancelled'):
            require(binding['expression'] == event, 'bindings')
    return value


def validate_pack(raw):
    pack = loads(raw, PACK_BYTES)
    keys(pack, ('schema', 'version', 'id', 'name', 'author', 'license', 'description', 'atlas', 'frames', 'clips', 'bindings'))
    require(pack['schema'] == 'vool.pet.v1' and integer(pack['version'], 1, 1))
    require(type(pack['id']) is str and IDENTIFIER.fullmatch(pack['id']))
    for field, maximum in (('name', 64), ('author', 64), ('license', 64), ('description', 300)):
        text(pack[field], maximum)
    keys(pack['atlas'], ('cell', 'png'))
    require(integer(pack['atlas']['cell'], CELL, CELL), 'atlas')
    art, width, height = png(unpack(pack['atlas']['png'], PNG_BYTES))
    frames = frame_map(pack['frames'], width*height//(CELL*CELL))
    assets = {digest(art): ('image/png', art)}
    require(type(pack['clips']) is dict and len(pack['clips']) <= 16, 'audio')
    clips = {}
    for identifier, clip in pack['clips'].items():
        require(type(identifier) is str and IDENTIFIER.fullmatch(identifier), 'audio')
        keys(clip, ('name', 'wav'))
        text(clip['name'], 64)
        audio, seconds = wav(unpack(clip['wav'], WAV_BYTES))
        sha = digest(audio)
        assets[sha] = ('audio/wav', audio)
        clips[identifier] = {'name': clip['name'], 'sha256': sha, 'seconds': seconds}
    bindings(pack['bindings'], frames, {'', *clips})
    metadata = {key: pack[key] for key in ('schema', 'version', 'id', 'name', 'author', 'license', 'description')}
    metadata.update(atlas={'cell': CELL, 'sha256': digest(art), 'width': width, 'height': height},
                    frames=frames, clips=clips, bindings=pack['bindings'])
    canonical = json.dumps(metadata, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return metadata, canonical, digest(canonical.encode()), assets


def validate_metadata(raw, expected):
    value = loads(raw, 131072)
    require(digest(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()) == expected, 'corrupt')
    keys(value, ('schema', 'version', 'id', 'name', 'author', 'license', 'description', 'atlas', 'frames', 'clips', 'bindings'))
    require(value['schema'] == 'vool.pet.v1' and integer(value['version'], 1, 1))
    require(type(value['id']) is str and IDENTIFIER.fullmatch(value['id']))
    for field, maximum in (('name', 64), ('author', 64), ('license', 64), ('description', 300)):
        text(value[field], maximum)
    keys(value['atlas'], ('cell', 'sha256', 'width', 'height'))
    art = value['atlas']
    require(integer(art['cell'], CELL, CELL) and type(art['sha256']) is str and SHA.fullmatch(art['sha256'])
            and integer(art['width'], CELL, 768) and integer(art['height'], CELL, 1536)
            and art['width'] % CELL == art['height'] % CELL == 0 and art['width']*art['height'] <= 1_000_000, 'corrupt')
    frame_map(value['frames'], art['width']*art['height']//(CELL*CELL))
    require(type(value['clips']) is dict and len(value['clips']) <= 16)
    for identifier, clip in value['clips'].items():
        require(type(identifier) is str and IDENTIFIER.fullmatch(identifier))
        keys(clip, ('name', 'sha256', 'seconds'))
        text(clip['name'], 64)
        require(type(clip['sha256']) is str and SHA.fullmatch(clip['sha256']) and type(clip['seconds']) in (int, float)
                and math.isfinite(clip['seconds']) and .1 <= clip['seconds'] <= 5)
    bindings(value['bindings'], value['frames'], {'', *value['clips']})
    return value


def default_config():
    return {'selected': '', 'sound': False, 'voice': 'auto', 'language': 'auto', 'bindings': {}}


def validate_config(raw, pets, sounds):
    value = loads(raw, 131072)
    keys(value, ('selected', 'sound', 'voice', 'language', 'bindings'))
    require(type(value['selected']) is str and value['selected'] in {'', *ROSTER, *pets}
            and type(value['sound']) is bool and value['voice'] in ('auto', 'bright', 'deep')
            and value['language'] in ('auto', 'en', 'ja', 'zh'), 'bindings')
    require(type(value['bindings']) is dict and len(value['bindings']) <= len(ROSTER)+16, 'bindings')
    for pet, choices in value['bindings'].items():
        require(type(pet) is str and pet in {*ROSTER, *pets}, 'bindings')
        expressions = pets[pet]['frames'] if CUSTOM_ID.fullmatch(pet) else EXPRESSIONS
        bindings(choices, expressions, {'', 'auto', *BUILTIN_SOUNDS, *sounds})
    return value
