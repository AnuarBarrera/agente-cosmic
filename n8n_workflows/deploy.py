#!/usr/bin/env python3
"""
Despliega workflows de n8n para el agente de negocio via REST API.
Uso: python n8n_workflows/deploy.py

Arquitectura de cada workflow:
  Webhook → HTTP Request (API externa) → [más HTTP si hace falta] → Code (solo formato) → HTTP Request (callback Django)

Los Code nodes NO hacen llamadas HTTP — solo transforman datos.
Las llamadas HTTP las hacen siempre nodos HTTP Request.
"""
import os
import sys
import uuid
import requests

# ── Configuración ──────────────────────────────────────────────────────────────

def _load_env():
    env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
    env = {}
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, _, v = line.partition('=')
                    env[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return env


_env = _load_env()
N8N_API_KEY         = os.environ.get('N8N_API_KEY') or _env.get('N8N_API_KEY', '')
N8N_CALLBACK_TOKEN  = os.environ.get('N8N_CALLBACK_TOKEN') or _env.get('N8N_CALLBACK_TOKEN', '')
N8N_BASE            = 'http://localhost:5678'
DJANGO_CALLBACK_URL = 'http://172.17.0.1:3001/api/v1/agent/n8n/callback/'

FB_PAGE_ID_TUWEBMX      = _env.get('FACEBOOK_PAGE_ID_TUWEBMX', '')
FB_TOKEN_TUWEBMX        = _env.get('FACEBOOK_TOKEN_TUWEBMX') or _env.get('FACEBOOK_TOKEN_ID_TUWEBMX', '')
FB_PAGE_ID_ANUARBARRERA = _env.get('FACEBOOK_PAGE_ID_ANUARBARRERA', '')
FB_TOKEN_ANUARBARRERA   = _env.get('FACEBOOK_TOKEN_ANUARBARRERA', '')

LINKEDIN_CRED_ID   = 'coPcC5aHwBi6UWVh'
LINKEDIN_CRED_NAME = 'LinkedIn account'
LINKEDIN_ORG_ID    = _env.get('LINKEDIN_ORG_ID', '')

if not N8N_API_KEY:
    print('ERROR: N8N_API_KEY no encontrada. Genera una en n8n: Settings → API.')
    sys.exit(1)
if not N8N_CALLBACK_TOKEN:
    print('ERROR: N8N_CALLBACK_TOKEN no encontrada.')
    sys.exit(1)

HEADERS = {'X-N8N-API-KEY': N8N_API_KEY, 'Content-Type': 'application/json'}

# ── Constructores de nodos ─────────────────────────────────────────────────────

def _id():
    return str(uuid.uuid4())


def webhook_node(path: str, pos=(240, 300)):
    return {
        'id': _id(), 'name': 'Webhook',
        'type': 'n8n-nodes-base.webhook', 'typeVersion': 2,
        'position': list(pos), 'webhookId': _id(),
        'parameters': {
            'httpMethod': 'POST', 'path': path,
            'responseMode': 'onReceived', 'options': {},
        },
    }


def http_get_node(name: str, url: str, pos=(500, 300)):
    """HTTP Request GET — sin autenticación (token en la URL si hace falta)."""
    return {
        'id': _id(), 'name': name,
        'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2,
        'position': list(pos),
        'parameters': {'method': 'GET', 'url': url, 'options': {}},
    }


def http_get_oauth_node(name: str, url: str, cred_type: str, cred_id: str, cred_name: str, pos=(500, 300)):
    """HTTP Request GET con credencial OAuth2 ya configurada en n8n."""
    return {
        'id': _id(), 'name': name,
        'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2,
        'position': list(pos),
        'parameters': {
            'method': 'GET', 'url': url,
            'authentication': 'predefinedCredentialType',
            'nodeCredentialType': cred_type,
            'options': {},
        },
        'credentials': {cred_type: {'id': cred_id, 'name': cred_name}},
    }


def code_node(name: str, code: str, pos=(700, 300)):
    """Code node para transformación de datos — SIN llamadas HTTP."""
    return {
        'id': _id(), 'name': name,
        'type': 'n8n-nodes-base.code', 'typeVersion': 2,
        'position': list(pos),
        'parameters': {'jsCode': code, 'mode': 'runOnceForAllItems'},
    }


def http_post_callback_node(pos=(900, 300)):
    """HTTP Request POST al callback de Django — envía el JSON completo del nodo anterior."""
    return {
        'id': _id(), 'name': 'Django Callback',
        'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2,
        'position': list(pos),
        'parameters': {
            'method': 'POST',
            'url': DJANGO_CALLBACK_URL,
            'sendHeaders': True,
            'headerParameters': {
                'parameters': [
                    {'name': 'X-N8N-Token', 'value': N8N_CALLBACK_TOKEN},
                    {'name': 'Content-Type', 'value': 'application/json'},
                ]
            },
            'sendBody': True,
            'contentType': 'json',
            'bodyParameters': {
                'parameters': [
                    {'name': 'job_id',  'value': '={{ $json.job_id }}'},
                    {'name': 'chat_id', 'value': '={{ $json.chat_id }}'},
                    {'name': 'status',  'value': '={{ $json.status }}'},
                    {'name': 'data',    'value': '={{ JSON.stringify($json.data) }}'},
                ]
            },
            'options': {},
        },
    }


def placeholder_code_node(platform: str, pos=(560, 300)):
    code = (
        f'const wh = $input.first().json;\n'
        f'const job_id = wh.body ? wh.body.job_id : wh.job_id;\n'
        f'const chat_id = wh.body ? wh.body.chat_id : wh.chat_id;\n'
        f'return [{{ json: {{ job_id, chat_id, status: "ok",\n'
        f'  data: {{ platform: "{platform}", note: "Placeholder — configura credenciales en n8n" }}\n'
        f'}} }}];\n'
    )
    return code_node(f'API {platform.capitalize()}', code, pos)


def make_workflow(name: str, nodes: list, connections: dict) -> dict:
    return {
        'name': name,
        'nodes': nodes,
        'connections': connections,
        'settings': {'executionOrder': 'v1'},
    }


# ── Workflows de Facebook ──────────────────────────────────────────────────────

def build_facebook_workflow(workflow_id: str, page_id: str, token: str, label: str) -> dict:
    """
    Webhook → HTTP GET (page info) → HTTP GET (posts) → Code (format) → HTTP POST (callback)
    El Code node solo transforma datos: lee page info y posts de nodos anteriores por nombre.
    """
    base = 'https://graph.facebook.com/v20.0'
    wh   = webhook_node(workflow_id, (240, 300))
    pg   = http_get_node(
        'FB Page Info',
        f'{base}/{page_id}?fields=name,fan_count,followers_count&access_token={token}',
        (460, 200),
    )
    ps   = http_get_node(
        'FB Posts',
        f'{base}/{page_id}/posts?fields=message,created_time,shares,permalink_url&limit=5&access_token={token}',
        (460, 400),
    )
    fmt_code = f"""// Formatea estadísticas de Facebook — {label}
const wh = $('Webhook').item.json.body;
const page = $('FB Page Info').item.json;
const posts = $('FB Posts').item.json;
const postData = (posts.data || []).map(function(p) {{
  return {{
    message: (p.message || '').substring(0, 150),
    created_time: p.created_time,
    shares: (p.shares) ? p.shares.count : 0,
    url: p.permalink_url || '',
  }};
}});
return [{{ json: {{
  job_id: wh.job_id,
  chat_id: wh.chat_id,
  status: 'ok',
  data: {{
    platform: 'facebook',
    page_name: page.name,
    fans: page.fan_count,
    followers: page.followers_count,
    recent_posts: postData,
  }},
}} }}];
"""
    fmt = code_node('Formatear Datos', fmt_code, (680, 300))
    cb  = http_post_callback_node((900, 300))

    # Conexiones secuenciales: Webhook → Page Info → Posts → Format → Callback
    connections = {
        'Webhook':         {'main': [[{'node': 'FB Page Info',    'type': 'main', 'index': 0}]]},
        'FB Page Info':    {'main': [[{'node': 'FB Posts',        'type': 'main', 'index': 0}]]},
        'FB Posts':        {'main': [[{'node': 'Formatear Datos', 'type': 'main', 'index': 0}]]},
        'Formatear Datos': {'main': [[{'node': 'Django Callback', 'type': 'main', 'index': 0}]]},
    }
    return make_workflow(workflow_id, [wh, pg, ps, fmt, cb], connections)


# ── Workflow de LinkedIn ───────────────────────────────────────────────────────

def build_linkedin_workflow(org_id: str) -> dict:
    """
    Webhook → HTTP GET (LinkedIn follower stats con OAuth2) → Code (format) → HTTP POST (callback)
    """
    wh = webhook_node('linkedin_stats', (240, 300))
    api = http_get_oauth_node(
        'LinkedIn API',
        f'https://api.linkedin.com/v2/organizationalEntityFollowerStatistics?q=organizationalEntity&organizationalEntity=urn:li:organization:{org_id}',
        'linkedInOAuth2Api', LINKEDIN_CRED_ID, LINKEDIN_CRED_NAME,
        (500, 300),
    )
    fmt_code = """// Formatea estadísticas de LinkedIn
const wh = $('Webhook').item.json.body;
const apiData = $input.first().json;
const elements = apiData.elements || [];
let totalFollowers = 0;
if (elements.length > 0) {
  const latest = elements[elements.length - 1];
  const gains = latest.followerGains || {};
  totalFollowers = (gains.organicFollowerGain || 0) + (gains.paidFollowerGain || 0);
}
return [{ json: {
  job_id: wh.job_id,
  chat_id: wh.chat_id,
  status: 'ok',
  data: {
    platform: 'linkedin',
    org_id: apiData.organizationalEntity || '',
    total_followers: (apiData.paging && apiData.paging.total) || totalFollowers,
    elements_count: elements.length,
  },
} }];
"""
    fmt = code_node('Formatear Datos', fmt_code, (720, 300))
    cb  = http_post_callback_node((940, 300))
    connections = {
        'Webhook':         {'main': [[{'node': 'LinkedIn API',     'type': 'main', 'index': 0}]]},
        'LinkedIn API':    {'main': [[{'node': 'Formatear Datos',  'type': 'main', 'index': 0}]]},
        'Formatear Datos': {'main': [[{'node': 'Django Callback',  'type': 'main', 'index': 0}]]},
    }
    return make_workflow('linkedin_stats', [wh, api, fmt, cb], connections)


# ── Workflow placeholder (para competitor y instagram) ────────────────────────

def build_placeholder_workflow(workflow_id: str, platform: str) -> dict:
    wh  = webhook_node(workflow_id)
    mid = placeholder_code_node(platform, (560, 300))
    cb  = http_post_callback_node((780, 300))
    connections = {
        'Webhook':                      {'main': [[{'node': mid['name'],         'type': 'main', 'index': 0}]]},
        mid['name']:                    {'main': [[{'node': 'Django Callback',   'type': 'main', 'index': 0}]]},
    }
    return make_workflow(workflow_id, [wh, mid, cb], connections)


# ── API helpers ────────────────────────────────────────────────────────────────

def list_workflows() -> list:
    r = requests.get(f'{N8N_BASE}/api/v1/workflows', headers=HEADERS, timeout=10)
    r.raise_for_status()
    return r.json().get('data', [])


def delete_workflow(wf_id: str, name: str) -> None:
    requests.delete(f'{N8N_BASE}/api/v1/workflows/{wf_id}', headers=HEADERS, timeout=10)
    print(f'  🗑  {name} eliminado')


def create_and_activate(wf_dict: dict) -> str:
    r = requests.post(f'{N8N_BASE}/api/v1/workflows', headers=HEADERS, json=wf_dict, timeout=15)
    r.raise_for_status()
    wf_id = r.json()['id']
    requests.post(f'{N8N_BASE}/api/v1/workflows/{wf_id}/activate', headers=HEADERS, timeout=10)
    return wf_id


# ── Main ───────────────────────────────────────────────────────────────────────

REAL_WORKFLOWS = [
    ('facebook_stats_tuwebmx',     lambda: build_facebook_workflow('facebook_stats_tuwebmx', FB_PAGE_ID_TUWEBMX, FB_TOKEN_TUWEBMX, 'Tu Web MX'),     FB_PAGE_ID_TUWEBMX and FB_TOKEN_TUWEBMX),
    ('facebook_stats_anuarbarrera', lambda: build_facebook_workflow('facebook_stats_anuarbarrera', FB_PAGE_ID_ANUARBARRERA, FB_TOKEN_ANUARBARRERA, 'Anuar Barrera'), FB_PAGE_ID_ANUARBARRERA and FB_TOKEN_ANUARBARRERA),
    ('linkedin_stats',             lambda: build_linkedin_workflow(LINKEDIN_ORG_ID),                                                                 LINKEDIN_ORG_ID),
]

PLACEHOLDER_WORKFLOWS = [
    ('instagram_stats',      'instagram'),
    ('instagram_competitor', 'instagram'),
    ('linkedin_competitor',  'linkedin'),
    ('facebook_competitor',  'facebook'),
]


def main():
    print(f'Conectando a n8n en {N8N_BASE}...')
    all_wf = list_workflows()
    by_name = {w['name']: w for w in all_wf}
    print(f'Workflows existentes: {len(all_wf)}')

    # Eliminar versiones anteriores de los 3 reales para re-crearlos limpios
    for name, _, _ in REAL_WORKFLOWS:
        if name in by_name:
            delete_workflow(by_name[name]['id'], name)

    # Crear/re-crear workflows reales
    for name, builder, ready in REAL_WORKFLOWS:
        if not ready:
            print(f'  ⚠️  {name} omitido: faltan variables en .env')
            continue
        wf_id = create_and_activate(builder())
        print(f'  ✅ {name} creado y activado (id={wf_id})')

    # Crear placeholders solo si no existen
    current = {w['name'] for w in list_workflows()}
    for name, platform in PLACEHOLDER_WORKFLOWS:
        if name in current:
            print(f'  ⏭  {name} ya existe, omitiendo')
        else:
            wf_id = create_and_activate(build_placeholder_workflow(name, platform))
            print(f'  ✅ {name} placeholder creado (id={wf_id})')

    print('\nWorkflows activos:')
    for w in list_workflows():
        if w['name'] != 'Prospector Local':
            print(f'  • {w["name"]} (active={w["active"]})')


if __name__ == '__main__':
    main()
