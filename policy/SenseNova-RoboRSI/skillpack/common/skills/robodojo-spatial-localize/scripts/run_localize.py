"""Run a packaged calibrated observation through geometry and task memory."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import uuid
from space_memory import load, import_result, save


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case',required=True,type=Path,help='Use observe.spatial_localization.case_path unchanged')
    parser.add_argument('--targets',type=Path,help='Optional multi-target annotation JSON')
    parser.add_argument('--object',dest='object_id')
    parser.add_argument('--point',default='contact')
    parser.add_argument('--description')
    parser.add_argument('--observation',action='append',default=[],metavar='CAMERA,U,V')
    parser.add_argument('--plane-z',type=float,help='Explicit known horizontal support height in environment metres')
    parser.add_argument('--height',type=float,help='Known physical point height above that plane; zero for contact')
    parser.add_argument('--memory',type=Path,default=Path('space.json'))
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    memory=load(args.memory)
    case_path=args.case.resolve(strict=True)
    case=json.loads(case_path.read_text())
    receipt=json.loads(case_path.with_suffix('.meta.json').read_text())
    latest_path=case_path.parent/'latest_spatial.json'
    latest=json.loads(latest_path.read_text())
    if latest['case_file']!=case_path.name or latest['control_step']!=receipt['control_step']:
        parser.error('This observation is stale; call observe and use its current case_path')
    if hashlib.sha256(case_path.read_bytes()).hexdigest()!=receipt['case_sha256']:
        parser.error('Observation case checksum differs; request a new observe result')
    if case.get('frame')!=memory['frame'] or case.get('units')!=memory['units']:
        parser.error('Observation and task memory frames differ')
    for name,camera in case['cameras'].items():
        image=case_path.parent/camera['image']
        if hashlib.sha256(image.read_bytes()).hexdigest()!=receipt['images'][name]:
            parser.error('Observation image checksum differs; request a new observe result')
        # Make a portable working copy without copying images or calibration math.
        camera['image']=str(image.resolve(strict=True))
    if args.targets:
        if args.object_id or args.observation:parser.error('Use --targets OR object/pixel annotations')
        targets=json.loads(args.targets.read_text())
    else:
        if not args.object_id or not args.description or not args.observation:
            parser.error('Supply --object, --description and --observation, or --targets')
        observations={}
        for item in args.observation:
            try:name,u,v=item.split(',');pixel=[float(u),float(v)]
            except ValueError:parser.error('Each --observation is CAMERA,U,V on the original image grid')
            if name not in case['cameras'] or name in observations:parser.error('Use distinct camera names from observe')
            observations[name]={'points_px':[pixel]}
        kind='support_plane' if args.plane_z is not None else 'direct'
        target=dict(id=args.point,object_id=args.object_id,description=args.description,
                    kind=kind,observations=observations)
        targets={'targets':[target]}
        if args.plane_z is None and len(observations)<2:
            parser.error('Unknown-height points need two views; known-plane points need --plane-z and --height')
        if args.plane_z is not None:
            if args.height is None:parser.error('--plane-z requires an explicit --height')
            case['support_planes']={'declared_support':dict(frame='environment_origin',normal=[0,0,1],
                offset_m=args.plane_z,height_sigma_m=0,source='Agent-declared known scene support height')}
            target.update(support_plane='declared_support',height_above_plane_m=args.height)
    root=args.output or Path('localization')/('step-'+str(receipt['control_step'])+'-'+uuid.uuid4().hex[:8])
    root=root.resolve();root.mkdir(parents=True,exist_ok=False)
    (root/'case.json').write_text(json.dumps(case,allow_nan=False))
    (root/'targets.json').write_text(json.dumps(targets,allow_nan=False))
    geometry=root/'geometry'
    child=subprocess.run([sys.executable,'-B',str(Path(__file__).with_name('localize_rgb.py')),
        '--case',str(root/'case.json'),'--targets',str(root/'targets.json'),'--output',str(geometry)],
        capture_output=True,text=True)
    if child.returncode:
        print(json.dumps({'status':'input_rejected','detail':child.stderr.strip().splitlines()[-1][:400] if child.stderr.strip() else 'Geometry process failed',
            'next_action':'Check target annotations or obtain another calibrated observation; do not debug helper sources.'}))
        return 2
    result_path=geometry/'points3d.json';result=json.loads(result_path.read_text())
    if json.loads(latest_path.read_text())!=latest:
        print(json.dumps({'status':'stale_observation','next_action':'The robot moved during localization; observe again before updating spatial memory.'}))
        return 2
    # Reload after geometry so unrelated current memory edits are preserved.
    memory=load(args.memory)
    import_result(memory,result,evidence=str(result_path),step=receipt['control_step'])
    save(args.memory,memory)
    print(json.dumps({'status':'localized' if any(r['estimate'].get('accepted') for r in result['points']) else 'rejected',
        'control_step':receipt['control_step'],'memory':str(args.memory),'result':str(result_path),
        'points':[{'object_id':r['object_id'],'point':r['id'],'accepted':r['estimate'].get('accepted'),
                   'position_m':r['estimate'].get('position_base_m'),'reason':r['estimate'].get('reason')}
                  for r in result['points']]},allow_nan=False))
    return 0


if __name__=='__main__':
    try:
        code=main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({'status':'host_input_error','detail':str(error)[:400],
            'next_action':'Use the current observe case_path and valid target annotations; report missing host inputs without inspecting helper sources.'}))
        code=2
    raise SystemExit(code)
