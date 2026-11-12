"""Final-body options for legacy requests-based provider helpers.

Local calls without a disclosure view keep their existing JSON call contract.
Reviewed calls send the consumed bytes, with no redirect or local proxy escape.
The invocation gateway still owns admission and spend.
"""
from __future__ import annotations

from core.disclosure.wire import DisclosureRefused, WirePayload, note_sent
from core.provider_http import exceptions, is_loopback_url


def request_options(payload: dict, url: str) -> dict:
    if not isinstance(payload, WirePayload):
        raise DisclosureRefused("This helper has no sealed request. Nothing was sent.")
    body = payload.consume_wire(url)
    local = payload.cloud_destination is False or is_loopback_url(url)
    def headers_arrived(response, *_args, **_kwargs):
        received_headers(payload)
        if 300 <= response.status_code < 400:
            raise exceptions.TooManyRedirects("Reviewed provider redirect refused.")
        return response

    options = {"allow_redirects": False, "hooks": {"response": headers_arrived}}
    if local:
        options["proxies"] = {"http": "", "https": "", "all": ""}
    if local and not payload.preview_id:
        options["json"] = dict(payload)
    else:
        options.update(data=body, headers={"Content-Type": "application/json"})
    return options


def received_headers(payload: WirePayload) -> None:
    if payload.preview_id:
        note_sent(payload.preview_id)


def assert_helper_endpoint(url: str) -> bool:
    from core import auto_local_only_mode
    from core.cloud_transport import _operator_cloud_network_allowed
    from core.remote_fetch_policy import remote_fetch_forbidden
    local=auto_local_only_mode.endpoint_is_local(url)
    if not local and (remote_fetch_forbidden() or auto_local_only_mode.turn_is_local_only(None)
                      or not _operator_cloud_network_allowed()):
        raise DisclosureRefused("Cloud models are off for this turn. Nothing was sent.")
    return local


def metadata_options(url: str) -> dict:
    local=assert_helper_endpoint(url)
    def headers_arrived(response,*_args,**_kwargs):
        if 300 <= response.status_code < 400:
            raise exceptions.TooManyRedirects("Provider metadata redirect refused.")
        return response
    options={"allow_redirects":False,"hooks":{"response":headers_arrived}}
    if local:options["proxies"]={"http":"","https":"","all":""}
    return options


def local_metadata_options(url: str) -> dict:
    """Options for a helper's metadata read through the plain ``requests.get`` entry.

    A loopback or private-network metadata read is not a disclosure and keeps the
    call shape local helpers always used (no extra options). Outside endpoints
    keep the reviewed, redirect-refusing options. Pair with
    :func:`refuse_redirected_metadata` on the response.
    """
    if assert_helper_endpoint(url):
        return {}
    return metadata_options(url)


def refuse_redirected_metadata(response):
    """A metadata response that arrived through a redirect is refused."""
    history = getattr(response, "history", None)
    if isinstance(history, (list, tuple)) and history:
        raise exceptions.TooManyRedirects("Provider metadata redirect refused.")
    return response
