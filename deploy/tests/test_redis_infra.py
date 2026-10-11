"""Deployment contracts that protect budget keys and Redis network isolation."""

from pathlib import Path
import re

import yaml

ROOT = Path(__file__).resolve().parents[2]


def compose(path='docker-compose.production.yml'):
    return yaml.safe_load((ROOT / path).read_text(encoding='utf-8'))


def test_redis_prod_and_dev_share_pinned_ephemeral_noeviction_contract():
    production = compose()
    development = compose('ai-service/docker-compose.qdrant.yml')
    redis = production['services']['redis']
    assert redis == development['services']['redis']
    assert re.fullmatch(r'redis:8@sha256:[0-9a-f]{64}', redis['image'])
    assert 'ports' not in redis and 'volumes' not in redis
    assert redis['networks'] == ['redis_internal']
    options = dict(zip(redis['command'][1::2], redis['command'][2::2]))
    assert options['--maxmemory'] == '512mb'
    assert options['--maxmemory-policy'] == 'noeviction'
    assert options['--save'] == '' and options['--appendonly'] == 'no'
    assert redis['healthcheck']['test'] == ['CMD', 'redis-cli', 'ping']
    assert redis['healthcheck']['interval'] == '10s'
    for config in [production, development]:
        assert config['networks']['redis_internal']['internal'] is True
    assert 'redis' not in production['volumes']
    assert set(production['services']['ai-service']['networks']) == {'backend', 'redis_internal'}


def test_redis_is_optional_at_start_and_features_remain_opt_in():
    ai = compose()['services']['ai-service']
    assert ai['depends_on']['redis'] == {'condition': 'service_healthy', 'required': False}
    environment = ai['environment']
    assert environment['REDIS_URL'] == '${REDIS_URL:-redis://redis:6379/0}'
    assert environment['BUDGET_LEDGER_BACKEND'] == '${BUDGET_LEDGER_BACKEND:-history}'
    for name in ['SESSION_CACHE', 'RATELIMIT', 'CONTEXT_DERIVED_CACHE', 'PROFILE_CACHE',
                 'DEDUP_LOCK', 'TRANSFER_IDEMPOTENCY', 'PLAYCOUNT', 'PROGRESS_EVENTS']:
        assert environment[name+'_BACKEND'] == '${'+name+'_BACKEND:-none}'
    assert environment['AI_AGENT_GRAPH_MODE'] == 'foundation'
    assert environment['HANDOFF_CANARY_ENABLED'] == '${HANDOFF_CANARY_ENABLED:-false}'
    assert environment['JEV_SHADOW_MODE'] == 'true'


def test_redis_dependency_and_maintenance_scripts_are_build_inputs():
    dockerfile = (ROOT/'deploy/ai.Dockerfile').read_text(encoding='utf-8')
    ignore = (ROOT/'.dockerignore').read_text(encoding='utf-8')
    assert '!ai-service/requirements.redis.txt' in ignore
    assert '-r requirements.redis.txt' in dockerfile
    assert 'COPY ai-service/requirements.redis.txt' in dockerfile
    for name in ['reconcile_redis_budget.py', 'flush_redis_playcounts.py']:
        assert '!ai-service/scripts/'+name in ignore
        assert 'COPY ai-service/scripts/'+name in dockerfile
