"""Offline catalog/shared-hash checks for the unique current-case entry."""
from importlib import import_module
import hashlib
import json
from pathlib import Path

import pytest

preflight = import_module('XPolicyLab.policy.SenseNova-RoboRSI.preflight')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def skillpack(tmp_path):
    root = tmp_path/'skillpack'
    general = root/'common/skills/robo-general/SKILL.md'
    general.parent.mkdir(parents=True)
    general.write_text('---\nname: robo-general\n---\nRobot operation.\n')
    core = root/'common/agent_core.md'
    core.write_text('Frozen core prompt.\n')
    frozen = {str(path.relative_to(root)): digest(path) for path in (general, core)}
    (root/'frozen_common.json').write_text(json.dumps(frozen))
    case = root/'cases/skills/stack_bowls'
    (case/'assets').mkdir(parents=True)
    (case/'SKILL.md').write_text('---\nname: stack_bowls\n---\nNest the bowls.\n')
    (case/'assets/reference.jpg').write_bytes(b'synthetic public image fixture')
    catalog = dict(schema_version=1, subagents_enabled=False,
                   raw_icl_training_material_in_runtime=False,
                   frozen_general_sha256=digest(general),
                   tasks={
                       'stack_bowls': dict(kind='standard', base_task='stack_bowls', case_skill='stack_bowls'),
                       'stack_bowls_random': dict(kind='generalization', base_task='stack_bowls', case_skill='stack_bowls'),
                       'align_blocks': dict(kind='open', base_task=None, case_skill=None),
                   }, packs={'stack_bowls': dict(directory='skills/stack_bowls', sha256={
                       str(path.relative_to(case)): digest(path) for path in case.rglob('*') if path.is_file()
                   })})
    (root/'cases/catalog.json').write_text(json.dumps(catalog))
    (tmp_path/'submission.json').write_text(json.dumps({'skillpack': {
        'catalog_path': 'skillpack/cases/catalog.json',
        'catalog_sha256': digest(root/'cases/catalog.json'),
        'frozen_common_path': 'skillpack/frozen_common.json',
        'frozen_common_sha256': digest(root/'frozen_common.json'),
    }}))
    return tmp_path


def repin_catalog(policy, catalog):
    path = policy/'skillpack/cases/catalog.json'
    path.write_text(json.dumps(catalog))
    submission = policy/'submission.json'
    metadata = json.loads(submission.read_text())
    metadata['skillpack']['catalog_sha256'] = digest(path)
    submission.write_text(json.dumps(metadata))


@pytest.mark.parametrize('task,case,skills', [
    ('stack_bowls', 'stack_bowls', ['robo-general', 'stack_bowls']),
    ('stack_bowls_random', 'stack_bowls', ['robo-general', 'stack_bowls_random']),
    ('align_blocks', None, ['robo-general']),
])
def test_selection_is_current_task_or_general_only(skillpack, task, case, skills):
    result = preflight.validate_case_skillpack(skillpack, task)
    assert result['selection']['case_skill'] == case
    assert result['selection']['skills'] == skills
    assert result['selection']['task'] == task
    assert result['raw_icl_materialized'] is False
    assert result['runtime_subagents_enabled'] is False
    assert not (skillpack/'runtime').exists()


def test_unknown_task_rejected(skillpack):
    with pytest.raises(ValueError, match='Unknown public RoboDojo task'):
        preflight.validate_case_skillpack(skillpack, 'missing')


@pytest.mark.parametrize('relative', ['common/agent_core.md', 'cases/skills/stack_bowls/assets/reference.jpg'])
def test_changed_shared_or_case_bytes_rejected(skillpack, relative):
    (skillpack/'skillpack'/relative).write_text('changed')
    with pytest.raises(ValueError, match='SHA-256 mismatch'):
        preflight.validate_case_skillpack(skillpack, 'stack_bowls')


def test_open_mapping_cannot_load_a_case(skillpack):
    path = skillpack/'skillpack/cases/catalog.json'
    catalog = json.loads(path.read_text())
    catalog['tasks']['align_blocks']['case_skill'] = 'stack_bowls'
    repin_catalog(skillpack, catalog)
    with pytest.raises(ValueError, match='Open task must use only robo-general'):
        preflight.validate_case_skillpack(skillpack, 'align_blocks')


def test_unlisted_case_material_rejected(skillpack):
    (skillpack/'skillpack/cases/skills/stack_bowls/old-preparation.md').write_text('extra input')
    with pytest.raises(ValueError, match='files differ from the catalog'):
        preflight.validate_case_skillpack(skillpack, 'stack_bowls')


def test_case_directory_cannot_escape_library(skillpack):
    path = skillpack/'skillpack/cases/catalog.json'
    catalog = json.loads(path.read_text())
    catalog['packs']['stack_bowls']['directory'] = '../common'
    repin_catalog(skillpack, catalog)
    with pytest.raises(ValueError, match='must stay within'):
        preflight.validate_case_skillpack(skillpack, 'stack_bowls')



@pytest.mark.parametrize('relative', ['cases/catalog.json', 'frozen_common.json'])
def test_changed_catalog_or_shared_manifest_pin_rejected(skillpack, relative):
    path = skillpack/'skillpack'/relative
    path.write_text(path.read_text()+'\n')
    with pytest.raises(ValueError, match='SHA-256 mismatch'):
        preflight.validate_case_skillpack(skillpack, 'stack_bowls')


def test_committed_skillpack_is_complete_and_hash_verified():
    policy = Path(__file__).resolve().parents[1]
    result = preflight.validate_case_skillpack(policy, 'arrange_largest_number_random')
    assert result['case_packs_verified'] == 26
    assert result['selection']['case_skill'] == 'arrange_largest_number'
    assert result['selection']['skills'] == ['robo-general', 'arrange_largest_number_random']
