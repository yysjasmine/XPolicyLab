import json,math,tempfile,types,sys,unittest
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation
import bounded_curobo as b
# The interface-only fake excludes the actual simulator and any model call.
api=types.ModuleType('policylab.api');api.InvalidAction=type('InvalidAction',(Exception,),{});sys.modules['policylab.api']=api
import curobo_execution as native_ee
class Array(np.ndarray):
 def cpu(self):return self
 def numpy(self):return np.asarray(self)
class FK:
 names=['j'+str(i)for i in range(6)]
 chain=[(n,'prismatic'if i<3 else'revolute',None,None)for i,n in enumerate(names)]
 def matrix(self,q):
  out=np.eye(4);out[:3,3]=q[:3];out[:3,:3]=Rotation.from_rotvec(q[3:]).as_matrix();return out
class Tests(unittest.TestCase):
 def test_old_vector_caps_and_shortest_rotation(self):
  now=np.eye(4);target=np.r_[[.12,.16,0],Rotation.from_rotvec([0,0,1.2]).as_quat()[[3,0,1,2]]];pose,delta=b.bounded_goal(now,target)
  self.assertAlmostEqual(np.linalg.norm(delta[:3]),.02);self.assertAlmostEqual(np.linalg.norm(delta[3:]),.1);self.assertAlmostEqual(np.linalg.norm(pose[:3]),.02)
  pose2,delta2=b.bounded_goal(now,np.r_[[0,0,0],-target[3:]]);self.assertTrue(np.allclose(delta2[3:],delta[3:]))
 def test_periodic_nearest_soft_limit_and_no_fake_prismatic_wrap(self):
  q=np.array([2.394861,2.358188,0,0,0,-3.071]);raw=q+np.array([-2*math.pi,2*math.pi,0,0,2*math.pi,0]);limits=np.tile([-math.pi,math.pi],(6,1));canonical,k=b.canonical_solution(raw,q,limits,[True]*6);self.assertTrue(np.allclose(canonical,q));self.assertEqual(k,[1,-1,0,0,-1,0]);bad,_=b.canonical_solution(raw,q,limits,[False]*6);self.assertIsNone(bad)
 def test_original_joint_bounds_ema_and_bad_state(self):
  lim=np.tile([-1.,1.],(6,1));q=np.zeros(6);out,info=b.smooth_bounded_joints(np.ones(6)*.8,q,lim,previous=q);self.assertTrue(np.allclose(out,.025));self.assertEqual(info['alpha'],.5)
  out2,_=b.smooth_bounded_joints(-np.ones(6)*.8,q,lim,previous=out);self.assertTrue(np.allclose(out2,-.0125));self.assertLessEqual(np.abs(out2-q).max(),.050001)
  with self.assertRaises(ValueError):b.bounded_joints(q,np.ones(6)*2,lim)
 def make_adapter(self,folder,fail=None):
  state={'left':np.zeros(6),'right':np.zeros(6)};calls=[];steps=[];robots={a:types.SimpleNamespace(arm_name=a+'_arm',robot_name=a,ee_link_name='link6',arm_joint_indices=list(range(6)),arm_joints_name=FK.names)for a in state};keys=[types.SimpleNamespace(data=types.SimpleNamespace(soft_joint_pos_limits=np.tile([-1.,1.],(1,6,1)).view(Array)))for a in state];manager=types.SimpleNamespace(robot_list=list(robots.values()),robot_key=keys,control_manager=types.SimpleNamespace(prev_control=[{a+'_arm_joint_state':{'position':[0.]*6}for a in state}]),ik_solver={a:types.SimpleNamespace(ee_link='link6')for a in state});manager.get_joint=lambda robot,ids:[state[robot.robot_name].copy()];manager.process_name=lambda name:name+'_joint_state'
  def solve(target_pose,env_idx,robot,trans):
   calls.append((robot.robot_name,list(target_pose)));return {'status':'Fail'}if robot.robot_name==fail else {'status':'Success','joint_names':FK.names,'joint_value':[*target_pose[:3],0,0,0]}
  manager.solve_ik=solve;kin=types.SimpleNamespace(robots=robots,manager=manager,fk={a:FK()for a in state},root=lambda a:np.eye(4),check=lambda:{'passed':True})
  def observations():return {a+'_arm_joint_state':state[a].tolist()for a in state}|{a+'_ee_pose':[*state[a][:3],1.,0.,0.,0.]for a in state}
  env=types.SimpleNamespace(robot_manager=manager,take_action_cnt=[0],sim=types.SimpleNamespace(_sim_step_counter=0),get_obs=lambda:{'state':observations()});adapter=types.SimpleNamespace(env=env,kinematics=kin,record_dir=Path(folder))
  def step(command):
   steps.append(command)
   for a in state:state[a]=np.array(command[a+'_arm_joint_state'])
   env.take_action_cnt[0]+=1;env.sim._sim_step_counter+=10;return {'observation':{'state':observations()},'done':False}
  adapter._step_joint=step;return adapter,calls,steps
 def test_exact_harness_execution_no_second_ik_and_gripper_not_smoothed(self):
  with tempfile.TemporaryDirectory()as folder:
   adapter,calls,steps=self.make_adapter(folder);original_solve=adapter.env.robot_manager.solve_ik;action={'mode':'eef','reason':'test','assessment':{'current_subgoal':'test'},'target':{a:{'position':[.04,0,0],'quaternion_wxyz':[1.,0,0,0],'gripper_opening':.2 if a=='left'else.8}for a in ['left','right']}};result=native_ee.execute(adapter,action)
   self.assertEqual(len(calls),2);self.assertEqual(len(steps),1);self.assertIs(adapter.env.robot_manager.solve_ik,original_solve);self.assertEqual(adapter.env.sim._sim_step_counter,10)
   self.assertTrue(np.allclose(calls[0][1][:3],[.02,0,0]));self.assertAlmostEqual(steps[0]['left_arm_joint_state'][0],.01);self.assertEqual(steps[0]['left_ee_joint_state'],[.2]);self.assertEqual(steps[0]['right_ee_joint_state'],[.8]);self.assertGreater(result['applied_action']['ik_diagnostics']['left']['actual_residual_to_requested_goal']['tcp_position_error_m'],.02)
   receipt=json.loads((Path(folder)/'bounded_curobo_execution.jsonl').read_text());self.assertEqual(receipt['status'],'completed');self.assertEqual(receipt['physics_steps'],10)
 def test_solver_failure_keeps_bounded_previous_and_executes_other_arm(self):
  with tempfile.TemporaryDirectory()as folder:
   adapter,calls,steps=self.make_adapter(folder,fail='left');action={'mode':'eef','reason':'test','assessment':{'current_subgoal':'test'},'target':{a:{'position':[.04,0,0],'quaternion_wxyz':[1.,0,0,0],'gripper_opening':.3}for a in ['left','right']}};result=native_ee.execute(adapter,action);self.assertEqual(steps[0]['left_arm_joint_state'],[0.]*6);self.assertGreater(steps[0]['right_arm_joint_state'][0],0);self.assertEqual(result['applied_action']['ik_fallback_arms'],['left'])
 def test_error_after_physics_is_not_repairable_invalid_action(self):
  with tempfile.TemporaryDirectory()as folder:
   adapter,calls,steps=self.make_adapter(folder);original=adapter._step_joint
   def broken(command):
    original(command);raise api.InvalidAction('synthetic post-physics error')
   adapter._step_joint=broken;action={'mode':'eef','reason':'test','assessment':{'current_subgoal':'test'},'target':{a:{'position':[.04,0,0],'quaternion_wxyz':[1.,0,0,0],'gripper_opening':.3}for a in ['left','right']}}
   with self.assertRaisesRegex(RuntimeError,'do not resend'):native_ee.execute(adapter,action)
   receipt=json.loads((Path(folder)/'bounded_curobo_execution.jsonl').read_text());self.assertEqual(receipt['status'],'fault');self.assertEqual(receipt['control_step_after'],1);self.assertEqual(receipt['physics_steps'],10);self.assertEqual(len(steps),1)
 def test_unchanged_arm_skips_solver_without_delaying_gripper(self):
  with tempfile.TemporaryDirectory()as folder:
   adapter,calls,steps=self.make_adapter(folder);action={'mode':'eef','reason':'test','assessment':{'current_subgoal':'test'},'target':{a:{'position':[.04 if a=='left' else 0,0,0],'quaternion_wxyz':[1.,0,0,0],'gripper_opening':.2}for a in ['left','right']}};result=native_ee.execute(adapter,action);self.assertEqual(len(calls),1);self.assertEqual(calls[0][0],'left');self.assertEqual(steps[0]['right_arm_joint_state'],[0.]*6);self.assertEqual(steps[0]['right_ee_joint_state'],[.2]);info=result['applied_action']['ik_diagnostics']['right'];self.assertFalse(info['solver_invoked']);self.assertEqual(info['solver_status'],'not_called_zero_error');self.assertEqual(info['status'],'Hold')
 def test_accepted_vla_joint_is_exact_and_never_calls_curobo_or_smoothing(self):
  import execution
  with tempfile.TemporaryDirectory()as folder:
   original,calls,steps=self.make_adapter(folder)
   cls=execution.adapter_class(type('UnusedBase',(),{}));adapter=cls.__new__(cls);adapter.__dict__.update(original.__dict__);adapter._eef_execution_backend='bounded_curobo'
   values=np.array([.02,0,0,.2,0,0,.25, .01,0,0,0,.2,0,.75])
   adapter._joint_proposal={'id':'stored','cursor':0,'control_step':0,'actions':[values.tolist()]}
   def forbidden(*a,**kw):raise AssertionError('VLA accept entered cuRobo or smoothing')
   adapter.env.robot_manager.solve_ik=forbidden
   saved=native_ee.execute;native_ee.execute=forbidden
   try:result=adapter.step({'mode':'stored_vla_joint','proposal_id':'stored','index':0})
   finally:native_ee.execute=saved
   self.assertFalse(result['applied_action']['ik_used']);self.assertEqual(len(steps),1);self.assertFalse(calls)
   self.assertEqual(steps[0]['left_arm_joint_state'],values[:6].tolist());self.assertEqual(steps[0]['right_arm_joint_state'],values[7:13].tolist())
   self.assertEqual(steps[0]['left_ee_joint_state'],[.25]);self.assertEqual(steps[0]['right_ee_joint_state'],[.75]);self.assertAlmostEqual(steps[0]['left_arm_joint_state'][3],.2)
   self.assertFalse((Path(folder)/'bounded_curobo_execution.jsonl').exists())

if __name__=='__main__':unittest.main()
