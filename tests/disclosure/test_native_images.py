from __future__ import annotations

import base64

from core.chat_attachments import flatten_message_for_ollama
from core.disclosure.sources import AttachmentParts, SourceCatalog, project_messages
from core.disclosure.wire import _select_sections
from core.remote_fetch_policy import remote_fetch_policy_scope

PIXELS=base64.b64encode(b'\x89PNG\r\n\x1a\n'+b'pixel-fixture').decode()
URL='data:image/png;base64,'+PIXELS


def test_native_flattening_preserves_only_first_party_image_source_labels():
    catalog=SourceCatalog()
    parts=AttachmentParts();parts.source='public-image'
    with remote_fetch_policy_scope({'_disclosure_sources':catalog}):
        parts.append({'type':'image_url','image_url':{'url':URL}})
        catalog.add('Describe the image.', 'conversation','*')
        message=flatten_message_for_ollama({'role':'user','content':[{'type':'text','text':'Describe the image.'},*parts]})
    projected,_,_=project_messages([message],project_id='image-project',rules={'revision':1,'rules':[
        {'kind':'attachment','source':'public-image','mode':'allow'}]},catalog=catalog)
    assert projected==[{'role':'user','content':'Describe the image.','images':[PIXELS]}]


def test_image_projection_keeps_exact_bytes_across_overlapping_allowed_fragments():
    catalog=SourceCatalog()
    catalog.add(URL,'attachment','public-image')
    catalog.add('png','conversation','*')
    projected,omitted,_=project_messages([{'role':'user','content':[{'type':'image_url','image_url':{'url':URL}}]}],
        project_id='image-project',rules={'revision':1,'rules':[{'kind':'attachment','source':'public-image','mode':'allow'}]},catalog=catalog)
    assert omitted==0
    assert projected==[{'role':'user','content':[{'type':'image_url','image_url':{'url':URL}}]}]


def test_native_parts_can_keep_text_and_one_image_without_the_other():
    body={'messages':[{'role':'user','content':'Describe the selected image.','images':[PIXELS,PIXELS+'private']} ]}
    assert _select_sections(body,['t:0','i:0:0'])==[
        {'role':'user','content':'Describe the selected image.','images':[PIXELS]}]


def test_private_image_label_wins_after_native_carrier_conversion():
    catalog=SourceCatalog()
    catalog.add(URL,'attachment','public-image')
    catalog.add(URL,'attachment','private-image')
    catalog.add('Describe the image.','conversation','*')
    with remote_fetch_policy_scope({'_disclosure_sources':catalog}):
        message=flatten_message_for_ollama({'role':'user','content':[
            {'type':'text','text':'Describe the image.'},{'type':'image_url','image_url':{'url':URL}}]})
    projected,omitted,_=project_messages([message],project_id='image-project',rules={'revision':1,'rules':[
        {'kind':'attachment','source':'public-image','mode':'allow'},
        {'kind':'attachment','source':'private-image','mode':'local'}]},catalog=catalog)
    assert projected==[{'role':'user','content':'Describe the image.'}]
    assert omitted>0


def test_unknown_or_malformed_image_carriers_stay_local():
    catalog=SourceCatalog();catalog.add('Describe the image.','conversation','*')
    rules={'revision':1,'rules':[{'kind':'attachment','source':'*','mode':'allow'}]}
    for message in [
        {'role':'user','content':'Describe the image.','images':[PIXELS,None,{'url':URL}]},
        {'role':'user','content':[{'type':'text','text':'Describe the image.'},{'type':'image_url','image_url':{}},{'type':'image_url','image_url':'untrusted'}]},
    ]:
        projected,omitted,_=project_messages([message],project_id='image-project',rules=rules,catalog=catalog)
        assert 'images' not in projected[0]
        assert 'image_url' not in str(projected)
        assert omitted>0
