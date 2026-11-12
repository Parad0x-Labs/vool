"""Pinned documented X request shapes, shared by discovery, approval and dispatch."""
from __future__ import annotations

import base64
import json
import re
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote

MAX_REQUEST_BYTES = 16 * 1024 * 1024
MAX_QUERY_BYTES = 16 * 1024


#: Reads of the owner's private bookmarks: like the bookmark actions, each one waits for Settings
#: approval instead of running straight from a chat.
APPROVAL_READS = frozenset({'getUsersBookmarks', 'getUsersBookmarkFolders', 'getUsersBookmarksByFolderId'})


class InvalidXRequest(ValueError):
    pass


def needs_approval(entry: dict[str, Any]) -> bool:
    return entry['method'] != 'GET' or entry['id'] in APPROVAL_READS


@lru_cache(maxsize=1)
def _catalog() -> dict[str, Any]:
    return json.loads(Path(__file__).with_name('x_endpoints.json').read_text(encoding='utf-8'))


def operations() -> list[dict[str, Any]]:
    return deepcopy(_catalog()['operations'])


def source_digest() -> str:
    return _catalog()['source_sha256']


def operation(operation_id: str) -> dict[str, Any]:
    for entry in _catalog()['operations']:
        if entry['id'] == operation_id:
            return deepcopy(entry)
    raise InvalidXRequest('Unknown X operation.')


def schema_details(name: str) -> dict[str, Any]:
    if name not in _catalog()['schemas']:
        raise InvalidXRequest('Unknown X request schema.')
    return deepcopy(_catalog()['schemas'][name])


def _schema(schema: dict[str, Any]) -> dict[str, Any]:
    seen = set()
    while ref := schema.get('$ref'):
        if ref in seen:
            raise InvalidXRequest('Circular X request schema.')
        seen.add(ref)
        name = str(ref).removeprefix('#/components/schemas/')
        try:
            schema = _catalog()['schemas'][name]
        except KeyError:
            raise InvalidXRequest('Unknown X request schema.') from None
    return schema


def _json_identity(value: Any):
    if isinstance(value, dict):
        return ('object', tuple(sorted((key, _json_identity(item)) for key, item in value.items())))
    if isinstance(value, list):
        return ('array', tuple(_json_identity(item) for item in value))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return ('number', value)
    return (type(value).__name__, value)


def validate(value: Any, schema: dict[str, Any], label: str, *, depth: int = 0) -> None:
    """Validate the frozen provider request schema before any credential read or effect."""
    if depth > 40:
        raise InvalidXRequest('X request nesting is too deep.')
    schema = _schema(schema)
    if value is None and schema.get('nullable'):
        return
    for child in schema.get('allOf', []):
        validate(value, child, label, depth=depth + 1)
    for keyword in ('oneOf', 'anyOf'):
        if keyword in schema:
            matched = 0
            for child in schema[keyword]:
                try:
                    validate(value, child, label, depth=depth + 1)
                    matched += 1
                except InvalidXRequest:
                    pass
            if matched == 0 or (keyword == 'oneOf' and matched != 1):
                raise InvalidXRequest(f'{label} does not match its documented shape.')
    kind = schema.get('type')
    types = {'string': str, 'integer': int, 'number': (int, float),
             'boolean': bool, 'array': list, 'object': dict}
    if kind in types and (not isinstance(value, types[kind]) or
                          (kind in {'integer', 'number'} and isinstance(value, bool))):
        raise InvalidXRequest(f'{label} must be {kind}.')
    if 'enum' in schema and value not in schema['enum']:
        raise InvalidXRequest(f'{label} is not an allowed value.')
    if isinstance(value, str):
        if len(value) < schema.get('minLength', 0) or len(value) > schema.get('maxLength', MAX_REQUEST_BYTES):
            raise InvalidXRequest(f'{label} has an invalid length.')
        if schema.get('pattern') and re.search(schema['pattern'], value) is None:
            raise InvalidXRequest(f'{label} has an invalid format.')
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < schema.get('minimum', float('-inf')) or value > schema.get('maximum', float('inf')):
            raise InvalidXRequest(f'{label} is outside its allowed range.')
    if isinstance(value, list):
        if len(value) < schema.get('minItems', 0) or len(value) > schema.get('maxItems', 1000):
            raise InvalidXRequest(f'{label} has an invalid number of items.')
        for item in value:
            validate(item, schema.get('items', {}), label, depth=depth + 1)
        if schema.get('uniqueItems'):
            try:
                unique = {_json_identity(item) for item in value}
            except TypeError:
                raise InvalidXRequest(f'{label} must contain JSON values.') from None
            if len(unique) != len(value):
                raise InvalidXRequest(f'{label} must contain unique items.')
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise InvalidXRequest(f'{label} needs text field names.')
        if set(schema.get('required', [])) - set(value):
            raise InvalidXRequest(f'{label} is missing required fields.')
        props = schema.get('properties', {})
        extra = schema.get('additionalProperties', True)
        for key, item in value.items():
            if key in props:
                validate(item, props[key], f'{label}.{key}', depth=depth + 1)
            elif extra is False:
                raise InvalidXRequest(f'{label} has an unknown field.')
            elif isinstance(extra, dict):
                validate(item, extra, f'{label}.{key}', depth=depth + 1)


def prepare(operation_id: str, *, path: dict[str, Any] | None = None,
            query: dict[str, Any] | None = None, body: Any = None) -> dict[str, Any]:
    entry = operation(operation_id)
    if entry['deferred']:
        raise InvalidXRequest("This X feature needs an extra X permission that VOOL's sign-in does not ask for. Nothing was sent.")
    path = {} if path is None else path
    query = {} if query is None else query
    if not isinstance(path, dict) or not isinstance(query, dict):
        raise InvalidXRequest('X path and query fields must be objects.')
    expected = {place: {p['name']: p for p in entry['parameters'] if p['in'] == place}
                for place in ('path', 'query')}
    clean_query: dict[str, Any] = {}
    url_path = entry['path']
    for place, supplied in (('path', path), ('query', query)):
        if set(supplied) - set(expected[place]):
            raise InvalidXRequest(f'Unknown X {place} field.')
        for name, parameter in expected[place].items():
            if name not in supplied:
                if parameter.get('required'):
                    raise InvalidXRequest(f'Missing X {place} field: {name}.')
                continue
            value = supplied[name]
            validate(value, parameter.get('schema', {}), name)
            if place == 'path':
                text = str(value)
                if text in {'.', '..'} or '/' in text or '\\' in text or '%' in text:
                    raise InvalidXRequest('Invalid X path value.')
                url_path = url_path.replace('{' + name + '}', quote(text, safe=''))
            else:
                clean_query[name] = ','.join(str(v).lower() if isinstance(v, bool) else str(v)
                                           for v in value) if isinstance(value, list) else (
                                               str(value).lower() if isinstance(value, bool) else value)
    request_body = entry['request_body']
    if body is None and request_body.get('required'):
        raise InvalidXRequest('This X operation needs a request body.')
    if body is not None:
        content = request_body.get('content', {})
        shape = content.get('application/json')
        if shape:
            validate(body, shape.get('schema', {}), 'body')
        elif 'application/octet-stream' in content:
            if not isinstance(body, dict) or set(body) != {'base64'} or not isinstance(body['base64'], str):
                raise InvalidXRequest('This upload needs a base64-encoded body.')
            try:
                base64.b64decode(body['base64'], validate=True)
            except (ValueError, TypeError):
                raise InvalidXRequest('Invalid base64 upload.') from None
        else:
            raise InvalidXRequest('This X operation does not accept a body.')
    try:
        encoded = json.dumps({'path': path, 'query': query, 'body': body}, allow_nan=False).encode()
    except (ValueError, TypeError, RecursionError):
        raise InvalidXRequest('X request values must be finite JSON data.') from None
    if len(encoded) > MAX_REQUEST_BYTES or len(json.dumps(clean_query).encode()) > MAX_QUERY_BYTES:
        raise InvalidXRequest('X request is too large.')
    return {'operation_id': operation_id, 'method': entry['method'], 'path': url_path,
            'query': clean_query, 'body': deepcopy(body), 'stream': entry['stream'],
            'content_type': 'application/octet-stream' if body is not None and
            'application/octet-stream' in request_body.get('content', {}) else 'application/json'}


def match(method: str, path: str) -> str:
    """Resolve a legacy request to the same policy identity as the documented tool."""
    for entry in _catalog()['operations']:
        pattern = re.sub(r'\\\{[^}]+\\\}', r'[^/]+', re.escape(entry['path']))
        if entry['method'] == method.upper() and re.fullmatch(pattern, path):
            return entry['id']
    raise InvalidXRequest('The X route is not in the documented catalog.')
