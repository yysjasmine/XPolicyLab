"""Select and stage only a task's case skill; shared instructions stay frozen."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import shutil
import uuid


ROOT = Path(__file__).resolve().parent


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class CaseSkillLibrary:
    """Case-only packaging; it does not configure tools, prompts or agents."""

    def __init__(self, directory=ROOT):
        self.directory = Path(directory).resolve(strict=True)
        self.index = json.loads((self.directory/'catalog.json').read_text())
        if self.index['subagents_enabled'] is not False:
            raise ValueError('Runtime subagents must remain disabled')

    def selection(self, task):
        if task not in self.index['tasks']:
            raise ValueError('Unknown task: '+str(task))
        result = deepcopy(self.index['tasks'][task])
        result.update(task=task, subagents_enabled=False,
                      visible_skills=['robo-general']+([task] if result['case_skill'] else []))
        return result

    def verify(self, task):
        """Verify only the selected package, including the single-MD limit."""
        selected = self.selection(task)
        if selected['case_skill'] is None:
            if selected['kind'] != 'open':
                raise ValueError('Only open tasks may omit a case skill')
            return selected
        pack = self.index['packs'][selected['case_skill']]
        root = (self.directory/pack['directory']).resolve(strict=True)
        if not root.is_relative_to(self.directory):
            raise ValueError('Case package escapes library')
        if {p.relative_to(root).as_posix() for p in root.rglob('*.md')} != {'SKILL.md'}:
            raise ValueError('A case skill must contain exactly one Markdown file')
        if (root/'SKILL.md').stat().st_size > 16384:
            raise ValueError('Case MD exceeds conservative 16384-byte main-document bound')
        actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
        if actual != set(pack['sha256']):
            raise ValueError('Unregistered or missing case resources')
        for relative, digest in pack['sha256'].items():
            path = (root/relative).resolve(strict=True)
            if not path.is_relative_to(root) or sha256(path) != digest:
                raise ValueError('Case resource differs: '+relative)
        return selected

    def stage(self, task, workspace, *, owner):
        """Install before model startup; archive replaced case copies privately."""
        selected = self.verify(task)
        workspace = Path(workspace).resolve(strict=True)
        owner = Path(owner).resolve(strict=True)
        if workspace == owner or not workspace.is_relative_to(owner):
            raise ValueError('Workspace must be inside a distinct owned episode directory')
        skills = workspace/'.agents/skills'
        if not skills.resolve().is_relative_to(workspace):
            raise ValueError('Skill directory escapes workspace')
        general = skills/'robo-general/SKILL.md'
        if not general.is_file() or sha256(general) != self.index['frozen_general_sha256']:
            raise ValueError('Stage the unchanged frozen robo-general first')
        before = sha256(general)
        marker = workspace/'.current-case-skill.json'
        if marker.exists():
            prior = json.loads(marker.read_text())
            if prior.get('task') == task and prior.get('case_package_sha256') == (
                    self.index['packs'][selected['case_skill']]['sha256'] if selected['case_skill'] else None):
                actual = sorted(p.name for p in skills.iterdir() if (p/'SKILL.md').is_file())
                if actual == sorted(selected['visible_skills']):
                    return prior
        staging = owner/'.case-skill-staging'/uuid.uuid4().hex
        staging.mkdir(parents=True)
        archive = owner/'.case-skill-archive'/staging.name
        pack_id = selected['case_skill']
        new_case = None
        if pack_id:
            pack = self.index['packs'][pack_id]
            new_case = staging/task
            shutil.copytree(self.directory/pack['directory'],new_case)
            if task != pack_id:
                main = new_case/'SKILL.md'
                text, count = re.subn(r'(?m)^name: [^\n]+$', 'name: '+task,main.read_text(),count=1)
                if count != 1:
                    raise ValueError('Case skill name is missing')
                main.write_text(text)
            if (new_case/'SKILL.md').stat().st_size > 16384:
                raise ValueError('Alias MD exceeds conservative token bound')
        moved = []
        for path in sorted(skills.iterdir()):
            if path.name == 'robo-general':
                continue
            archive.mkdir(parents=True,exist_ok=True)
            path.rename(archive/path.name)
            moved.append(path.name)
        if new_case:
            new_case.rename(skills/task)
        staging.rmdir()
        if sha256(general) != before:
            raise RuntimeError('Shared skill changed during case installation')
        actual = sorted(p.name for p in skills.iterdir() if (p/'SKILL.md').is_file())
        if actual != sorted(selected['visible_skills']):
            raise RuntimeError('Unexpected skill visibility')
        receipt = {**selected,'replaced_case_copies':moved,
                   'case_package_sha256':self.index['packs'][pack_id]['sha256'] if pack_id else None,
                   'shared_general_sha256':before,'shared_instructions_changed':False,
                   'system_prompt_changed':False,'mcp_tools_changed':False,
                   'case_only':True,'source_training_material_installed':False}
        marker.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
        return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task',required=True)
    parser.add_argument('--workspace',type=Path)
    parser.add_argument('--owner',type=Path)
    args = parser.parse_args()
    library = CaseSkillLibrary()
    if args.workspace is not None:
        if args.owner is None:
            parser.error('--workspace requires --owner')
        result = library.stage(args.task,args.workspace,owner=args.owner)
    else:
        result = library.verify(args.task)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
