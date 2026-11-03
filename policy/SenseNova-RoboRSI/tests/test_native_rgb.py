"""Lossless native IPC and JPEG archives; no simulator/model/GPU calls."""
from copy import deepcopy
import hashlib
from importlib import import_module, util
import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

P = Path(__file__).resolve().parents[1]
rgb = import_module("XPolicyLab.policy.SenseNova-RoboRSI.native_rgb")


def vision(step=0):
    # Nonuniform, strided, read-only RGB exposes swaps, JPEG loss and aliasing.
    base = np.arange(32*96*3, dtype=np.uint16).reshape(32,96,3)
    result = {}
    for i, camera in enumerate(rgb.CAMERAS):
        values = ((base+step*17+i*29) % 256).astype(np.uint8)[:,::2,:]
        values.flags.writeable = False
        result[camera] = {"color": values}
    return result


@pytest.fixture
def native_adapter(reference, monkeypatch):
    monkeypatch.setitem(sys.modules, "native_rgb", rgb)
    spec = util.spec_from_file_location("_native_rgb_adapter_test", P/"native_rgb_adapter.py")
    module = util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def adapter(module, tmp_path, step=0):
    value = module.Adapter.__new__(module.Adapter)
    value.frame = step
    value.record_dir = tmp_path/"records";value.record_dir.mkdir()
    value.observation_dir = tmp_path/"observations";value.observation_dir.mkdir()
    raw = dict(vision=vision(step), state={"joint": np.arange(6,dtype=np.float32)},
               instruction="Synthetic alignment.", additional_info={"frequency":25})
    value.env = SimpleNamespace(get_obs=lambda: raw, take_action_cnt=[step], end_flag=[False])
    return value, raw


def test_raw_roundtrip_is_exact_immutable_and_not_an_image_codec():
    source = vision();decoded = rgb.decode_rgb(json.loads(json.dumps(rgb.encode_rgb(source))))
    for camera in rgb.CAMERAS:
        a,b = source[camera]["color"],decoded[camera]["color"]
        assert a.tobytes()==b.tobytes() and a.shape==b.shape and b.dtype==np.uint8
        assert not b.flags.writeable and not np.shares_memory(a,b)


def test_native_archive_is_jpeg_but_model_input_never_decodes_it(native_adapter,tmp_path,monkeypatch):
    value,raw = adapter(native_adapter,tmp_path)
    transition = json.loads(json.dumps(value._transition()))
    underlying = SimpleNamespace(request=lambda **kw: deepcopy(transition), close=lambda:None)
    consumer = rgb.NativeRgbBackend(underlying)
    for path in value.observation_dir.glob('*.jpg'):
        # Deliberately poison archival bytes: live RGB must remain untouched.
        Image.new('RGB',(48,32),color=(0,0,0)).save(path,format='JPEG')
    def no_decode(*args,**kwargs):raise AssertionError('Live RGB must not decode an archive')
    monkeypatch.setattr(Image,'open',no_decode)
    public = consumer.request(op='reset');obs = consumer.model_observation(public)
    assert '_native_rgb' not in json.dumps(public)
    assert not list(tmp_path.rglob('*.png'))
    for camera in rgb.CAMERAS:
        assert obs['vision'][camera]['color'].tobytes()==raw['vision'][camera]['color'].tobytes()
    record=json.loads((value.record_dir/'rgb_frames.jsonl').read_text())
    assert 'data' not in record and 'transport' in record


def test_jpeg_archive_sha_survives_raw_file_copy(native_adapter,tmp_path):
    value,raw = adapter(native_adapter,tmp_path)
    transition=value._transition();target=tmp_path/'transfer';target.mkdir()
    for camera,item in transition['native_rgb']['archive'].items():
        src=value.observation_dir/item['file'];dst=target/item['file'];shutil.copyfile(src,dst)
        assert src.read_bytes()==dst.read_bytes()
        assert hashlib.sha256(dst.read_bytes()).hexdigest()==item['sha256']
        with Image.open(dst) as image:
            assert image.format=='JPEG' and image.mode=='RGB' and image.size==(48,32)
    assert rgb.verify_archive(value.record_dir/'rgb_frames.jsonl',target)['files']==3
    next(target.glob('*.jpg')).write_bytes(b'Corrupt transfer fixture')
    with pytest.raises(ValueError,match='original bytes'):
        rgb.verify_archive(value.record_dir/'rgb_frames.jsonl',target)
    assert value.env.take_action_cnt==[0] and not list(tmp_path.rglob('*.png'))


@pytest.mark.parametrize('damage',['missing','hash','shape','bytes','stale'])
def test_bad_transport_fails_closed_without_reading_jpeg(native_adapter,tmp_path,damage):
    value,raw=adapter(native_adapter,tmp_path);wire=value._transition()
    consumer=rgb.NativeRgbBackend(SimpleNamespace(request=lambda **kw:deepcopy(wire),close=lambda:None))
    first=consumer.request(op='reset')
    wire['native_rgb']['frame']=1;wire['native_rgb']['control_step']=1;wire['observation']['control_step']=1
    if damage=='missing':del wire['_native_rgb']
    elif damage=='hash':wire['_native_rgb']['cam_head']['sha256']='0'*64
    elif damage=='shape':wire['_native_rgb']['cam_head']['shape'][2]=4
    elif damage=='bytes':wire['_native_rgb']['cam_head']['data']='AAAA'
    elif damage=='stale':wire['native_rgb']['frame']=0
    with pytest.raises(ValueError):consumer.request(op='step',action={})
    with pytest.raises(ValueError,match='current'):consumer.model_observation(first)


def test_current_frame_only_and_terminal_score_video_use_original_code(native_adapter,tmp_path):
    value,raw=adapter(native_adapter,tmp_path)
    assert native_adapter.Adapter._native_result is native_adapter.module.Adapter._native_result
    assert native_adapter.Adapter._step_joint is native_adapter.module.Adapter._step_joint
    wire=value._transition()
    consumer=rgb.NativeRgbBackend(SimpleNamespace(request=lambda **kw:deepcopy(wire),close=lambda:None))
    first=consumer.request(op='reset')
    value.env.take_action_cnt[0]=1;wire=value._transition()
    second=consumer.request(op='step',action={})
    with pytest.raises(ValueError,match='current'):consumer.model_observation(first)
    assert consumer.model_observation(second)['additional_info']['frequency']==25


@pytest.mark.parametrize('success,expected_score',[(True,1.0),(False,.375)])
def test_original_native_finalizer_records_video_once(native_adapter,tmp_path,success,expected_score):
    value,raw=adapter(native_adapter,tmp_path)
    calls=[]
    value.finalized=False
    value.env.end_flag=[True];value.env.success=[success];value.env.env_seeds=[0]
    value.env.get_score=lambda:None
    value.env.reward_manager=SimpleNamespace(get_score=lambda:[37.5])
    value.env.save_video=lambda *args:calls.append(args)
    first=value._transition();second=value._transition()
    assert first['metrics']==second['metrics']==dict(success=success,score=expected_score,layout_id=0)
    assert calls==[(0,str(value.record_dir/'episode_0000000.mp4'),'success' if success else 'fail')]
    assert not list(tmp_path.rglob('*.png'))


@pytest.mark.parametrize('height,width',[(9,12),(480,640)])
def test_raw_rgb_crosses_real_private_worker_pipe(reference,tmp_path,height,width):
    # The production JSON-lines backend/worker runs in a separate interpreter.
    # Only the physics environment is a synthetic fixture.
    fake=tmp_path/'fake_native.py'
    fake.write_text('''
from pathlib import Path
import sys
sys.path.insert(0, %r)
from native_rgb_adapter import Adapter as RealAdapter
import numpy as np
from types import SimpleNamespace
class Adapter(RealAdapter):
 def __init__(self,root,options):
  self.frame=0;self.closed=False
  self.env=SimpleNamespace(take_action_cnt=[0],end_flag=[False])
 def raw(self):
  step=self.env.take_action_cnt[0]
  a=(np.arange(%d*%d*3).reshape(%d,%d,3)+step).astype(np.uint8)
  return dict(vision={k:dict(color=a) for k in ('cam_head','cam_left_wrist','cam_right_wrist')},state={'joints':[step]},instruction='Synthetic native IPC.',additional_info={'frequency':25})
 def reset(self,episode,record_dir,observation_dir):
  self.record_dir=Path(record_dir);self.record_dir.mkdir()
  self.observation_dir=Path(observation_dir);self.observation_dir.mkdir()
  self.env.get_obs=self.raw
  return self._transition()
 def step(self,action):
  self.env.take_action_cnt[0]+=1
  return self._transition(applied_action={'joint_command':action})
 def finish(self):
  self.env.end_flag[0]=True
  return self._transition()
 def _native_result(self):return dict(success=True,score=1.0)
 def close(self):self.closed=True
''' % (str(P),height,width,height,width))
    cfg=dict(request_seconds=10,benchmark_python=sys.executable,environment_dir=str(tmp_path),options={},adapter=str(fake)+':Adapter')
    (tmp_path/'config.json').write_text(json.dumps(cfg))
    backend_module=import_module('policylab.backend')
    consumer=rgb.NativeRgbBackend(backend_module.Backend(cfg,tmp_path))
    try:
        assert consumer.request(op='initialize')=={'initialized':True}
        reply=consumer.request(op='reset',episode={},record_dir=str(tmp_path/'records'),observation_dir=str(tmp_path/'images'))
        for step in range(3):
            if step:reply=consumer.request(op='step',action={'step':step})
            obs=consumer.model_observation(reply)
            expected=(np.arange(height*width*3).reshape(height,width,3)+step).astype(np.uint8)
            for camera in rgb.CAMERAS:np.testing.assert_array_equal(obs['vision'][camera]['color'],expected)
            assert '_native_rgb' not in json.dumps(reply)
        final=consumer.request(op='finish')
        assert final['done'] and final['metrics']==dict(success=True,score=1.0)
        assert not list(tmp_path.rglob('*.png'))
    finally:consumer.close()


def test_factory_changes_only_adapter_and_preserves_native_options(tmp_path):
    cfg=dict(adapter=str(P/'vendor/robodojo_adaptive/source/code/configurations/robodojo/20261005_standard_1x_10x/adapters/adapter.py')+':Adapter',
             benchmark_python=sys.executable,environment_dir='/existing/native',request_seconds=180,
             options=dict(action_mode='joint',gpu=0,review_window_steps=1050,max_repeat=1))
    before=deepcopy(cfg);seen=[]
    def factory(config,run):
        seen.append(deepcopy(config))
        return SimpleNamespace(request=lambda **kw:{'initialized':True},close=lambda:None)
    backend=rgb.create_backend(cfg,tmp_path,backend_factory=factory)
    assert backend.request(op='initialize')=={'initialized':True}
    assert cfg==before and seen[0]['options']==cfg['options']
    assert seen[0]['adapter']==str(P/'native_rgb_adapter.py')+':Adapter'
    assert json.loads((tmp_path/'config.json').read_text())==seen[0]
    bad=deepcopy(cfg);bad['adapter']='/custom/trial_adapter.py:Adapter'
    with pytest.raises(ValueError,match='original native'):
        rgb.create_backend(bad,tmp_path/'bad',backend_factory=factory)
    assert len(seen)==1


def test_input_pixels_reach_pinned_vla_encoder_unchanged(reference,experiment,backend,vla):
    fixtures=import_module('XPolicyLab.policy.SenseNova-RoboRSI.tests.test_hybrid_backend')
    raw=fixtures.observation();raw['vision']=rgb.decode_rgb(rgb.encode_rgb(vision()))
    module=import_module('XPolicyLab.policy.SenseNova-RoboRSI.hybrid_backend')
    backend._latest=module._observation(raw)
    fixtures.propose(experiment)
    for source,dest in [('cam_head','cam_high'),('cam_left_wrist','cam_left_wrist'),('cam_right_wrist','cam_right_wrist')]:
        np.testing.assert_array_equal(vla.inputs[0]['images'][dest],raw['vision'][source]['color'].transpose(2,0,1))
