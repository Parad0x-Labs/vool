"""Reviewed, pinned external instruction packs. No downloads on catalogue reads."""

CATALOG = [{'id': 'anthropic-frontend-design',
  'name': 'Frontend design',
  'description': 'Guidance for distinctive web interfaces. Pinned standalone revision; no scripts '
                 'included.',
  'category': 'Coding',
  'kind': 'skill',
  'publisher': 'Anthropic',
  'repository': 'https://github.com/anthropics/skills',
  'ref': '00756142ab04c82a447693cf373c4e0c554d1005',
  'path': 'skills/frontend-design',
  'skill_name': 'frontend-design',
  'license': 'Apache-2.0',
  'sha256': 'b81e2ff87ed8fa4d6c377ccb127a7254c9e6a77e3ae94f21e6b514f7bb2945a0',
  'license_sha256': '0d542e0c8804e39aa7f37eb00da5a762149dc682d7829451287e11b938e94594',
  'requirements': 'A connected model; existing VOOL tools and permissions still apply. No bundled '
                  'executable dependencies.',
  'compatibility': 'Standalone instruction format; model output quality is not certified.'},
 {'id': 'anthropic-brand-guidelines',
  'name': 'Anthropic brand guide',
  'description': 'Anthropic’s own colors and typography. This is their brand guide, not VOOL branding.',
  'category': 'Design',
  'kind': 'skill',
  'publisher': 'Anthropic',
  'repository': 'https://github.com/anthropics/skills',
  'ref': '34040c9c568585f6929bedeaad110ad08f079624',
  'path': 'skills/brand-guidelines',
  'skill_name': 'brand-guidelines',
  'license': 'Apache-2.0',
  'sha256': '1120b3769e2985cefb3d25be981b1f914abeba57ae079b83c20c666c164fa9fe',
  'license_sha256': 'bc6b3af2f331cbc7fb0da1344efb2cbe5877a31498b4d70dbc7000f3405a1362',
  'requirements': 'A connected model; existing VOOL tools and permissions still apply. No bundled '
                  'executable dependencies.',
  'compatibility': 'Standalone instruction format; model output quality is not certified.'}]

# Release-time compatibility checks; sources remain inert until scan and acceptance.
for _entry in CATALOG:
    _entry.update(bundled_source=True, import_ready=True, discovery_only=False)
import json as _json
from pathlib import Path as _Path

_DIRECTORY = _json.loads(_Path(__file__).with_name('addon_directory.json').read_text(encoding='utf-8'))
CATALOG += _DIRECTORY['entries']
