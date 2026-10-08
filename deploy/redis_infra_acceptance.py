"""Local Compose acceptance against a built AI image and a disposable PG network.

Requires INFRA_TEST_DATABASE_URL for that network; never loads production env or
secrets. The temporary Compose runs the real app/lifespan with offline test
settings, while reusing Redis and its optional health dependency verbatim.
"""

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
from uuid import uuid4

import yaml

ROOT = Path(__file__).resolve().parents[1]
SWITCHES = ('BUDGET_LEDGER_BACKEND', 'SESSION_CACHE_BACKEND', 'RATELIMIT_BACKEND',
    'CONTEXT_DERIVED_CACHE_BACKEND', 'PROFILE_CACHE_BACKEND', 'DEDUP_LOCK_BACKEND',
    'TRANSFER_IDEMPOTENCY_BACKEND', 'PLAYCOUNT_BACKEND', 'PROGRESS_EVENTS_BACKEND')


def command(args, *, body=None, check=True, env=None):
    result = subprocess.run(args, input=body, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', env=env, timeout=180)
    if check and result.returncode:
        # Command output is not included: initialization errors may contain URLs.
        raise RuntimeError('acceptance command failed: ' + args[0] + ' ' + args[1])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--postgres-network', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    assert command(['docker', 'network', 'inspect', '--format',
        '{{index .Labels "codex.task"}}', args.postgres_network]).stdout.strip() == 'redis-infra'
    database = os.environ['INFRA_TEST_DATABASE_URL']
    production = yaml.safe_load((ROOT / 'docker-compose.production.yml').read_text(encoding='utf-8'))
    redis = deepcopy(production['services']['redis'])
    project = 'moodflow-redis-infra-' + uuid4().hex[:10]
    env = dict(os.environ)
    env.pop('REDIS_URL', None)
    result = {'image': args.image, 'redis_image': redis['image'], 'switches': [], 'startup': []}
    with tempfile.TemporaryDirectory(prefix='moodflow-redis-infra-') as directory:
        folder = Path(directory).resolve()
        assert folder.is_relative_to(Path(tempfile.gettempdir()).resolve())
        path = folder / 'compose.yml'
        base_environment = deepcopy(production['services']['ai-service']['environment'])
        base_environment.update(DATABASE_URL=database, LLM_API_KEY=uuid4().hex,
            LLM_BASE_URL='https://example.test', LLM_MODEL='infra-test', LLM_TIMEOUT_SECONDS='5',
            SPRING_BOOT_BASE_URL='http://127.0.0.1:1', SPRING_BOOT_TIMEOUT_SECONDS='1',
            PERSISTENCE_BACKEND='postgres', AI_CONTINUITY_STORE_MODE='postgres',
            CHECKPOINT_BACKEND='memory', MEMORY_BACKEND='disabled', AI_RAG_RETRIEVAL_MODE='empty',
            JEV_ENABLED='false', JEV_ROUTER_V5_SHADOW_ENABLED='false')
        dependency = deepcopy(production['services']['ai-service']['depends_on']['redis'])
        spec = {'services': {'redis': deepcopy(redis), 'ai-service': {
            'image': args.image, 'init': True,
            'entrypoint': ['python', '-m', 'uvicorn', 'app.main:app', '--host', '0.0.0.0', '--port', '8001'],
            'environment': base_environment, 'networks': ['backend', 'redis_internal'],
            'depends_on': {'redis': dependency}, 'healthcheck': {
                'test': ['CMD', 'python', '-c', "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health',timeout=2)"],
                'interval': '2s', 'timeout': '3s', 'retries': 30, 'start_period': '10s'}}},
            'networks': {'backend': {'external': True, 'name': args.postgres_network},
                'redis_internal': deepcopy(production['networks']['redis_internal'])}}
        compose = ['docker', 'compose', '-p', project, '-f', str(path)]

        def write(switch=None, all_on=False):
            environment = spec['services']['ai-service']['environment']
            for name in SWITCHES:
                environment[name] = 'redis' if all_on or name == switch else ('history' if name == SWITCHES[0] else 'none')
            path.write_text(yaml.safe_dump(spec, sort_keys=False), encoding='utf-8')

        def cid(service):
            return command([*compose, 'ps', '-aq', service], env=env).stdout.strip()

        def execute(script):
            output = command(['docker', 'exec', '-i', cid('ai-service'), 'python', '-'], body=script)
            return json.loads(output.stdout.strip().splitlines()[-1])

        def ready():
            deadline = time.monotonic() + 150
            while time.monotonic() < deadline:
                container = cid('ai-service')
                state = command(['docker', 'inspect', '--format', '{{.State.Health.Status}}', container], check=False)
                if state.stdout.strip() == 'healthy':
                    return True
                time.sleep(1)
            raise RuntimeError('AI readiness timed out')

        def http_play():
            return execute("""import json, urllib.request, urllib.error
from uuid import uuid4
song='infra_'+uuid4().hex
url='http://127.0.0.1:8001/api/ai/playcounts/'+song
response=urllib.request.urlopen(urllib.request.Request(url,method='POST'),timeout=5)
assert response.status==200 and json.load(response)['count']==1
try:
    urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8001/api/ai/playcounts/bad!',method='POST'),timeout=5)
except urllib.error.HTTPError as error:
    assert error.code==422
else:
    raise AssertionError('invalid song accepted')
print(json.dumps({'success':200,'invalid':422}))
""")

        try:
            write()
            config = json.loads(command([*compose, 'config', '--format', 'json'], env=env).stdout)
            assert config['services']['ai-service']['environment']['REDIS_URL'] == 'redis://redis:6379/0'
            overridden = dict(env, REDIS_URL='redis://alternate.invalid:6379/7')
            alt = json.loads(command([*compose, 'config', '--format', 'json'], env=overridden).stdout)
            assert alt['services']['ai-service']['environment']['REDIS_URL'] == overridden['REDIS_URL']
            command([*compose, 'up', '-d'], env=env)
            ready()
            result['default_url_and_override'] = True
            result['connection'] = execute("""import json,os
from redis import Redis
client=Redis.from_url(os.environ['REDIS_URL'],decode_responses=True)
assert client.connection_pool.connection_kwargs['host']=='redis' and client.ping()
print(json.dumps({'version':client.info('server')['redis_version'],'ping':True}))
""")
            redis_id = cid('redis')
            ports = json.loads(command(['docker', 'inspect', '--format', '{{json .NetworkSettings.Ports}}', redis_id]).stdout)
            assert all(not binding for binding in (ports or {}).values())
            assert json.loads(command(['docker', 'inspect', '--format', '{{json .Mounts}}', redis_id]).stdout) == []
            with socket.socket() as probe:
                probe.settimeout(1)
                assert probe.connect_ex(('127.0.0.1', 6379)) != 0
            networks = json.loads(command(['docker','inspect','--format','{{json .NetworkSettings.Networks}}',redis_id]).stdout)
            assert len(networks)==1
            redis_ip=next(iter(networks.values()))['IPAddress']
            network_name=next(iter(networks))
            assert command(['docker','network','inspect','--format','{{.Internal}}',network_name]).stdout.strip()=='true'
            outsider = command(['docker','run','--rm','--network',args.postgres_network,'--entrypoint','python',args.image,
                '-c', f"import socket; p=socket.socket(); p.settimeout(1); assert p.connect_ex(({redis_ip!r},6379)) != 0"],check=False)
            assert outsider.returncode==0
            result['no_host_ports_no_volumes_internal_only'] = True
            for switch in SWITCHES:
                write(switch)
                command([*compose,'up','-d','--no-deps','--force-recreate','ai-service'],env=env)
                ready()
                script = (ROOT/'deploy/redis_infra_smoke.py').read_text(encoding='utf-8')
                status=execute("import os\nos.environ['INFRA_SMOKE_SWITCH']="+repr(switch)+"\n"+script)
                assert status['ok']
                result['switches'].append({'switch':switch,**status})
            write(all_on=True)
            command([*compose,'up','-d','--no-deps','--force-recreate','ai-service'],env=env)
            ready()
            result['all_on_http']=http_play()
            for scenario in ['unhealthy','failed']:
                command([*compose,'stop','ai-service','redis'],env=env)
                spec['services']['redis']=deepcopy(redis)
                spec['services']['redis']['restart']='no'
                if scenario=='unhealthy':
                    spec['services']['redis']['command']=['redis-server','--port','0','--unixsocket','/tmp/redis.sock','--save','','--appendonly','no']
                    spec['services']['redis']['healthcheck'].update(interval='1s',timeout='1s',retries=1,start_period='0s')
                else:
                    spec['services']['redis']['command']=['redis-server','--infra-invalid-option']
                write(all_on=True)
                up=command([*compose,'up','-d','--force-recreate'],env=env)
                ready()
                state=command(['docker','inspect','--format','{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}',cid('redis')]).stdout.strip()
                logs=command([*compose,'logs','--no-color','ai-service'],env=env).stdout
                http=http_play()
                logs=command([*compose,'logs','--no-color','ai-service'],env=env).stdout
                assert 'fail-open' in logs and 'using PG counters' in logs
                result['startup'].append({'scenario':scenario,'compose_exit':up.returncode,'redis_state':state,'ai_healthy':True,'http':http,'warn_and_pg_fallback':True})
            spec['services']['redis']=deepcopy(redis)
            write()
            command([*compose,'up','-d','--force-recreate'],env=env)
            ready()
            execute("import json,os\nfrom redis import Redis\nr=Redis.from_url(os.environ['REDIS_URL']);r.set('infra:restart-proof','temporary');print(json.dumps({'written':True}))")
            command([*compose,'restart','redis'],env=env)
            time.sleep(1)
            execute("import json,os\nfrom redis import Redis\nr=Redis.from_url(os.environ['REDIS_URL']);assert r.get('infra:restart-proof') is None;print(json.dumps({'lost':True}))")
            result['restart_discards_hot_data']=True
        finally:
            command([*compose,'down','--remove-orphans'],check=False,env=env)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))


if __name__ == '__main__':
    main()
