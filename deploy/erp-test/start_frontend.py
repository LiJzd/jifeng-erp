import ipaddress
import os
from pathlib import Path

address = str(ipaddress.IPv4Address(os.environ["PUBLIC_IP"]))
template = Path("/opt/jifeng-deploy/nginx-tls.conf.template").read_text()
Path("/tmp/jifeng-nginx.conf").write_text(template.replace("__PUBLIC_IP__", address))
os.execvp("nginx", ["nginx", "-c", "/tmp/jifeng-nginx.conf", "-g", "daemon off;"])
