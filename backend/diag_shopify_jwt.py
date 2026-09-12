import os
from urllib.request import Request, urlopen
from jose import jwt

BACKEND_DIR = '/home/ronzoro/Downloads/mia-ai/mia-ai/backend'
ENV_PATH = os.path.join(BACKEND_DIR, '.env')
ENDPOINT = 'http://127.0.0.1:8002/auth/session'

env = {}
with open(ENV_PATH) as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if '=' in line:
            k, v = line.split('=', 1)
            env[k] = v

api_key = env.get('SHOPIFY_API_KEY', '')
api_secret = env.get('SHOPIFY_API_SECRET', '')

now = 1700000000
claims = {
    'aud': api_key,
    'iss': 'https://safenestt.myshopify.com/admin',
    'dest': 'https://safenestt.myshopify.com',
    'sub': 'diag-test-shop',
    'exp': now + 3600,
    'nbf': now - 60,
    'iat': now,
}

token = jwt.encode(claims, api_secret, algorithm='HS256')

req = Request(
    ENDPOINT,
    headers={'Authorization': f'Bearer {token}'},
    method='GET',
)
try:
    with urlopen(req) as resp:
        status = resp.status
        body = resp.read().decode()
except Exception as e:
    status = getattr(e, 'code', 0) or 0
    body = str(e)

print(f'STATUS={status}')
print(f'BODY={body}')
