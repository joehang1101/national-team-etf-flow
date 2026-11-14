"""Versioned, editable observation universe; roles are research hypotheses."""
import json
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent / 'config' / 'universe.json'
CONFIG = json.loads(CONFIG_PATH.read_text(encoding='utf-8-sig'))
ENTRIES = CONFIG['entries']
TARGETS = [(e['code'], e['name'], e['market']) for e in ENTRIES]
if len(set(c for c, _, _ in TARGETS)) != len(TARGETS):
    raise ValueError('Duplicate universe code')
CAT_MAP = {e['code']: e['category'] for e in ENTRIES}
CAT_INDEX = CONFIG['index_sources']
BASE23 = {e['code'] for e in ENTRIES if e['legacy_group'] == 'base'}
NEW_TAG_CODES = set(CAT_MAP) - BASE23
BASE_TAG, NEW_TAG = '原始观察池', '扩展观察池'


def members_on(day, known_as_of=None, entries=None):
    """Undated legacy membership is explicitly not a point-in-time universe."""
    day = day.replace('-', '')
    known_as_of = (known_as_of or day).replace('-', '')
    return [e for e in (entries if entries is not None else ENTRIES)
            if (not e.get('effective_from') or e['effective_from'].replace('-', '') <= day)
            and (not e.get('effective_to') or day <= e['effective_to'].replace('-', ''))
            and (not e.get('known_from') or e['known_from'].replace('-', '') <= known_as_of)]
