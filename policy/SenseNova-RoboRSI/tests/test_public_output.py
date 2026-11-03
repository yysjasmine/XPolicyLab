"""Model-facing precision/size contracts, without robot or provider execution."""
import ast
from copy import deepcopy
from decimal import Decimal
from importlib import import_module, util
import json
import math
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

POLICY = Path(__file__).resolve().parents[1]
CASES = POLICY / 'skillpack/cases/skills'
CLIS = sorted(CASES.glob('*/scripts/*.py'))
CLIS = [p for p in CLIS if p.name != 'geometry.py']


def load(path):
    spec = util.spec_from_file_location('_public_' + path.parent.parent.name, path)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def precision(value):
    if isinstance(value, float):
        assert math.isfinite(value)
        assert Decimal(str(value)).as_tuple().exponent >= -3
        assert value != 0 or math.copysign(1, value) == 1
    elif isinstance(value, (list, tuple)):
        for item in value:
            precision(item)
    elif isinstance(value, dict):
        for item in value.values():
            precision(item)


@pytest.mark.parametrize('path', CLIS, ids=lambda p: p.parent.parent.name)
def test_every_cli_formats_nested_output_and_preserves_input(path):
    module = load(path)
    formatter = getattr(module, 'rounded', None) or module.display
    source = {'state': (1.23456789, {'array': np.array([-.00001, .987654321]),
                                   'scalar': np.float32(.12345678)}),
              'id': 'part-0.123456789', 'integer': 1234567890123, 'flag': True}
    result = formatter(source)
    precision(result)
    assert result['state'] == [1.235, {'array': [0.0, .988], 'scalar': .123}]
    assert result['id'] == source['id'] and result['integer'] == source['integer']
    assert result['flag'] is True
    assert source['state'][0] == 1.23456789
    np.testing.assert_array_equal(source['state'][1]['array'], [-.00001, .987654321])
    for invalid in (float('nan'), float('inf')):
        with pytest.raises(ValueError, match='finite'):
            formatter({'bad': invalid})


def test_all_26_cli_serializers_use_compact_rounded_json():
    assert len(CLIS) == 26
    for path in CLIS:
        tree = ast.parse(path.read_text())
        names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        formatter = 'rounded' if 'rounded' in names else 'display'
        dumps = [n.args[0] for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                 and n.func.id == 'print' and n.args and isinstance(n.args[0], ast.Call)
                 and isinstance(n.args[0].func, ast.Attribute)
                 and n.args[0].func.attr == 'dumps']
        assert len(dumps) == 1, path
        dump = dumps[0]
        assert isinstance(dump.args[0], ast.Call)
        assert dump.args[0].func.id == formatter, path
        keywords = {k.arg: ast.literal_eval(k.value) for k in dump.keywords}
        assert keywords['allow_nan'] is False and keywords['separators'] == (',', ':')
        assert 'indent' not in keywords


OFFSET = {'tcp_position': [.123456789, .234567891, .876543219],
          'object_position': [.133456789, .254567891, .856543219],
          'destination_position': [-.234567891, -.123456789, .765432198],
          'tcp_yaw_deg': 22.222222, 'yaw_change_deg': 33.333333}


@pytest.mark.parametrize('case,command,data,extra', [
    ('arrange_largest_number', 'project', None,
     ['--pixel', '187', '285', '--plane-z', '.774123456789']),
    ('arrange_largest_number', 'plan',
     {'digits': [{'id': 'digit-a', 'value': 8, 'position': [.123456789, 0, .774]}],
      'pads': [{'id': 'pad-a', 'position': [-.23456789, 0, .77]}]}, []),
    ('build_tower', 'offset', OFFSET, []),
    ('classify_objects', 'fit', {'inner_size_xy': [.333333333, .222222222],
      'object_size_xy': [.123456789, .076543219], 'yaw_deg': 33.333333333}, []),
    ('fold_clothes', 'point', {'left_hem': [-.2, 0, .765], 'right_hem': [.2, 0, .765],
      'collar': [0, .3, .765]}, ['--lateral-fraction', '.333333333', '--height', '.0123456789']),
    ('put_bottles_into_dustbin', 'body-point', {'base': [.123456789, .23456789, .7654321],
      'cap': [.135, .245, .93], 'fraction': .333333333}, []),
    ('play_tic_tac_toe', 'cells', {'corners_uv': [[0, 0], [100.123456, 0],
      [100.123456, 100.654321], [0, 100.654321]]}, []),
    ('push_T', 'alignment', {'block': {'center_xy_m': [.123456789, .23456789],
      'yaw_deg': 33.33333333}, 'pad': {'center_xy_m': [0, 0], 'yaw_deg': 22.222222}}, []),
])
def test_actual_cli_output_is_one_rounded_json_line(case, command, data, extra, tmp_path):
    script = CASES / case / 'scripts' / ('arrange.py' if case == 'arrange_largest_number' else 'assist.py')
    args = [sys.executable, str(script), command, *extra]
    if data is not None:
        source = tmp_path / 'input.json'
        source.write_text(json.dumps(data))
        args.extend(['--input', str(source)])
    run = subprocess.run(args, text=True, capture_output=True, check=True)
    assert len(run.stdout.splitlines()) == 1
    precision(json.loads(run.stdout))
    assert len(run.stdout) < 2000


def test_documented_batch_projection_rounds_results_not_calibration():
    geometry = load(CASES / 'arrange_largest_number/scripts/geometry.py')
    assert geometry.HEAD_K[0, 0] == 288.13254096884566
    assert geometry.HEAD_T[1, 1] == .8660253435950835
    actual = geometry.pixel_to_plane([[187, 285], [223, 247]], .774)
    assert actual.shape == (2, 3)
    np.testing.assert_array_equal(actual, [[-.261, -.204, .774], [-.205, -.119, .774]])
    precision(actual.tolist())


def test_robot_output_rounds_nested_arrays_without_quantizing_native_state():
    profile = import_module('XPolicyLab.policy.SenseNova-RoboRSI.execution_profile')
    runtime = profile.runtime_modules(POLICY)
    native = {'observation': {'control_step': 123,
              'state': {'left_ee_pose': [.123456789, .234567891, .876543219, 1., 0., 0., 0.]},
              'diagnostics': (np.array([.123456789, -.00001]), np.float32(.987654321))}}
    original = deepcopy(native['observation']['state'])
    tools = runtime.robot_tools.RobotTools(lambda req: native, setting='simate_vla')
    result = tools.execute_tool('observe', {}).result
    precision(result)
    assert result['observation']['diagnostics'] == [[.123, 0.0], .988]
    assert native['observation']['state'] == original
    assert native['observation']['state']['left_ee_pose'][0] == .123456789


def test_vla_preview_and_act_results_do_not_quantize_stored_proposal():
    profile = import_module('XPolicyLab.policy.SenseNova-RoboRSI.execution_profile')
    runtime = profile.runtime_modules(POLICY)
    arm = {'position': [.123456789, .234567891, .876543219],
           'quaternion_wxyz': [1., 0., 0., 0.], 'gripper_opening': .333333333}
    proposal = {'proposal_id': 'fixture', 'targets': [deepcopy({'left': arm, 'right': arm}) for _ in range(50)],
                'out_of_bounds': [[] for _ in range(50)], 'inference_latency_s': .123456789}
    original = deepcopy(proposal)
    calls = []
    def bridge(request):
        calls.append(deepcopy(request))
        if request['op'] == 'vla_propose':
            return proposal
        return {'observation': {'control_step': 7, 'latency_s': .123456789}}
    tools = runtime.robot_tools.RobotTools(bridge, setting='simate_vla')
    preview = tools.execute_tool('vla_propose', {'subgoal': 'Move above the object.'}).result
    precision(preview)
    assert len(preview['targets']) == 10
    result = tools.execute_tool('act', {'proposal_id': 'fixture', 'steps': 5,
        'reason': 'Use the accepted prefix.', 'assessment': {'current_subgoal': 'Approach.'}}).result
    precision(result)
    assert calls[-1]['op'] == 'vla_review' and calls[-1]['targets'] is None
    assert calls[-1]['approved_steps'] == 5
    assert proposal == original
