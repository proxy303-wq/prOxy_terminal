
import sys, os, time, copy
from oci.config import from_file
from oci.identity import IdentityClient
from oci.identity.models import CreateRegionSubscriptionDetails

CFG = from_file(r"C:\Users\tgowd\.oci\config")
TEN = CFG["tenancy"]
HOME = "ap-hyderabad-1"
CFG["region"] = HOME
id_c = IdentityClient(CFG)
subs = id_c.list_region_subscriptions(TEN).data
print("subscribed regions:", [s.region_name for s in subs], flush=True)
names = [s.region_name for s in subs]
if "ap-mumbai-1" not in names:
    print("subscribing ap-mumbai-1...", flush=True)
    try:
        id_c.create_region_subscription(TEN, CreateRegionSubscriptionDetails(
            region_key="BOM"))
        print("subscription accepted - waiting 60s to propagate", flush=True)
        time.sleep(60)
        subs2 = id_c.list_region_subscriptions(TEN).data
        print("after:", [s.region_name for s in subs2], flush=True)
    except Exception as e:
        print("subscribe FAILED:", str(e)[:300], flush=True)
else:
    print("ap-mumbai-1 already subscribed", flush=True)
