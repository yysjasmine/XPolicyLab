"""Lossless public tool/message transport adapter for the frozen CCI models.

Codex namespace/custom tools become the training schema functions.exec({_raw}).
No robot action is executed here. Both upstream wire and CLI response are saved.
"""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import threading
import time
import urllib.request
import urllib.error


def declarations(items, prefix=''):
    result=[]
    for item in items:
        kind=item['type']
        if kind=='namespace':
            result.extend(declarations(item['tools'],prefix+item['name']+'.'))
        elif kind in ('function','custom'):
            result.append(dict(type='function',name=prefix+item['name'],
                description=item.get('description',''),parameters=item.get('parameters') if kind=='function' else
                dict(type='object',properties={'_raw':{'type':'string'}},required=['_raw'],additionalProperties=False)))
        else: raise ValueError('Unsupported tool declaration: '+kind)
    return result


def request_to_cci(request):
    raw=deepcopy(request)
    tools=declarations(raw.get('tools',[]))
    source=raw.get('input',[])
    if isinstance(source,str): source=[dict(type='message',role='user',content=[dict(type='input_text',text=source)])]
    items=[]
    system=[raw['instructions']] if raw.get('instructions') else []
    for item in source:
        kind=item.get('type')
        if kind=='additional_tools':
            tools.extend(declarations(item['tools']));continue
        if kind=='reasoning':
            # No decryption/reconstruction; private reasoning stays in raw logs.
            continue
        if item.get('role') in ('system','developer'):
            if items: raise ValueError('Unexpected mid-history system instruction')
            content=item['content']
            system.append(content if isinstance(content,str) else '\n'.join(p['text'] for p in content));continue
        if kind in ('custom_tool_call','function_call'):
            name=item['name']
            if item.get('namespace'):name=item['namespace']+'.'+name
            elif name in ('exec','wait'):name='functions.'+name
            args=json.dumps({'_raw':item['input']},ensure_ascii=False) if kind=='custom_tool_call' else item['arguments']
            item=dict(type='function_call',call_id=item['call_id'],name=name,arguments=args)
        elif kind in ('custom_tool_call_output','function_call_output'):
            item=dict(type='function_call_output',call_id=item['call_id'],output=item['output'])
        items.append(item)
    result=dict(model=raw['model'],instructions='\n\n'.join(system),input=items,
        tools=tools,tool_choice='auto',parallel_tool_calls=False,stream=False,store=False,
        reasoning=raw.get('reasoning',{'effort':'xhigh'}),
        chat_template_kwargs={'enable_thinking':True,'reasoning_effort':(raw.get('reasoning') or {}).get('effort','xhigh')},
        temperature=0)
    if raw.get('max_output_tokens') is not None:
        result['max_output_tokens']=raw['max_output_tokens']
    def image_defaults(value):
        if isinstance(value,dict):
            if value.get('type')=='input_image':value.setdefault('detail','auto')
            for child in value.values():image_defaults(child)
        elif isinstance(value,list):
            for child in value:image_defaults(child)
    image_defaults(result['input'])
    return result


def response_to_cli(response):
    result=deepcopy(response)
    out=[]
    for item in result.get('output',[]):
        if item['type']=='reasoning':
            # Preserve opaque provider fields, never manufacture a think string.
            item={k:v for k,v in item.items() if k in ('type','id','summary','encrypted_content')}
            item.setdefault('summary',[])
        elif item['type']=='function_call':
            full=item['name']
            if item.get('namespace'):full=item['namespace']+'.'+full
            elif full in ('exec','wait'):full='functions.'+full
            ns,_,name=full.rpartition('.')
            if full=='functions.exec':
                try:args=json.loads(item['arguments'])
                except (ValueError,TypeError):args=None
                if isinstance(args,dict) and set(args)=={'_raw'} and isinstance(args['_raw'],str):
                    item=dict(type='custom_tool_call',id=item['id'],call_id=item['call_id'],
                              name=name,namespace=ns,input=args['_raw'],status='completed')
                else:
                    # Keep the wrong function payload intact. The native CLI
                    # returns its tool-validation error so the model can fix
                    # its own call; never execute a guessed shell command.
                    item.update(name=name,namespace=ns)
            elif full=='functions.wait':
                item.update(name=name,namespace=ns)
            # An unknown call likewise remains an unknown call for the CLI;
            # model mistakes are not synthetic HTTP/transport failures.
        out.append(item)
    result['output']=out
    return result


def sse(response):
    events=[]
    def emit(kind,**kw):events.append(dict(type=kind,sequence_number=len(events),**kw))
    initial={**response,'status':'in_progress','output':[],'usage':None}
    emit('response.created',response=initial)
    emit('response.in_progress',response=initial)
    for i,item in enumerate(response.get('output',[])):
        kind=item['type'];start=deepcopy(item)
        if kind in ('function_call','custom_tool_call'):start['arguments' if kind=='function_call' else 'input']=''
        elif kind=='message':start['content']=[]
        emit('response.output_item.added',output_index=i,item=start)
        if kind in ('function_call','custom_tool_call'):
            field='arguments' if kind=='function_call' else 'input'
            emit('response.'+kind+('_arguments' if kind=='function_call' else '_input')+'.delta',
                 output_index=i,item_id=item['id'],delta=item[field])
            emit('response.'+kind+('_arguments' if kind=='function_call' else '_input')+'.done',
                 output_index=i,item_id=item['id'],**{field:item[field]})
        elif kind=='message':
            for ci,part in enumerate(item.get('content',[])):
                emit('response.content_part.added',output_index=i,item_id=item['id'],content_index=ci,
                     part={**part,'text':''} if part.get('type')=='output_text' else part)
                if part.get('type')=='output_text':
                    emit('response.output_text.delta',output_index=i,item_id=item['id'],content_index=ci,delta=part['text'])
                    emit('response.output_text.done',output_index=i,item_id=item['id'],content_index=ci,text=part['text'])
                emit('response.content_part.done',output_index=i,item_id=item['id'],content_index=ci,part=part)
        emit('response.output_item.done',output_index=i,item=item)
    emit('response.'+response.get('status','completed'),response=response)
    return ''.join('event: '+e['type']+'\ndata: '+json.dumps(e,ensure_ascii=False)+'\n\n' for e in events).encode()


class CciAdapter:
    def __init__(self,endpoint,model,directory,request_timeout=1200):
        self.endpoint=endpoint.rstrip('/')+'/responses';self.model=model
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
        self.counter=0;self.lock=threading.Lock();self.request_timeout=request_timeout
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                with owner.lock:
                    owner.counter+=1;n=owner.counter
                prefix=owner.directory/f'{n:06d}'
                request=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                prefix.with_suffix('.cli-request.json').write_text(json.dumps(request,ensure_ascii=False))
                try:
                    assert self.path=='/v1/responses' and request['model']==owner.model
                    payload=request_to_cci(request)
                    prefix.with_suffix('.request.json').write_text(json.dumps(payload,ensure_ascii=False))
                    req=urllib.request.Request(owner.endpoint,data=json.dumps(payload,ensure_ascii=False).encode(),headers={'Content-Type':'application/json'})
                    try:
                        with urllib.request.urlopen(req,timeout=owner.request_timeout) as r:status=r.status;body=r.read()
                    except urllib.error.HTTPError as error:status=error.code;body=error.read()
                    prefix.with_suffix('.response.json').write_bytes(body)
                    prefix.with_suffix('.receipt.json').write_text(json.dumps(dict(at=time.time(),status=status,endpoint=owner.endpoint,model=owner.model)))
                    content_type='application/json'
                    if 200<=status<300:
                        response=response_to_cli(json.loads(body))
                        body=sse(response) if request.get('stream') else json.dumps(response,ensure_ascii=False).encode()
                        if request.get('stream'):content_type='text/event-stream'
                except Exception as error:
                    status=502;content_type='application/json'
                    body=json.dumps({'error':{'type':'cci_adapter_error','message':str(error)}}).encode()
                    prefix.with_suffix('.adapter_error.json').write_bytes(body)
                self.send_response(status);self.send_header('Content-Type',content_type)
                self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.base_url=f'http://127.0.0.1:{self.server.server_port}/v1'
    def start(self):threading.Thread(target=self.server.serve_forever,daemon=True).start()
    def stop(self):self.server.shutdown();self.server.server_close()
