"""Load the current three-tool profile for a normal task episode."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path


class RobotPromptProfile:
    def __init__(self,directory):
        self.directory=Path(directory).resolve(strict=True)
        self.manifest=json.loads((self.directory/'profile.json').read_text())
        self.task=self.manifest['task'];self.profile_id=self.manifest['profile_id']
        self.core=(self.directory/'agent_core.md').read_text().strip()
        self.tools=json.loads((self.directory/'native_tools.json').read_text())
        self.deferred=json.loads((self.directory/'actual_robot_declarations.json').read_text())
        self.expected_tools=set(self.manifest['robot_tool_registration']['tools'])
        if self.expected_tools!={'observe','vla_propose','act'}:
            raise ValueError('Expected the three-tool robot profile')
        if {t['name'] for t in self.deferred}!={'mcp__robot__'+n for n in self.expected_tools}:
            raise ValueError('Pinned real MCP runtime declarations differ')
        # Native direct MCP calls render text and images without JS forwarding.
        # Generic code execution retains the pinned exec/wait declarations.
        self.skills_instruction=(self.directory/'skills_instruction.md').read_text().strip()
        for relative,digest in self.manifest['sha256'].items():
            p=(self.directory/relative).resolve(strict=True)
            if not p.is_relative_to(self.directory) or hashlib.sha256(p.read_bytes()).hexdigest()!=digest:
                raise ValueError('Skillpack asset differs: '+relative)
        for skill in self.manifest.get('single_markdown_skills',[]):
            root=self.directory/'skills'/skill
            if {p.relative_to(root).as_posix() for p in root.rglob('*.md')}!={'SKILL.md'}:
                raise ValueError('Skill must have one main Markdown file: '+skill)

    def prepare_workspace(self,request,*,task):
        if task!=self.task:raise ValueError('Wrong case skill')
        cfg_path=Path(request['config']);cfg=json.loads(cfg_path.read_text())
        # Extend this new case's owned runtime copy. Frozen publisher files and
        # completed captures remain untouched; the worker loads this exact path.
        source=Path(request['source'])
        here=Path(__file__).resolve().parent
        runtime=here/'robot-runtime' if (here/'robot-runtime').is_dir() else here
        import importlib.util
        import sys
        sys.path.insert(0,str(runtime))
        from execution import stage_owned_adapter
        stage_owned_adapter(source,cfg,runtime)
        cfg['options']['codex_compact_tokens']=200000
        cfg_path.write_text(json.dumps(cfg,indent=2)+'\n')
        overrides=request['cli_configuration']['config_overrides']
        overrides.update(model_instructions_file='/workspace/preparation/agent_system.md',
                         model_auto_compact_token_limit=200000,model_context_window=200000)
        request['prompt_profile']={'id':self.profile_id,'task':task,'directory':str(self.directory),
            'sha256':self.manifest['sha256'],'registered_skills':self.manifest['skills'],
            'model_tools':['functions.exec','functions.wait','robot.observe','robot.vla_propose','robot.act'],'stage':'before CLI input construction'}
        request.setdefault('baseline_adaptations',[]).append('two_skillpack_and_short_episode_prompt')

    def stage_live_workspace(self,workspace,*,owner):
        workspace=Path(workspace).resolve(strict=True);owner=Path(owner).resolve(strict=True)
        if not workspace.is_relative_to(owner):raise ValueError('Workspace escapes owned runtime')
        # Mutable task memory is not a pinned skill asset. Preserve it across
        # repeated staging, compaction and same-episode workspace copies.
        memory=workspace/'space.json'
        if memory.is_symlink():raise ValueError('Spatial memory must be a workspace file')
        if memory.exists():
            state=json.loads(memory.read_text())
            if (state.get('task')!=self.task or state.get('schema_version')!=1
                    or state.get('frame')!='environment_origin' or state.get('units')!='meters'
                    or not isinstance(state.get('objects'),dict)):
                raise ValueError('Spatial memory belongs to another task or schema')
        else:
            with memory.open('x') as stream:
                initial=json.loads((self.directory/'space.json').read_text())
                initial['task']=self.task
                json.dump(initial,stream,indent=2)
                stream.write('\n')
        public=workspace.parent/'tools/BENCHMARK.md'
        if public.exists():public.write_text('# Public robot interface\n\nRead the registered robo-general and current case skills.\n')
        marker=workspace/'.skillpack-staged.json'
        if marker.exists():
            receipt=json.loads(marker.read_text())
            if receipt['profile_id']!=self.profile_id:raise ValueError('Workspace has another profile')
            return receipt
        removed=[]
        for relative in self.manifest['remove_legacy_markdown']+self.manifest.get('remove_legacy_assets',[]):
            path=workspace/relative
            if not path.resolve().is_relative_to(workspace):raise ValueError('Legacy MD escapes workspace')
            if path.is_file():path.unlink();removed.append(relative)
        for skill in self.manifest.get('single_markdown_skills',[]):
            root=workspace/'.agents/skills'/skill
            if not root.resolve().is_relative_to(workspace):raise ValueError('Skill path escapes workspace')
            for path in root.rglob('*.md'):
                if path.relative_to(root).as_posix()!='SKILL.md':
                    if not path.resolve().is_relative_to(workspace):raise ValueError('Legacy MD escapes workspace')
                    path.unlink();removed.append(str(path.relative_to(workspace)))
        assets={}
        # Stage every pinned skill resource, including executable helpers and
        # JSON evidence; unregistered files and development tests stay out.
        for name in sorted(self.manifest['sha256']):
            if not name.startswith('skills/'):continue
            source=self.directory/name
            relative=Path('.agents')/source.relative_to(self.directory)
            destination=workspace/relative
            if not destination.resolve().is_relative_to(workspace):raise ValueError('Skill path escapes workspace')
            destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(source.read_bytes())
            assets[str(relative)]=hashlib.sha256(source.read_bytes()).hexdigest()
        core_path=workspace/'preparation/agent_system.md';core_path.parent.mkdir(exist_ok=True)
        core_path.write_text(self.core+'\n')
        # The old public markdown becomes an entry point, not a second manual.
        public=workspace.parent/'tools/BENCHMARK.md'
        if public.exists():public.write_text('# Public robot interface\n\nRead the registered robo-general and current case skills.\n')
        lengths={str(p.relative_to(workspace)):len(p.read_bytes()) for p in workspace.rglob('*.md') if p.is_file()}
        if any(size>16384 for size in lengths.values()):raise ValueError('Workspace MD exceeds conservative byte cap')
        receipt={'profile_id':self.profile_id,'removed_legacy_md':removed,'assets':assets,
                 'spatial_memory':'space.json',
                 'markdown_utf8_bytes':lengths,'source_preparation_changed':False}
        marker.write_text(json.dumps(receipt,indent=2)+'\n');return receipt

    def episode_prompt(self,cfg):
        """Normal task input; private operator early-stop counters stay outside it."""
        options=cfg['options']
        data={'task':self.task,'phase':'evaluation','setting':'simate_vla',
              'vla_backend':'Simate_beta','seed':0,'layout':0,'modality':'RGB-only',
              'reset_allowed':False,'direct_act_enabled':True,
              'native_step_limit':options['review_window_steps']*options['review_max_windows']}
        if cfg['episode_seconds'] is not None:data['wall_seconds_budget']=cfg['episode_seconds']
        for key in ['max_repeat','review_window_steps','review_max_windows','stop_min_steps','review_allow_reset']:
            if key in options:data[key]=options[key]
        return ('Carry out the current episode task.\nEpisode configuration:\n'
                +json.dumps(data,separators=(',',':'))+'\n')

    def transform(self,request):
        original=deepcopy(request);result=deepcopy(request);new=[];found_core=False;found_tools=False;removed=[]
        names=set()
        def robot_only(specs, path=()):
            kept=[]
            for spec in specs:
                full=path+(spec.get('name',''),)
                if isinstance(spec.get('tools'),list):
                    children=robot_only(spec['tools'],full)
                    if children:
                        value=deepcopy(spec);value['tools']=children;kept.append(value)
                else:
                    name=spec.get('name','');leaf=name.rsplit('__',1)[-1]
                    if leaf in {'observe','vla_propose','act'} and 'robot' in '.'.join(full):
                        names.add(leaf);kept.append(deepcopy(spec))
            return kept
        def merge(groups, additions):
            for addition in additions:
                match=next((g for g in groups if g.get('type')=='namespace' and addition.get('type')=='namespace' and g.get('name')==addition.get('name')),None)
                if match is None:groups.append(addition)
                else:match['tools'].extend(addition['tools'])
        if not isinstance(result.get('input'),list):raise ValueError('Explicit history required')
        tags=('<skills_instructions>','<permissions instructions>','<collaboration_mode>','<multi_agent_role>','<multi_agent_mode>')
        for i,item in enumerate(result['input']):
            if item.get('type')=='additional_tools':
                found_tools=True
                if item.get('tools'):
                    robot=robot_only(item['tools'])
                    item['tools']=deepcopy(self.tools)
                    merge(item['tools'],robot)
                new.append(item);continue
            if item.get('role') not in ['developer','system']:
                texts='\n'.join(p.get('text','') for p in item.get('content',[]) if isinstance(p,dict))
                if texts.startswith('# AGENTS.md instructions for /workspace') or texts.startswith('You are the robot policy in a PolicyLab experiment.'):
                    raise ValueError('Legacy user prompt reached CLI: staging failed')
                new.append(item);continue
            content=[]
            for j,part in enumerate(item.get('content',[])):
                text=part.get('text')
                if isinstance(text,str) and (text.lstrip().startswith(tags) or text.strip()==self.skills_instruction):
                    removed.append({'item':i,'part':j});continue
                if isinstance(text,str) and (text.strip()==self.core or text.startswith('You are Codex, an agent based on GPT-6.')):
                    if found_core:raise ValueError('Duplicate core')
                    found_core=True;part['text']=self.core
                content.append(part)
            if content:
                item['content']=content;new.append(item)
                if any(p.get('text')==self.core for p in content):
                    new.append({'type':'message','role':'developer','content':[{'type':'input_text','text':self.skills_instruction}]})
        if not found_core or not found_tools:raise ValueError('Pinned CLI prefix missing')
        if names!={'observe','vla_propose','act'}:
            raise ValueError('Actual CLI robot declarations missing: '+str(sorted(names)))
        result['input']=new
        return result,{'profile_id':self.profile_id,'removed_blocks':removed,'changed':result!=original,
                       'actual_robot_tools_preserved':sorted(names),
                       'robot_binding':'native direct MCP; automatic text and image blocks',
                       'user_history_images_and_responses_rewritten':False}
