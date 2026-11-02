"""Common measurement and paid-call boundaries for every ablation stage."""

from pathlib import Path
import asyncio
from copy import deepcopy
from .context_windows import ContextWindows
import gzip
import hashlib
import json
import threading
import time
import zlib

from roborsi.planner.provider_capture import ProviderCapture, parse_response_body, _redact


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary=path.with_suffix('.new')
    temporary.write_text(json.dumps(value,indent=2)+'\n')
    temporary.replace(path)


def provider_errors(body, content_type, response):
    """Read protocol error objects only; do not scan task or reasoning text."""
    errors=[]
    if isinstance(response,dict):
        if response.get('error'):errors.append(response['error'])
        if response.get('status') in ['failed','incomplete']:
            errors.append({'status':response['status'],
                           'details':response.get('incomplete_details')})
    if 'text/event-stream' in content_type:
        for frame in body.decode('utf-8-sig',errors='replace').replace('\r\n','\n').split('\n\n'):
            data='\n'.join(line[5:].lstrip() for line in frame.splitlines() if line.startswith('data:'))
            if not data or data=='[DONE]':continue
            try:event=json.loads(data)
            except ValueError:continue
            if isinstance(event,dict) and event.get('type')=='error':errors.append(event)
    elif response is None:
        try:event=json.loads(body)
        except ValueError:event=None
        if isinstance(event,dict) and event.get('error'):errors.append(event['error'])
    return errors


def terminal_provider_error(status, errors):
    """Separate explicit provider blocks from ordinary transient transport errors."""
    if status in [400,401,402,403]:return True
    codes=json.dumps(errors).lower()
    return any(token in codes for token in [
        'content_policy','content_filter','forbidden','insufficient_quota',
        'invalid_api_key','authentication_error','billing_hard_limit',
    ])


def transient_provider_error(status, errors):
    if terminal_provider_error(status, errors):return False
    def codes(value):
        if isinstance(value,dict):
            return [str(v).lower() for k,v in value.items() if k in ['code','type']] + [c for v in value.values() for c in codes(v)]
        if isinstance(value,list):return [c for v in value for c in codes(v)]
        return []
    return status in [429,500,502,503,504] or bool(set(codes(errors)) & {'rate_limit_exceeded','no_capacity','too_many_requests','overloaded_error','server_error'})


def output_length_limit(record, parsed, errors, usage):
    """Retry only an intact, accounted response with the explicit length reason."""
    return bool(record['status_code'] == 200
                and record.get('transport_complete')
                and parsed.get('parse_status') == 'parsed'
                and isinstance(usage, dict) and usage
                and errors == [{'status': 'incomplete',
                                'details': {'reason': 'max_output_tokens'}}])


def request_kind(request, endpoint=''):
    """Native CLI compaction may use the ordinary /responses endpoint."""
    if endpoint.rstrip('/').endswith('/responses/compact'):return 'compaction'
    metadata=request.get('client_metadata',{}) if isinstance(request,dict) else {}
    if not isinstance(metadata,dict):return 'turn'
    turn=metadata.get('x-codex-turn-metadata',{})
    if isinstance(turn,str):
        try:turn=json.loads(turn)
        except ValueError:turn={}
    if metadata.get('request_kind')=='compaction' or (
        isinstance(turn,dict) and turn.get('request_kind')=='compaction'):
        return 'compaction'
    return 'turn'


def read_usage_rows(path):
    """Classify legacy records from their immutable captured request files."""
    path=Path(path)
    rows=[json.loads(line) for line in path.read_text().splitlines() if line]
    for row in rows:
        upstream=path.parent/'cci-wire'/f'{row["request_id"]:06d}.response.json'
        if upstream.exists():
            actual=json.loads(upstream.read_text())
            if actual.get('status')=='completed' and isinstance(actual.get('usage'),dict):
                row['upstream_accounting_source']=str(upstream)
                row['original_cli_completed']=row['completed']
                row['usage']=actual['usage']
                row['completed']=True
        if 'request_kind' in row:continue
        source=path.parent/'api'/f'{row["request_id"]:06d}.request.json'
        request=json.loads(source.read_text()).get('forwarded',{}) if source.exists() else {}
        row['request_kind']=request_kind(request,row.get('endpoint',''))
    return rows


def cache_statistics(rows):
    ordinary=[r for r in rows if r.get('request_kind','turn')!='compaction']
    total=sum((r.get('usage') or {}).get('input_tokens',0) for r in ordinary)
    cached=sum(((r.get('usage') or {}).get('input_tokens_details') or {}).get('cached_tokens',0) for r in ordinary)
    return {'scope':'ordinary_requests_excluding_compaction',
            'input_tokens':total,'cached_input_tokens':cached,
            'weighted_cache_ratio':cached/total if total else None,
            'known_usage_calls':sum(r.get('usage') is not None for r in ordinary),
            'excluded_compaction_calls':sum(r.get('request_kind')=='compaction' for r in rows)}


class GuardedCapture(ProviderCapture):
    """Capture exact provider bytes, then stop unsafe or unaccounted dispatch."""

    def __init__(self, *args, case_dir, model, model_label, cohort_root, prior_rows=(), prompt_profile=None, case_deadline=None, enforce_paid_cache=True, **kwargs):
        super().__init__(*args,**kwargs)
        self.case=Path(case_dir)
        self.model=model
        self.model_label=model_label
        self.cohort=Path(cohort_root)
        self.prompt_profile=prompt_profile
        self.enforce_paid_cache=enforce_paid_cache
        self.case_deadline=case_deadline
        self.rows=[dict(row) for row in prior_rows]
        self._cache_epoch_start=len(self.rows)
        self._guard_lock=threading.RLock()
        self._attempts=len(self.rows)
        self.context=ContextWindows(Path(__file__).resolve().parent)

    def _hold(self, kind, evidence, *, cohort=False):
        value={'at':time.time(),'kind':kind,'evidence':evidence,
               'model':self.model,'case_dir':str(self.case),
               'key_or_route_fallback':False}
        write(self.case/'dispatch_hold.json',value)
        if cohort:write(self.cohort/'provider_blocks'/f'{self.model_label}.json',value)

    async def _buffered_error(self, kind, evidence):
        self._hold(kind, evidence)
        messages=[]
        async def collect(message):messages.append(message)
        await self._error(collect,503,b'Provider dispatch stopped; see retained retry and budget evidence')
        return messages

    async def _buffered_forward(self,scope,payload):
        # No tool output reaches CLI until the provider attempt has terminated.
        # Thus a bounded model-request retry cannot replay a robot action.
        length_retries=0
        transient_retries=0
        first_request_id=None
        while True:
            if self._attempts>=330 or sum(r['completed'] for r in self.rows)>=300:
                return await self._buffered_error('model_call_budget',
                    {'attempts':self._attempts,'first_request_id':first_request_id})
            remaining=None if self.case_deadline is None else self.case_deadline-time.time()
            if remaining is not None and remaining<=0:
                return await self._buffered_error('case_wall_budget',
                    {'deadline':self.case_deadline,'attempts':self._attempts})
            messages=[]
            async def collect(message):messages.append(message)
            self._attempts+=1
            before=len(self.rows)
            forward=super()._forward(scope,payload,collect)
            try:
                if remaining is None:await forward
                else:await asyncio.wait_for(forward,timeout=remaining)
            except asyncio.TimeoutError:
                return await self._buffered_error('case_wall_budget',
                    {'deadline':self.case_deadline,'attempts':self._attempts})
            # ProviderCapture preserves the cancelled response in its finally block.
            # It may consume cancellation, so also check the absolute deadline here.
            if self.case_deadline is not None and time.time()>=self.case_deadline:
                return await self._buffered_error('case_wall_budget',
                    {'deadline':self.case_deadline,'attempts':self._attempts})
            if len(self.rows)!=before+1:
                return await self._buffered_error('missing_attempt_accounting',
                    {'attempts':self._attempts,'rows_before':before,'rows_after':len(self.rows)})
            row=self.rows[-1] if self.rows else {}
            if first_request_id is None:first_request_id=row['request_id']
            if row.get('output_length_limit'):
                if length_retries>=1:
                    return await self._buffered_error('output_length_retry_exhausted',
                        {**row,'max_retries':1,'first_request_id':first_request_id,
                         'last_request_id':row['request_id'],'attempts':self._attempts})
                if self._attempts>=330:
                    return await self._buffered_error('model_call_budget',
                        {'attempts':self._attempts,'last_request_id':row['request_id']})
                length_retries+=1
                write(self.case/'model-retries'/f'{row["request_id"]:06d}-output-length.json',
                      {'at':time.time(),'reason':'max_output_tokens','retry_number':length_retries,
                       'max_retries':1,'first_request_id':first_request_id,
                       'failed_request_id':row['request_id'],'model':self.model,
                       'request_payload_sha256':hashlib.sha256(payload).hexdigest(),
                       'same_request':True,'failed_output_forwarded':False,
                       'budget_reset':False,'provider_attempts_consumed':self._attempts,
                       'case_deadline':self.case_deadline})
                continue
            if not row.get('transient_capacity_or_server_error'):return messages
            if transient_retries>=2:
                self._hold('transient_retry_exhausted',{'attempts':3,'last_request_id':row.get('request_id')})
                return messages
            delay=[5,15][transient_retries]
            transient_retries+=1
            if self.case_deadline is not None and time.time()+delay>=self.case_deadline:
                return await self._buffered_error('case_wall_budget',
                    {'deadline':self.case_deadline,'attempts':self._attempts})
            await asyncio.sleep(delay)

    async def _compact_window(self, scope, request, history, reasons):
        compact=deepcopy(request)
        compact['input']=deepcopy(history)
        for item in compact['input']:
            if item.get('type')=='additional_tools':item['tools']=[]
        compact['input'].append({'type':'message','role':'user','content':[{'type':'input_text','text':self.context.prompt}]})
        compact['stream']=False
        compact['tool_choice']='auto'
        metadata=compact.setdefault('client_metadata',{})
        turn=metadata.get('x-codex-turn-metadata',{})
        if isinstance(turn,str):turn=json.loads(turn)
        turn['request_kind']='compaction'
        metadata['x-codex-turn-metadata']=json.dumps(turn,separators=(',',':'))
        chunks=[];status=0;content_type='application/json';encoding=''
        async def collect(message):
            nonlocal status,content_type,encoding
            if message['type']=='http.response.start':
                status=message['status']
                for key,value in message.get('headers',[]):
                    if key.lower()==b'content-type':content_type=value.decode()
                    if key.lower()==b'content-encoding':encoding=value.decode()
            elif message['type']=='http.response.body':chunks.append(message.get('body',b''))
        write(self.case/'context-windows'/f'compact-{self.context.epoch+1:04d}.json',
              {'at':time.time(),'reasons':reasons,'summary_source':'same model, pinned Codex default compact prompt',
               'extra_current_state':False,'raw_request_id':self.summary['requests']+1})
        buffered=await self._buffered_forward(scope,json.dumps(compact,ensure_ascii=False,separators=(',',':')).encode())
        for message in buffered:await collect(message)
        compact_body=b''.join(chunks)
        if encoding=='gzip':compact_body=gzip.decompress(compact_body)
        elif encoding=='deflate':compact_body=zlib.decompress(compact_body)
        parsed=parse_response_body(compact_body,content_type,endpoint='/responses',include_response=True)
        response=parsed.get('response') or {}
        if status!=200 or not parsed.get('response_completed') or response.get('error'):
            raise RuntimeError('Default text compaction failed; raw provider evidence retained')
        text=''.join(p['text'] for item in response.get('output',[]) if item.get('type')=='message'
                     for p in item.get('content',[]) if p.get('type')=='output_text')
        if not text.strip():raise RuntimeError('Text compaction has no public continuation summary')
        if (self.case/'dispatch_hold.json').exists():raise RuntimeError('Compaction dispatch held; inspect evidence')
        return text

    async def _forward(self, scope, body, send):
        cohort=self.cohort/'provider_blocks'/f'{self.model_label}.json'
        if (self.case/'dispatch_hold.json').exists() or cohort.exists():
            await self._error(send,503,b'Experiment dispatch paused; see evidence');return
        request=json.loads(body)
        if (self.case/'user_smoke_complete.json').exists():
            write(self.case/'post_smoke.original_cli_request.json',_redact(request))
            await self._error(send,409,b'Operator act limit reached; no further provider dispatch')
            return
        if request.get('model')!=self.model or (request.get('reasoning') or {}).get('effort')!='xhigh':
            self._hold('model_or_effort_mismatch',{'model':request.get('model'),'reasoning':request.get('reasoning')})
            await self._error(send,400,b'Model/effort differs');return
        if self._attempts>=330 or sum(r['completed'] for r in self.rows)>=300:
            self._hold('model_call_budget',{'attempts':self._attempts});await self._error(send,503,b'Model call budget exhausted');return
        original=deepcopy(request)
        write(self.case/'cli-input'/f"{self.summary['requests']+1:06d}.json",_redact(original))
        try:
            normalized,profile_evidence=self.prompt_profile.transform(request)
            kind=request_kind(normalized,scope['path'])
            normalized,context_evidence,raw=await self.context.prepare(normalized,
                lambda req,history,reasons:self._compact_window(scope,req,history,reasons))
        except (ValueError,KeyError,TypeError,RuntimeError) as error:
            self._hold('context_or_profile_error',{'error_type':type(error).__name__,'error':str(error)})
            await self._error(send,503,b'Context/profile dispatch held; raw evidence retained');return
        write(self.case/'prompt-input'/f"{self.summary['requests']+1:06d}.json",{
            'original_cli_request':_redact(original),'model_request':_redact(normalized),
            'normalization':profile_evidence,'context':context_evidence})
        before=len(self.rows)
        buffered=await self._buffered_forward(scope,json.dumps(normalized,ensure_ascii=False,separators=(',',':')).encode())
        for message in buffered:await send(message)
        if len(self.rows)>before and self.rows[-1]['completed']:
            self.context.completed(raw,normalized['input'],self.rows[-1]['usage'],native_compact=kind=='compaction')

    def _finish_record(self, record, path, encoding):
        super()._finish_record(record,path,encoding)
        request_path=path.parent/f'{record["request_id"]:06d}.request.json'
        request=json.loads(request_path.read_text()).get('forwarded',{}) if request_path.exists() else {}
        kind=request_kind(request,record['endpoint'])
        raw=path.read_bytes()
        parsed={}
        try:
            if encoding.lower()=='gzip':raw=gzip.decompress(raw)
            elif encoding.lower()=='deflate':raw=zlib.decompress(raw)
            parsed=parse_response_body(raw,record['content_type'],endpoint=record['endpoint'],include_response=True)
            response=parsed.get('response')
            errors=provider_errors(raw,record['content_type'],response)
            usage=response.get('usage') if isinstance(response,dict) else None
        except Exception as error:
            usage=None;errors=[{'parse_error_type':type(error).__name__}]
        row={'request_id':record['request_id'],'status_code':record['status_code'],
             # CLI may close SSE immediately after response.completed, before
             # HTTP EOF. Count that paid terminal response against the budget,
             # while retaining the independent transport-integrity flag.
             'completed':bool(record.get('response_completed') and not errors
                              and 200<=record['status_code']<300),
             'capture_complete':record['complete'],
             'transport_complete':record.get('transport_complete',False),
             'usage':usage,'protocol_errors':errors,
             'endpoint':record['endpoint'],'request_kind':kind,'at':time.time(),
             'transient_capacity_or_server_error':transient_provider_error(record['status_code'],errors),
             'output_length_limit':output_length_limit(record,parsed,errors,usage)}
        with self._guard_lock:
            self.rows.append(row)
            with (self.case/'usage.jsonl').open('a') as stream:stream.write(json.dumps(row)+'\n')
            input_tokens=sum((r['usage'] or {}).get('input_tokens',0) for r in self.rows)
            cached=sum(((r['usage'] or {}).get('input_tokens_details') or {}).get('cached_tokens',0) for r in self.rows)
            output_tokens=sum((r['usage'] or {}).get('output_tokens',0) for r in self.rows)
            if kind=='compaction' and row['completed']:
                self._cache_epoch_start=len(self.rows)
            ordinary_cache=cache_statistics(self.rows)
            epoch=[r for r in self.rows[self._cache_epoch_start:]
                   if r.get('request_kind','turn')!='compaction' and r['completed'] and r['usage'] is not None]
            write(self.case/'usage.latest.json',{'schema_version':2,'attempts':self._attempts,
                  'completed':sum(r['completed'] for r in self.rows),
                  'input_tokens':input_tokens,'cached_input_tokens':cached,
                  'output_tokens':output_tokens,
                  'weighted_cache_ratio':ordinary_cache['weighted_cache_ratio'],
                  'cache_statistics':ordinary_cache,
                  'all_calls_weighted_cache_ratio':cached/input_tokens if input_tokens else None,
                  'cache_epoch_ordinary_completed_calls':len(epoch),
                  'unknown_usage_count':sum(r['usage'] is None for r in self.rows),
                  'billing_amount':'unknown; provider usage is retained separately'})
            if row['transient_capacity_or_server_error'] or row['output_length_limit']:
                pass  # Failure and unknown usage retained; bounded request retry occurs before client delivery.
            elif errors or record['status_code'] in [400,401,402,403]:
                self._hold('provider_terminal_or_protocol_error',row,
                           cohort=terminal_provider_error(record['status_code'],errors))
            elif usage is None:
                self._hold('usage_unknown',row)
            # Costs/call budgets span retries, but a fresh model context needs
            # its own five warmup calls plus the three-call measurement window.
            elif self.enforce_paid_cache and kind!='compaction' and len(epoch)>=8:
                recent=epoch[-3:]
                total=sum(r['usage'].get('input_tokens',0) for r in recent)
                hits=sum((r['usage'].get('input_tokens_details') or {}).get('cached_tokens',0) for r in recent)
                if len(recent)==3 and total and hits/total<0.65:
                    self._hold('cache_below_65_percent',{'request_ids':[r['request_id'] for r in recent],
                               'input_tokens':total,'cached_input_tokens':hits,'weighted_ratio':hits/total})
