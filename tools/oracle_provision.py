
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oci.config import from_file
from oci.core import VirtualNetworkClient, ComputeClient, BlockstorageClient
from oci.identity import IdentityClient
from oci.core.models import (CreateVcnDetails, CreateInternetGatewayDetails,
                             UpdateRouteTableDetails, RouteRule, UpdateSecurityListDetails,
                             IngressSecurityRule, TcpOptions, PortRange,
                             LaunchInstanceDetails, CreateVnicDetails, LaunchInstanceShapeConfigDetails,
                             CreateVolumeDetails, AttachParavirtualizedVolumeDetails,
                             CreateSubnetDetails)
from oci.core.models import UpdateRouteTableDetails as URT

CFG = from_file(r"C:\Users\tgowd\.oci\config")
TEN = CFG["tenancy"]
REGION = os.environ.get("ORACLE_REGION", CFG["region"])
CFG["region"] = REGION
print("region:", REGION, flush=True)

vcn_c = VirtualNetworkClient(CFG)
comp_c = ComputeClient(CFG)
bs_c = BlockstorageClient(CFG)
import copy
HOME_REGION = os.environ.get("ORACLE_HOME_REGION", "ap-hyderabad-1")
_HOME_CFG = copy.deepcopy(CFG)
_HOME_CFG["region"] = HOME_REGION
id_c = IdentityClient(_HOME_CFG)   # IAM is a GLOBAL service - home region only
try:
    from oci.identity.models import CreateRegionSubscriptionDetails
    subs = id_c.list_region_subscriptions(TEN).data
    if not any(s.region_name == REGION for s in subs):
        id_c.create_region_subscription(TEN, CreateRegionSubscriptionDetails(
            region_key=REGION.split("-")[1].upper()))
        print("subscribed region:", REGION, flush=True)
        time.sleep(30)   # region takes a moment to become usable
    else:
        print("region already subscribed:", REGION, flush=True)
except Exception as e:
    print("region subscribe skipped:", str(e)[:150], flush=True)

# 1) availability domain
ads = id_c.list_availability_domains(TEN).data
AD = ads[0].name
print("AD:", AD, flush=True)

# 2) VCN (idempotent: reuse if exists)
vcns = vcn_c.list_vcns(compartment_id=TEN).data
vcn = next((v for v in vcns if v.display_name == "proxy-vcn"), None)
if not vcn:
    vcn = vcn_c.create_vcn(CreateVcnDetails(compartment_id=TEN, cidr_blocks=["10.0.0.0/16"],
                                            display_name="proxy-vcn")).data
    time.sleep(5)
print("VCN:", vcn.id, flush=True)
rt_id = vcn.default_route_table_id
sl_id = vcn.default_security_list_id

# 3) internet gateway
igws = vcn_c.list_internet_gateways(compartment_id=TEN, vcn_id=vcn.id).data
igw = next((g for g in igws if g.display_name == "proxy-igw"), None)
if not igw:
    igw = vcn_c.create_internet_gateway(CreateInternetGatewayDetails(
        compartment_id=TEN, vcn_id=vcn.id, is_enabled=True,
        display_name="proxy-igw")).data
print("IGW:", igw.id, flush=True)

# 4) route table: 0.0.0.0/0 -> igw
vcn_c.update_route_table(rt_id, URT(route_rules=[
    RouteRule(destination="0.0.0.0/0", network_entity_id=igw.id)]))

# 5) security list: allow 8080 + 22
ing = []
for port in (8080, 22):
    ing.append(IngressSecurityRule(protocol="6", source="0.0.0.0/0",
                                   tcp_options=TcpOptions(destination_port_range=PortRange(min=port, max=port)),
                                   description=f"port {port}"))
vcn_c.update_security_list(sl_id, UpdateSecurityListDetails(ingress_security_rules=ing))
print("security list updated (8080/22)", flush=True)

# 6) subnet
subs = vcn_c.list_subnets(compartment_id=TEN, vcn_id=vcn.id).data
sub = next((s for s in subs if s.display_name == "proxy-subnet"), None)
if not sub:
    sub = vcn_c.create_subnet(CreateSubnetDetails(
        compartment_id=TEN, vcn_id=vcn.id, cidr_block="10.0.0.0/24",
        display_name="proxy-subnet", route_table_id=rt_id,
        security_list_ids=[sl_id], prohibit_public_ip_on_vnic=False)).data
print("SUBNET:", sub.id, flush=True)

# 7) Ubuntu 24.04 image for the A1 shape
images = comp_c.list_images(TEN, shape="VM.Standard.A1.Flex",
                            operating_system="Canonical Ubuntu",
                            operating_system_version="24.04").data
images.sort(key=lambda i: i.time_created, reverse=True)
img = images[0]
print("IMAGE:", img.display_name, flush=True)

# 8) instance (A1.Flex - retry smaller shapes: ARM capacity in Hyderabad
#    is often exhausted; 2 OCPU/12GB is still fully Always-Free and enough
#    for TF + worker + streamlit)
with open(r"C:\PrOxyTradingTerminal\.oracle\proxy_ed25519.pub") as fh:
    pubkey = fh.read().strip()
# smallest first: 1 OCPU/6GB has the best odds when capacity is scarce
SHAPES = [(1, 6), (2, 12), (4, 24)]
inst = None
import oci
for ocpus, mem in SHAPES:
    for attempt in range(5):
        try:
            inst = comp_c.launch_instance(LaunchInstanceDetails(
                compartment_id=TEN, availability_domain=AD, shape="VM.Standard.A1.Flex",
                shape_config=LaunchInstanceShapeConfigDetails(ocpus=ocpus, memory_in_gbs=mem),
                image_id=img.id, subnet_id=sub.id, display_name="proxy-terminal",
                metadata={"ssh_authorized_keys": pubkey},
                create_vnic_details=CreateVnicDetails(assign_public_ip=True))).data
            print(f"INSTANCE launched: {ocpus} OCPU/{mem}GB | {inst.id}", flush=True)
            break
        except oci.exceptions.ServiceError as e:
            if "capacity" in str(e.message).lower():
                print(f"  {ocpus} OCPU/{mem}GB attempt {attempt+1}: out of capacity - retrying in 45s", flush=True)
                time.sleep(45)
            else:
                print("  launch error:", str(e.message)[:200], flush=True)
                break
    if inst:
        break
if not inst:
    print("FAILED: ARM capacity exhausted at all sizes - run this script again later "
          "(or at a different hour; Oracle frees capacity periodically)", flush=True)
    sys.exit(2)
print("INSTANCE:", inst.id, "| state:", inst.lifecycle_state, flush=True)
for _ in range(60):
    time.sleep(10)
    st = comp_c.get_instance(inst.id).data
    if st.lifecycle_state in ("RUNNING", "STOPPED", "TERMINATED"):
        print("state:", st.lifecycle_state, flush=True)
        break

# 9) block volume 50GB + attach (idempotent)
vols = bs_c.list_volumes(compartment_id=TEN, availability_domain=AD).data
vol = next((v for v in vols if v.display_name == "proxy-data"), None)
if not vol:
    vol = bs_c.create_volume(CreateVolumeDetails(compartment_id=TEN, availability_domain=AD,
                                                 size_in_gbs=50, display_name="proxy-data")).data
for _ in range(40):
    time.sleep(10)
    vv = bs_c.get_volume(vol.id).data
    if vv.lifecycle_state == "AVAILABLE":
        break
# attach only if not already attached
atts = comp_c.list_volume_attachments(compartment_id=TEN, availability_domain=AD).data
if not any(a.volume_id == vol.id for a in atts):
    att = comp_c.attach_volume(AttachParavirtualizedVolumeDetails(
        instance_id=inst.id, volume_id=vol.id)).data
    print("VOLUME:", vol.id, "| attach:", att.lifecycle_state, flush=True)
else:
    print("VOLUME:", vol.id, "| already attached", flush=True)

# 10) public IP
vnis = comp_c.list_vnic_attachments(compartment_id=TEN, instance_id=inst.id).data
pub_ip = None
for va in vnis:
    vnic = comp_c.get_vnic(va.vnic_id).data
    if vnic.public_ip:
        pub_ip = vnic.public_ip
        break
print("PUBLIC_IP:", pub_ip, flush=True)
print("INSTANCE_OCID:", inst.id, flush=True)
