"""Independent rollback proof through real proposal, approval and task execution."""
import os
from pathlib import Path
import pytest
from tests.repoops.test_forge_actions import world
from tests.repoops._harness import context, door
from tests.test_approved_destination_identity_r6 import (
    _billing_world, _approved_patch, _patch_step,
    VAT_RATE_PATCH, TAX_BUGGY, RATE_BUGGY,
)

@pytest.mark.parametrize('new_path,new_text', [
    ('billing/created_note.txt', 'reviewed invoice note'),
    ('billing/conversion_helper.py', 'RATIO = 0.125'),
])
def test_partial_patch_restores_absence_of_a_new_file(world, monkeypatch, new_path, new_text):
    from core.execution import artifacts
    root, _, _ = world
    _billing_world(root)
    (root / 'decoy').mkdir()
    (root / 'decoy/rate.py').write_text(RATE_BUGGY)
    patch = (f'diff --git a/{new_path} b/{new_path}\nnew file mode 100644\n'
             f'--- /dev/null\n+++ b/{new_path}\n@@ -0,0 +1 @@\n+{new_text}\n') + VAT_RATE_PATCH
    ctx = context(root, session='review-created-file-rollback')
    task_id, args = _approved_patch(ctx, patch)
    original = artifacts._open_pinned_directory
    def retarget_final_file(parent):
        if Path(parent).name == 'pricing' and not (root / 'pricing').is_symlink():
            os.rename(root / 'pricing', root / 'pricing-moved')
            (root / 'pricing').symlink_to(root / 'decoy', target_is_directory=True)
        return original(parent)
    monkeypatch.setattr(artifacts, '_open_pinned_directory', retarget_final_file)
    refused = door('code.task.step', _patch_step(task_id, args), ctx)
    assert not refused.ok and refused.status == 'destination_changed', (refused.status, refused.response_text)
    assert (root / 'billing/vat.py').read_text() == TAX_BUGGY
    assert (root / 'pricing-moved/rate.py').read_text() == RATE_BUGGY
    assert (root / 'decoy/rate.py').read_text() == RATE_BUGGY
    assert not (root / new_path).exists(), (refused.response_text, refused.details, (root/new_path).read_bytes())
