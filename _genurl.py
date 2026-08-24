import sys
sys.path.insert(0, ".")
from proxy.dhan_auth import load_api_keypair, api_key_consent, consent_login_url
from proxy.dhan_broker import _load_athena_env
k, s = load_api_keypair()
cid = _load_athena_env()["client_id"]
res = api_key_consent(k, s, client_id=cid)
cid2 = res.get("consentAppId") or ""
print("CONSENT_ID_OK:", bool(cid2))
if cid2:
    print("LOGIN_URL:", consent_login_url(cid2))
