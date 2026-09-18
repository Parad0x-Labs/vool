"""Shared forge response validation; transport and authorization stay with KAS."""
import json
from urllib.parse import parse_qsl, urljoin, urlsplit

from core.kas.contract import ForgeRefusedError


def json_payload(body):
    try:
        return json.loads(body.decode('utf-8'))
    except (ValueError, UnicodeError, AttributeError) as exc:
        raise ForgeRefusedError(200, reason='malformed_response') from exc


def object_payload(body):
    value = json_payload(body)
    if not isinstance(value, dict) or not value:
        raise ForgeRefusedError(200, reason='malformed_object')
    return value


def listing_rows(payload, items_key=None):
    rows = payload.get(items_key) if items_key and isinstance(payload, dict) else payload if items_key is None else None
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ForgeRefusedError(200, reason='malformed_listing')
    return rows


def scoped_next_page(first_url, current_url, following, visited):
    """A next-page link can advance pagination, never change the repository or query."""
    target = urljoin(current_url, following)
    first, nxt = urlsplit(first_url), urlsplit(target)
    filters = lambda url: sorted((key, value) for key, value in parse_qsl(url.query, keep_blank_values=True)
                                 if key not in {'page', 'per_page'})
    sizes = [value for key, value in parse_qsl(nxt.query) if key == 'per_page']
    if ((first.scheme, first.netloc, first.path) != (nxt.scheme, nxt.netloc, nxt.path)
            or filters(first) != filters(nxt) or nxt.fragment
            or any(not size.isdigit() or not 1 <= int(size) <= 100 for size in sizes)):
        raise ForgeRefusedError(200, reason='pagination_scope_changed')
    if target in visited:
        raise ForgeRefusedError(200, reason='pagination_cycle')
    return target
