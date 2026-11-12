"""Local operator doors for the Yard and inert cosmetics. No model or network calls."""
from __future__ import annotations

import base64
import json
import sqlite3
from contextlib import closing

from core import pet_cosmetics
from core.pet_world import lifeform_bridge, worlds
from core.pet_world.catalog import MAX_BYTES, catalog
from core.pet_world.model import CUSTOM_ID, YardError, keys, require, validate
from core.pet_world.race import race
from core.request_trust import is_loopback_host
from storage import pet_cosmetics_store, pet_world_store


def companion_state():
    """Coarse runtime truth only; no chat text, summaries or execution details."""
    from core.live_turns import live_checkpoint_ids

    try:
        with closing(lifeform_bridge._read_conn()) as conn:
            rows = conn.execute("SELECT status,last_checkpoint_id FROM runtime_sessions WHERE status IN ('running','pending_approval','waiting_approval') ORDER BY updated_at DESC LIMIT 32").fetchall()
        if any(r['status'] in ('pending_approval', 'waiting_approval') for r in rows):
            return 'approval'
        active = live_checkpoint_ids()
        if any(r['last_checkpoint_id'] in active for r in rows):
            return 'tool'
        return 'unknown' if rows else 'idle'
    except (sqlite3.Error, OSError):
        return 'unknown'


def payload():
    return {'ok': True, **pet_world_store.load(), 'catalog': catalog(),
            'lifeform': lifeform_bridge.inspect(), 'companion': companion_state()}


def get(path, query, headers, runtime, client_host):
    from core.web.api.service import ApiResponse, html_response, json_response

    headers = headers or {}
    if not is_loopback_host(client_host):
        return json_response(403, {'ok': False, 'error': 'owner_local_required'})
    try:
        if path == '/pet-yard':
            from core.pet_world_page import render_pet_world_html
            from core.ui_palette import appearance_from_cookie_header
            from core.web.api.service import request_ui_locale

            cookie = next((v for k, v in headers.items() if k.lower() == 'cookie'), '')
            return html_response(200, render_pet_world_html(request_ui_locale(headers, (query.get('ui_locale') or [''])[0]), appearance_from_cookie_header(cookie)),
                                 headers={'Content-Security-Policy': "default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'unsafe-inline'; font-src data:; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; base-uri 'none'; frame-ancestors 'self'", 'X-Content-Type-Options': 'nosniff'})
        if path == '/api/pet-yard':
            return json_response(200, payload())
        if path == '/api/pet-yard/lifeform':
            return json_response(200, {'ok': True, **lifeform_bridge.inspect()})
        if path == '/api/pet-cosmetics':
            return json_response(200, pet_cosmetics_store.library())
        if path.startswith('/api/pet-cosmetics/assets/'):
            mime, data = pet_cosmetics_store.asset(path.rsplit('/', 1)[-1])
            return ApiResponse(200, mime, data, headers={'Cache-Control': 'private,max-age=31536000,immutable', 'X-Content-Type-Options': 'nosniff'})
        if path.startswith('/api/pet-cosmetics/voices/'):
            from core.pet_world.audio_data import CLIPS

            name = path.rsplit('/', 1)[-1]
            require(name in CLIPS, 'missing', 404)
            return ApiResponse(200, 'audio/mpeg', base64.b64decode(CLIPS[name]), headers={'Cache-Control': 'private,max-age=31536000,immutable', 'X-Content-Type-Options': 'nosniff'})
        raise YardError('missing', 404)
    except YardError as exc:
        return json_response(exc.status, {'ok': False, 'error': exc.code})
    except (sqlite3.Error, OSError):
        return json_response(503, {'ok': False, 'error': 'unavailable'})


def post(path, body, headers, runtime, client_host):
    from core.web.api.registry_authorities import operator_json_guard
    from core.web.api.service import json_response

    # Reuse every operator transport/authentication check. The document door has
    # its own larger explicit cap; it never skips origin, peer or media-type checks.
    guard = operator_json_guard({}, headers, runtime, client_host)
    if guard is not None:
        return guard
    try:
        limit = pet_cosmetics.PACK_BYTES+65536 if path.startswith('/api/pet-cosmetics') else MAX_BYTES+65536
        try:
            require(len(json.dumps(body, ensure_ascii=False).encode()) <= limit, 'size', 413)
        except (TypeError, UnicodeError, RecursionError) as exc:
            raise YardError() from exc
        require(type(body) is dict)
        if path.startswith('/api/pet-cosmetics/'):
            action = path.rsplit('/', 1)[-1]
            if action == 'validate-yard':
                keys(body, ('document',))
                validate(body['document'])
                return json_response(200, {'ok': True})
            if action == 'preview':
                keys(body, ('document',))
                metadata, _, _, assets = pet_cosmetics.validate_pack(body['document'])
                data = assets[metadata['atlas']['sha256']][1]
                return json_response(200, {'ok': True, 'pet': metadata, 'preview': 'data:image/png;base64,'+base64.b64encode(data).decode()})
            if action == 'export':
                keys(body, ('id',))
                return json_response(200, {'ok': True, 'document': pet_cosmetics_store.export(body['id'])})
            return json_response(200, pet_cosmetics_store.change(action, body))
        action = body.get('action')
        if action == 'export_world':
            keys(body, ('action', 'document', 'description'))
            return json_response(200, {'ok': True, 'document': worlds.export(body['document'], body['description'])})
        if action in ('preview_world', 'import_world'):
            keys(body, ('action', 'document') if action == 'preview_world' else ('action', 'document', 'revision', 'stamp', 'confirmed'))
            world = worlds.decode(body['document'])
            known = {p['id'] for p in pet_cosmetics_store.library()['pets']}
            missing = [p['id'] for p in world['pets'] if CUSTOM_ID.fullmatch(p['id']) and p['id'] not in known]
            if action == 'preview_world':
                return json_response(200, {'ok': True, 'world': world, 'yard': worlds.materialize(world), 'missing': missing})
            require(body['confirmed'] is True)
            require(not missing, 'missing_pet', 409)
            current = pet_world_store.load()
            yard = worlds.materialize(world, body['revision'], current['yard']['preferences'])
            pet_world_store.save(worlds.canonical(yard), archive=True, stamp=body['stamp'])
            return json_response(200, payload())
        if action == 'save':
            keys(body, ('action', 'document'))
            yard, _ = validate(body['document'])
            library = pet_cosmetics_store.library()
            known = {p['id'] for p in library['pets']}
            require(all(not CUSTOM_ID.fullmatch(p['id']) or p['id'] in known for p in yard['pets']), 'missing_pet', 409)
            life = lifeform_bridge.inspect()
            require(not any(p['id'] == 'lifeform' for p in yard['pets']) or 'spec' in life, 'growth_unverified', 409)
            return json_response(200, {'ok': True, **pet_world_store.save(body['document'])})
        if action == 'reset':
            keys(body, ('action', 'revision', 'stamp', 'confirmed'))
            return json_response(200, {'ok': True, **pet_world_store.reset(body['revision'], body['stamp'], body['confirmed'])})
        if action == 'race':
            keys(body, ('action', 'document', 'seed'))
            yard, _ = validate(body['document'])
            return json_response(200, {'ok': True, **race(yard, body['seed'], lifeform_bridge.inspect(full=True))})
        if action == 'growth':
            keys(body, ('action',))
            return json_response(200, {'ok': True, **lifeform_bridge.refresh()})
        if action == 'egg':
            keys(body, ('action',))
            from core.command_registry.execute import ExecutionContext, execute_command

            result = execute_command('lifeform.egg', {}, context=ExecutionContext(projection='api', principal='operator'))
            return json_response(200, {'ok': result.ok, 'egg': result.to_json(), 'lifeform': lifeform_bridge.inspect()})
        raise YardError()
    except YardError as exc:
        return json_response(exc.status, {'ok': False, 'error': exc.code})
    except (sqlite3.Error, OSError):
        return json_response(503, {'ok': False, 'error': 'unavailable'})
