import sys, os
sys.path.insert(0, '.')
from proxy.athena_env import load_athena_env
load_athena_env()
from proxy.dhan_auth import resolve_token_safe
cid = os.environ['DHAN_CLIENT_ID']
tok, src = resolve_token_safe(cid, notify=lambda *a: print('[notify]', a, flush=True))
print('result:', src, '| token len:', len(tok or ''))
