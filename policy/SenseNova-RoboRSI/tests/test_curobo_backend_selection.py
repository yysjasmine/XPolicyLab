"""Bind fresh/default and recovered solver selection without model or physics calls."""
from copy import deepcopy
from importlib import import_module
import json

import pytest

scene = import_module('XPolicyLab.policy.SenseNova-RoboRSI.tests.test_scene_resume_identity')
boundary = scene.boundary


def checkpoint(model, backend):
    pins = scene.model_module.submission()['hybrid']
    path = scene.model_module.POLICY_DIR / pins['profile_path'] / 'resources/fresh-configs/align_blocks-s0.json'
    cfg = json.loads(path.read_text())
    cfg.update(model='fixture-model', reasoning_effort='xhigh', episode_seconds=None)
    cfg['options'].update(model._adaptive_steps)
    cfg['options'].update(codex_compact_tokens=200000, review_window_steps=200,
                          review_max_windows=1, stop_min_steps=200,
                          review_allow_reset=False, max_repeat=1)
    cfg['test_episodes'][0].update(layout_id=14, id='align_blocks-s0-eval0-layout14')
    if backend is not None:
        cfg['options']['eef_execution_backend'] = backend
    return cfg


@pytest.mark.parametrize('resumed,stored,requested,expected', [
    (False, None, None, 'bounded_curobo'),
    (False, None, 'legacy_dls', 'legacy_dls'),
    (True, None, None, 'legacy_dls'),
    (True, 'legacy_dls', None, 'legacy_dls'),
    (True, 'bounded_curobo', None, 'bounded_curobo'),
])
def test_model_config_and_public_backend_use_the_same_solver(
    boundary, monkeypatch, resumed, stored, requested, expected
):
    boundary._agent_config.document['agent']['reasoning_effort'] = 'xhigh'
    if resumed:
        boundary._resume = {'native_identity': scene.receipt(control_step=9),
                            'config': checkpoint(boundary, stored)}
    if requested is not None:
        boundary.model_cfg['eef_execution_backend'] = requested
    boundary.update_obs(scene.observation(scene.receipt()))
    captured = scene._prepare_start(monkeypatch, boundary)
    boundary._start()
    assert captured['backend']['eef_execution_backend'] == expected
    assert captured['context'].cfg['options'].get('eef_execution_backend', 'legacy_dls') == expected
    assert captured['backend']['step_limit'] == 200
    assert captured['context'].cfg['test_episodes'][0]['layout_id'] == 14


@pytest.mark.parametrize('stored,requested', [
    ('legacy_dls', 'bounded_curobo'), ('bounded_curobo', 'legacy_dls'),
    (None, 'bounded_curobo'),
])
def test_checkpoint_cannot_change_its_original_solver(boundary, monkeypatch, stored, requested):
    boundary._agent_config.document['agent']['reasoning_effort'] = 'xhigh'
    boundary._resume = {'native_identity': scene.receipt(control_step=9),
                        'config': checkpoint(boundary, stored)}
    boundary.model_cfg['eef_execution_backend'] = requested
    boundary.update_obs(scene.observation(scene.receipt()))
    captured = scene._prepare_start(monkeypatch, boundary)
    with pytest.raises(ValueError, match='original action/review/context options'):
        boundary._start()
    assert 'backend' not in captured and 'context' not in captured


def test_legacy_checkpoint_without_backend_remains_compatible(boundary):
    old = checkpoint(boundary, None)
    current = deepcopy(old)
    current['options']['eef_execution_backend'] = 'legacy_dls'
    scene.hybrid_resume.validate_policy(old, current)
    assert 'eef_execution_backend' not in old['options']
