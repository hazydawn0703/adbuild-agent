"""Real-model intake development regression; never a blind/generalization benchmark.

Run from backend with a private environment pointing DATABASE_URL to an initialized
*_test database: python evals/run_workflow.py --output /private/new-run --repeats 1.
No auth, database, mapping or model mocks. The only substitute fails any ad call.
Each output directory is new, append-only, and sealed after all cases complete.
"""
import argparse
import asyncio
import contextvars
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch
from uuid import uuid4

import httpx
from openpyxl import Workbook, load_workbook
from sqlalchemy.engine import make_url
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tracers.context import register_configure_hook

from app.auth import password_hash
from app.db import transaction
from app.main import app
from app.prompts import PROMPT_HASH
from app.settings import settings


CASE = contextvars.ContextVar('workflow_case', default='setup')
STAGE = contextvars.ContextVar('workflow_stage', default='setup')
RECORDER = contextvars.ContextVar('workflow_recorder', default=None)
register_configure_hook(RECORDER, True)


def now():
    return datetime.now(timezone.utc).isoformat()


def workbook_case(goal, variant, moment):
    """Inputs and permitted later answers are authored independently of model output."""
    number = 'launch-' + uuid4().hex[:10]
    post = str(secrets.randbelow(10**18) + 7 * 10**18)
    account = str(secrets.randbelow(10**18) + 7 * 10**18)
    identity = str(secrets.randbelow(10**18) + 7 * 10**18)
    start = (moment + timedelta(days=30)).replace(microsecond=0)
    end = start + timedelta(days=3)
    amount = Decimal('45.50') + variant
    facts = {'account_id': account, 'business_no': number, 'post_id': post,
             'post_url': 'https://www.tiktok.com/@development/video/' + post,
             'objective': goal, 'budget': str(amount), 'budget_mode': 'lifetime',
             'currency': 'USD', 'start_at': start.isoformat(), 'end_at': end.isoformat(),
             'timezone': 'UTC', 'audience': 'Development audience', 'language': 'en',
             'identity_id': identity, 'cta': 'LEARN_MORE', 'landing_url': 'https://example.org/development'}
    labels = {'business_no': 'Business reference', 'post_url': 'TikTok post URL', 'objective': 'Advertising goal',
              'budget': 'Budget amount', 'budget_mode': 'Budget type', 'currency': 'Currency',
              'start_at': 'Start time', 'end_at': 'End time', 'timezone': 'Timezone',
              'language': 'Language', 'cta': 'CTA', 'landing_url': 'Destination URL'}
    # Three separately authored layouts, with an additional semantically equal column/position variant.
    keys = list(labels)
    if goal == 'video_views':
        keys = ['objective', 'business_no', 'budget', 'currency', 'post_url'] + [k for k in keys if k not in ('objective', 'business_no', 'budget', 'currency', 'post_url')]
    elif goal == 'traffic':
        keys = list(reversed(keys))
    if variant:
        keys = keys[3:] + keys[:3]
    w = Workbook()
    sheet = w.active
    sheet.title = 'Input-' + uuid4().hex[:6]
    if goal == 'followers':
        sheet.append(['Campaign planning material; select only the requested business reference.'])
        sheet.append([])
    if variant:
        for _ in range(31): sheet.append([])
    headers = [labels[k] for k in keys] + ['Platform', 'Owner / not an account identifier']
    sheet.append(headers)
    for i in range(3):
        row = dict(facts)
        if i != 1:
            row['business_no'] = 'reference-' + uuid4().hex[:10]
            row['post_url'] = ('https://www.tiktok.com/@reference/video/' + str(8 * 10**18 + secrets.randbelow(10**17))) if i == 0 else 'https://www.facebook.com/example/posts/123'
        sheet.append([row[k] for k in keys] + ['TikTok' if i != 2 else 'Facebook', 'Example operator'])
    accounts = w.create_sheet('Account-' + uuid4().hex[:6])
    accounts.append(['Account information'])
    accounts.append(['TikTok advertising account ID', account])
    data = BytesIO()
    w.save(data)
    history = Workbook()
    hs = history.active
    hs.title = 'Export-' + uuid4().hex[:6]
    hs.append(['Campaign ID','Ad Group ID','Ad ID','Post ID','Campaign Name','Advertising Objective','Start Time','End Time','Languages','Campaign Budget Amount','Campaign Budget Type','Ad Group Budget','Ad Group Budget Type'])
    hs.append([str(7*10**18+secrets.randbelow(10**17)),str(7*10**18+secrets.randbelow(10**17)),str(7*10**18+secrets.randbelow(10**17)),post,
               'Prior development batch','traffic' if goal!='traffic' else 'followers',
               (moment-timedelta(days=10)).replace(tzinfo=None).isoformat(sep=' '),(moment-timedelta(days=7)).replace(tzinfo=None).isoformat(sep=' '),'en','35','lifetime','35','lifetime'])
    export = BytesIO()
    history.save(export)
    message = f'本次新增广告，只搭建业务编号 {number} 的 TikTok 需求。其他记录作为参考，不纳入本次搭建。目标按需求表明确配置，预算与排期也按表中原文；不要创建真实广告。'
    # Audience and identity are intentionally absent initially, and may be supplied via normal clarification.
    permitted_answers = {'audience': facts['audience'], 'identity_id': identity}
    return {'name': f'{goal}-layout-{variant}', 'facts': facts, 'initial_message': message,
            'permitted_answers': permitted_answers, 'bytes': data.getvalue(), 'export_bytes':export.getvalue(),'sheet': sheet.title}


class StageFailure(Exception):
    pass


class Run:
    def __init__(self, output):
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=False)
        self.lock = threading.Lock()
        self.ad_calls = 0

    def write(self, path, value):
        with (self.output / path).open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, default=str)

    def event(self, kind, payload):
        # Private records contain synthetic business inputs, never configured credentials.
        text = json.dumps({'time': now(), 'case': CASE.get(), 'stage': STAGE.get(), 'event': kind, 'payload': payload}, ensure_ascii=False, default=str)
        cfg = settings()
        for secret in (cfg.llm_api_key, cfg.database_url, cfg.session_secret, cfg.token_encryption_key):
            if secret: text = text.replace(secret, '[REDACTED]')
        with self.lock:
            with (self.output / 'model-events.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(text + '\n')

    async def forbidden_ad_call(self, *args, **kwargs):
        self.ad_calls += 1
        raise AssertionError('Advertising interface called during preview-only regression')


def inspect_result(state, preview, case, uploaded_file):
    rows = (state.get('draft') or {}).get('requirements', [])
    included = [r for r in rows if r.get('included')]
    facts = case['facts']
    scope_ok = len(included) == 1 and included[0].get('business_no') == facts['business_no']
    fields = []
    row = included[0] if scope_ok else {}
    for field in ('account_id', 'post_id', 'objective', 'budget', 'budget_mode', 'currency', 'start_at', 'end_at', 'timezone', 'audience', 'identity_id'):
        actual = row.get(field)
        expected = facts[field]
        if field == 'budget':
            try: correct = Decimal(str(actual)) == Decimal(expected)
            except Exception: correct = False
        elif field in ('start_at', 'end_at'):
            try: correct = datetime.fromisoformat(actual) == datetime.fromisoformat(expected)
            except (TypeError, ValueError): correct = False
        else: correct = actual == expected
        fields.append({'field': field, 'correct': correct, 'actual': actual, 'expected': expected})
    refs = []
    workbook = load_workbook(BytesIO(case['bytes']), data_only=True)
    message_ids = {m['id'] for m in state['task'].get('messages', [])}
    for field in ('budget', 'objective', 'post_id', 'start_at', 'end_at', 'account_id', 'audience', 'identity_id'):
        source = row.get('sources', {}).get(field)
        valid = False
        if source and source.get('kind') == 'excel':
            if source.get('file_id') == uploaded_file and source.get('sheet') in workbook.sheetnames:
                try: valid = str(workbook[source['sheet']][source['cell']].value).strip() == source['raw_value']
                except (KeyError, ValueError, TypeError): pass
        elif source and source.get('kind') == 'confirmed':
            valid = source.get('message_id') in message_ids
        elif source and source.get('kind') == 'template' and field == 'account_id':
            valid = source.get('raw_value') == facts['account_id']
        refs.append({'field': field, 'valid': valid, 'source': source})
    return {'scope_correct': scope_ok, 'fields': fields, 'sources': refs,
            'fields_correct': sum(f['correct'] for f in fields), 'fields_expected': len(fields),
            'sources_correct': sum(r['valid'] for r in refs), 'sources_expected': len(refs),
            'counts_correct': bool(preview) and all(preview['counts'][k] == 1 for k in ('campaigns', 'adgroups', 'ads'))}


async def main(args):
    cfg = settings()
    if cfg.execution_enabled: raise RuntimeError('Refuse execution-enabled environment')
    if not (make_url(cfg.database_url).database or '').endswith('_test'):
        raise RuntimeError('Refuse non-test database')
    if not cfg.llm_api_key: raise RuntimeError('A real configured model is required')
    if args.repeats < 1 or args.repeats > 3: raise ValueError('Repeats must be between 1 and 3')
    os.umask(0o077)
    run = Run(args.output)
    os.environ['STORAGE_DIRECTORY'] = str(run.output / 'private-workbooks')
    settings.cache_clear()
    moment = datetime.now(timezone.utc)
    source_root=Path(__file__).resolve().parents[2]
    sources=[*sorted((source_root/'backend/app').glob('*.py')),Path(__file__).resolve()]
    source_hashes={str(p.relative_to(source_root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    run.write('protocol.json', {'label': 'constructed_development_regression', 'time_basis': moment.isoformat(),
              'prompt_hash': PROMPT_HASH, 'configured_model': cfg.llm_model, 'repeats': args.repeats,
              'mapping_corrections': 0, 'max_analysis_rounds': 3, 'extra_model_retries': 0,
              'normal_answers': 'Only pre-authored audience and identity; repeating explicit inputs is recorded as manual correction',
              'confirmation': 'Harness simulates review acknowledgment only after source-grounded checks pass',
              'execution_enabled': False, 'independent_holdout': False,
              'source_hashes':source_hashes,
              'dependency_versions': {p: importlib.metadata.version(p) for p in ('openpyxl', 'langgraph', 'langchain-openai', 'fastapi', 'PyMySQL')}})

    class Recorder(BaseCallbackHandler):
        def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
            run.event('model_start', {'run_id': str(run_id), 'messages': [[m.model_dump(mode='json') for m in seq] for seq in messages]})
        def on_llm_end(self, response, *, run_id, **kwargs):
            run.event('model_end', {'run_id': str(run_id), 'response': response.model_dump(mode='json')})
        def on_llm_error(self, error, *, run_id, **kwargs):
            run.event('model_error', {'run_id': str(run_id), 'error_type': type(error).__name__, 'message': str(error)})
    RECORDER.set(Recorder())
    tenant, user = str(uuid4()), str(uuid4())
    email, password = user + '@workflow.invalid', secrets.token_urlsafe(32)
    with transaction() as c:
        c.execute('insert into users(id,email,password_hash) values(%s,%s,%s)', (user, email, password_hash.hash(password)))
        c.execute('insert into tenants(id,name) values(%s,%s)', (tenant, 'Constructed intake regression'))
        c.execute('insert into memberships values(%s,%s,%s)', (tenant, user, 'admin'))
        jobs_before = c.execute('select count(*) n from jobs').fetchone()['n']
    summaries = []
    with patch('app.executor.call', run.forbidden_ad_call), patch('app.executor.call_binding', run.forbidden_ad_call), patch('app.main.connect', run.forbidden_ad_call):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url='https://workflow.invalid', timeout=200) as client:
            login = await client.post('/auth/login', json={'email': email, 'password': password})
            if login.status_code != 200: raise RuntimeError('Test user authentication failed')
            client.headers['Authorization'] = 'Bearer ' + login.json()['access_token']
            for repeat in range(args.repeats):
                for goal in ('followers', 'video_views', 'traffic'):
                    for variant in (0, 1):
                        case = workbook_case(goal, variant, moment)
                        name = f'{goal}-{variant}-repeat-{repeat + 1}'
                        CASE.set(name)
                        case_path = run.output / name
                        case_path.mkdir()
                        (case_path / 'input.xlsx').write_bytes(case['bytes'])
                        (case_path / 'export.xlsx').write_bytes(case['export_bytes'])
                        run.write(Path(name) / 'authored-input.json', {k: v for k, v in case.items() if k not in ('bytes','export_bytes')})
                        count = 0
                        summary = {'case': name, 'goal': goal, 'variant': variant, 'repeat': repeat + 1,
                                   'manual_corrections': 0, 'normal_answers': 0, 'mapping_corrections': 0,
                                   'proposal_acceptances': 0, 'errors': [], 'complete': False}
                        project_id = task_id = file_id = None

                        async def call(stage, method, path, **kwargs):
                            nonlocal count
                            STAGE.set(stage)
                            count += 1
                            started = time.monotonic()
                            response = await client.request(method, path, **kwargs)
                            try: body = response.json()
                            except ValueError: body = {'unparsed_body': response.text}
                            request = {'method': method, 'path': path, 'json': kwargs.get('json'), 'params': kwargs.get('params')}
                            if 'files' in kwargs:
                                request['files'] = {k: {'name': v[0], 'sha256': hashlib.sha256(v[1]).hexdigest()} for k, v in kwargs['files'].items()}
                            run.write(Path(name) / f'{count:03d}-{stage}.json', {'time': now(), 'elapsed': time.monotonic() - started,
                                      'request': request, 'status': response.status_code, 'body': body})
                            if response.status_code >= 400:
                                summary['errors'].append({'stage': stage, 'status': response.status_code, 'body': body})
                                raise StageFailure(stage)
                            return body

                        async def action(state, stage, action_name, **kwargs):
                            return await call(stage, 'POST', f'/projects/{project_id}/chat', json={
                                'task_id': task_id, 'request_id': str(uuid4()), 'expected_version': state['task']['version'],
                                'expected_draft_version': (state.get('draft') or {}).get('version'), 'action': action_name, **kwargs})

                        try:
                            async with asyncio.timeout(800):
                                project = await call('project', 'POST', '/projects', json={'tenant_id': tenant, 'name': name, 'account_id': case['facts']['account_id']})
                                project_id = project['id']
                                task = await call('task', 'POST', f'/projects/{project_id}/tasks', json={'name': name})
                                task_id = task['id']
                                await action({'task': task}, 'initial-intent', 'message', message=case['initial_message'])
                                await call('upload-history', 'POST', f'/projects/{project_id}/files', params={'kind':'export','task_id':task_id}, files={'file':('export.xlsx',case['export_bytes'])})
                                uploaded = await call('upload', 'POST', f'/projects/{project_id}/files', params={'kind': 'requirements', 'task_id': task_id}, files={'file': ('input.xlsx', case['bytes'])})
                                file_id = uploaded['id']
                                if uploaded.get('file_error'): raise StageFailure('Workbook unreadable')
                                proposal = await call('auto-mapping', 'POST', f'/files/{file_id}/mapping/propose')
                                if not proposal.get('complete', True) or not proposal.get('mappings'): raise StageFailure('Mapping incomplete')
                                await call('confirm-unchanged-mapping', 'POST', f'/files/{file_id}/mapping/confirm', json={
                                    'mappings': proposal['mappings'], 'expected_task_version': proposal['expected_task_version'], 'request_id': str(uuid4())})
                                for round_index in range(3):
                                    state = await call('reload', 'GET', '/tasks/' + task_id)
                                    answers = {}
                                    for q in state['task'].get('questions', []):
                                        field = q['field']
                                        if field in case['permitted_answers']:
                                            answers[q['id']] = case['permitted_answers'][field]
                                            summary['normal_answers'] += 1
                                    if answers:
                                        await action(state, 'provided-answers', 'answer', answers=answers)
                                        state = await call('reload-after-answers', 'GET', '/tasks/' + task_id)
                                    await action(state, 'analyze', 'analyze')
                                    state = await call('reload-analysis', 'GET', '/tasks/' + task_id)
                                    pending = state['task'].get('proposal')
                                    if pending and pending['status'] == 'pending':
                                        await action(state, 'accept-unchanged-proposal', 'accept', proposal_id=pending['id'])
                                        summary['proposal_acceptances'] += 1
                                        state = await call('reload-accepted', 'GET', '/tasks/' + task_id)
                                    preview = await call('preview', 'GET', '/drafts/' + state['draft']['id'] + '/preview')
                                    if preview['can_confirm']: break
                                summary.update(inspect_result(state, preview, case, file_id))
                                all_correct = summary['scope_correct'] and all(f['correct'] for f in summary['fields']) and all(s['valid'] for s in summary['sources']) and summary['counts_correct']
                                if all_correct and preview['can_confirm']:
                                    await call('confirm-reviewed-preview', 'POST', '/drafts/' + state['draft']['id'] + '/confirm', json={
                                        'expected_version': preview['version'], 'digest': preview['digest'], 'acknowledged': True})
                                    preview = await call('verify-confirmation', 'GET', '/drafts/' + state['draft']['id'] + '/preview')
                                summary['confirmation_valid'] = preview['approval_valid']
                                summary['complete'] = bool(all_correct and preview['approval_valid'] and not summary['manual_corrections'])
                        except Exception as error:
                            summary['runner_error'] = {'type': type(error).__name__, 'message': str(error)}
                        with transaction() as c:
                            counts = {table: c.execute('select count(*) n from ' + table + ' where project_id=%s', (project_id,)).fetchone()['n'] for table in ('runs', 'deployments')}
                            row = c.execute('select reserved from projects where id=%s', (project_id,)).fetchone()
                            counts['reserved'] = str(row['reserved']) if row else '0'
                            counts['jobs_delta'] = c.execute('select count(*) n from jobs').fetchone()['n'] - jobs_before
                            counts['ad_calls'] = run.ad_calls
                            counts['project_observed'] = bool(project_id and row)
                        summary['safety_observations'] = counts
                        summary['safe'] = counts['project_observed'] and counts['runs'] == counts['deployments'] == counts['jobs_delta'] == counts['ad_calls'] == 0 and Decimal(counts['reserved']) == 0
                        summary['complete'] = summary['complete'] and summary['safe']
                        run.write(Path(name) / 'summary.json', summary)
                        summaries.append(summary)
                        print(json.dumps({'completed': len(summaries), 'passed': sum(s['complete'] for s in summaries), 'case': name}), flush=True)
    mismatches=[str(p.relative_to(source_root)) for p in sources if hashlib.sha256(p.read_bytes()).hexdigest()!=source_hashes[str(p.relative_to(source_root))]]
    run.write('results.json', {'label': 'constructed_development_regression', 'time': now(), 'tenant_id': tenant,
              'prompt_hash': PROMPT_HASH, 'cases': summaries, 'passed': sum(s['complete'] for s in summaries), 'total': len(summaries),
              'field_correct': sum(s.get('fields_correct', 0) for s in summaries), 'field_expected': 11 * len(summaries),
              'source_correct': sum(s.get('sources_correct', 0) for s in summaries), 'source_expected': 8 * len(summaries),
              'scope_correct': sum(s.get('scope_correct', False) for s in summaries), 'manual_corrections': sum(s['manual_corrections'] for s in summaries),
              'safe_cases': sum(s['safe'] for s in summaries), 'source_mismatches_after':mismatches,'execution_enabled': False})
    files = {str(p.relative_to(run.output)): hashlib.sha256(p.read_bytes()).hexdigest() for p in run.output.rglob('*') if p.is_file()}
    run.write('seal.json', {'time': now(), 'files': files})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--repeats', type=int, default=1)
    asyncio.run(main(parser.parse_args()))
