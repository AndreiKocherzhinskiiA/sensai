import json
import re
from pathlib import Path

FIELDS = ('id', 'name', 'tone', 'role', 'scope')
DEFAULT_DIR = Path(__file__).resolve().parent.parent / 'personas'

class PersonaError(Exception):
    """A persona file is missing, unreadable or invalid."""

def load_persona(path):
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError) as error:
        raise PersonaError(f'Cannot load persona {path.name}: {error}') from error
    if not isinstance(data, dict):
        raise PersonaError(f'Persona {path.name} must be a JSON object.')
    for field in FIELDS:
        value = data.get(field)
        if not isinstance(value, str) or not value.strip():
            raise PersonaError(f'Persona {path.name}: field {field!r} must be a non-empty string.')
    return {field: data[field].strip() for field in FIELDS}


def load_personas(directory=DEFAULT_DIR):
    personas = {}
    for path in sorted(Path(directory).glob('*.json')):
        persona = load_persona(path)
        if persona['id'] in personas:
            raise PersonaError(f'Duplicate persona id {persona["id"]!r}.')
        personas[persona['id']] = persona
    if not personas:
        raise PersonaError(f'No personas found in {directory}.')
    return personas


BUILTIN_IDS = frozenset({'explainer', 'reviewer'})
ID_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,39}$')
MAX_LEN = {'id': 40, 'name': 40, 'tone': 200, 'role': 200, 'scope': 400}


def slugify(name):
    slug = re.sub(r'[^a-z0-9]+', '-', str(name).lower()).strip('-')[:40].strip('-')
    return slug or 'persona'


def _clean(data):
    clean = {}
    for field in FIELDS:
        value = data.get(field)
        if not isinstance(value, str) or not value.strip():
            raise PersonaError(f'Field {field!r} is required.')
        value = value.strip()
        if len(value) > MAX_LEN[field]:
            raise PersonaError(f'Field {field!r} is longer than {MAX_LEN[field]} characters.')
        clean[field] = value
    if not ID_RE.match(clean['id']):
        raise PersonaError('Id may use lowercase letters, digits, "-" and "_" only.')
    return clean


def _write(directory, persona):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f'{persona["id"]}.json'
    tmp = target.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(persona, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    tmp.replace(target)


def create_persona(data, directory=DEFAULT_DIR):
    """Create a persona; derive a unique id from the name when none is given."""
    data = dict(data)
    existing = load_personas(directory)
    if not (data.get('id') or '').strip():
        base = slugify(data.get('name', ''))
        candidate, n = base, 2
        while candidate in existing:
            candidate = f'{base[:36]}-{n}'
            n += 1
        data['id'] = candidate
    persona = _clean(data)
    if persona['id'] in existing:
        raise PersonaError(f'Persona {persona["id"]!r} already exists.')
    _write(directory, persona)
    return persona


def update_persona(persona_id, data, directory=DEFAULT_DIR):
    if persona_id in BUILTIN_IDS:
        raise PersonaError('Built-in personas cannot be edited.')
    if persona_id not in load_personas(directory):
        raise KeyError(persona_id)
    persona = _clean({**data, 'id': persona_id})
    _write(directory, persona)
    return persona


def delete_persona(persona_id, directory=DEFAULT_DIR):
    if persona_id in BUILTIN_IDS:
        raise PersonaError('Built-in personas cannot be deleted.')
    if persona_id not in load_personas(directory):
        raise KeyError(persona_id)
    (Path(directory) / f'{persona_id}.json').unlink()


def persona_prompt(base_prompt, persona):
    return (
        f'{base_prompt}\n\n'
        f'Persona: {persona["name"]}\n'
        f'Role: {persona["role"]}\n'
        f'Tone: {persona["tone"]}\n'
        f'Scope: {persona["scope"]}'
    )
