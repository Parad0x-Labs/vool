#!/usr/bin/env bash
set -euo pipefail

KEY_PATH="${1:-$HOME/Desktop/vool-ssh/vool_do_ed25519_v2}"

if [[ ! -f "$KEY_PATH" ]]; then
  echo "ERROR: key not found: $KEY_PATH" >&2
  echo "Usage: bash ops/do_ip_first_bootstrap.sh /path/to/private_key" >&2
  exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TLS_DIR_LOCAL="$(mktemp -d /tmp/vool-ipfirst-tls.XXXXXX)"
# Example IPs (RFC 5737 TEST-NET-3, non-routable). Replace with your real node
# IPs before running — or export EU_IP/US_IP/APAC_IP/WATCH_IP in the environment.
EU_IP="${EU_IP:-203.0.113.11}"
US_IP="${US_IP:-203.0.113.12}"
APAC_IP="${APAC_IP:-203.0.113.13}"
WATCH_IP="${WATCH_IP:-203.0.113.14}"
NODES=("$EU_IP" "$US_IP" "$APAC_IP" "$WATCH_IP")
MESH_PSK_B64="${VOOL_MESH_PSK_B64:-}"
REMOTE_MESH_ENV=""
if [[ -n "$MESH_PSK_B64" ]]; then
  REMOTE_MESH_ENV="VOOL_MESH_PSK_B64='$MESH_PSK_B64'"
fi

cleanup() {
  rm -rf "$TLS_DIR_LOCAL"
}
trap cleanup EXIT

if ! command -v openssl >/dev/null 2>&1; then
  echo "ERROR: openssl is required locally to generate closed-test TLS certificates." >&2
  exit 1
fi

ssh_cmd=(ssh -i "$KEY_PATH" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new)
rsync_ssh=(ssh -i "$KEY_PATH" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new)

run_ssh() {
  local ip="$1"
  shift
  "${ssh_cmd[@]}" "root@${ip}" "$@"
}

generate_node_cert() {
  local common_name="$1"
  local ip_addr="$2"
  local base_name="$3"
  local key_path="$TLS_DIR_LOCAL/${base_name}-key.pem"
  local csr_path="$TLS_DIR_LOCAL/${base_name}.csr"
  local cert_path="$TLS_DIR_LOCAL/${base_name}-cert.pem"
  local ext_path="$TLS_DIR_LOCAL/${base_name}.ext"

  cat >"$ext_path" <<EOF
subjectAltName=IP:${ip_addr}
extendedKeyUsage=serverAuth
keyUsage=digitalSignature,keyEncipherment
EOF

  openssl req -new -nodes -newkey rsa:2048 \
    -keyout "$key_path" \
    -out "$csr_path" \
    -subj "/CN=${common_name}" >/dev/null 2>&1

  openssl x509 -req \
    -in "$csr_path" \
    -CA "$TLS_DIR_LOCAL/cluster-ca.pem" \
    -CAkey "$TLS_DIR_LOCAL/cluster-ca-key.pem" \
    -CAcreateserial \
    -out "$cert_path" \
    -days 825 \
    -sha256 \
    -extfile "$ext_path" >/dev/null 2>&1
}

echo "[1/9] SSH connectivity checks"
for ip in "${NODES[@]}"; do
  run_ssh "$ip" "echo connected:\$(hostname)"
done

echo "[2/9] Generate closed-test cluster CA and IP certificates"
openssl req -x509 -nodes -newkey rsa:4096 \
  -keyout "$TLS_DIR_LOCAL/cluster-ca-key.pem" \
  -out "$TLS_DIR_LOCAL/cluster-ca.pem" \
  -days 1825 \
  -subj "/CN=VOOL Closed Test Cluster CA" >/dev/null 2>&1
generate_node_cert "seed-eu-1" "$EU_IP" "seed-eu-1"
generate_node_cert "seed-us-1" "$US_IP" "seed-us-1"
generate_node_cert "seed-apac-1" "$APAC_IP" "seed-apac-1"
generate_node_cert "watch-edge-1" "$WATCH_IP" "watch-edge-1"

echo "[3/9] Sync repo to droplets"
for ip in "${NODES[@]}"; do
  rsync -az --delete \
    --exclude '.vool_local*' \
    --exclude 'storage/*.db' \
    --exclude 'data/keys/*' \
    --exclude '__pycache__/' \
    --exclude '.venv/' \
    -e "${rsync_ssh[*]}" \
    "${REPO_DIR}/" "root@${ip}:/opt/Decentralized_VOOL/"
done

echo "[4/9] Sync TLS bundle to droplets"
for ip in "${NODES[@]}"; do
  run_ssh "$ip" "mkdir -p /opt/Decentralized_VOOL/config/meet_clusters/do_ip_first_4node/tls"
done
rsync -az -e "${rsync_ssh[*]}" "$TLS_DIR_LOCAL/cluster-ca.pem" "root@${EU_IP}:/opt/Decentralized_VOOL/config/meet_clusters/do_ip_first_4node/tls/cluster-ca.pem"
rsync -az -e "${rsync_ssh[*]}" "$TLS_DIR_LOCAL/cluster-ca.pem" "root@${US_IP}:/opt/Decentralized_VOOL/config/meet_clusters/do_ip_first_4node/tls/cluster-ca.pem"
rsync -az -e "${rsync_ssh[*]}" "$TLS_DIR_LOCAL/cluster-ca.pem" "root@${APAC_IP}:/opt/Decentralized_VOOL/config/meet_clusters/do_ip_first_4node/tls/cluster-ca.pem"
rsync -az -e "${rsync_ssh[*]}" "$TLS_DIR_LOCAL/cluster-ca.pem" "root@${WATCH_IP}:/opt/Decentralized_VOOL/config/meet_clusters/do_ip_first_4node/tls/cluster-ca.pem"
rsync -az -e "${rsync_ssh[*]}" "$TLS_DIR_LOCAL/seed-eu-1-cert.pem" "$TLS_DIR_LOCAL/seed-eu-1-key.pem" "root@${EU_IP}:/opt/Decentralized_VOOL/config/meet_clusters/do_ip_first_4node/tls/"
rsync -az -e "${rsync_ssh[*]}" "$TLS_DIR_LOCAL/seed-us-1-cert.pem" "$TLS_DIR_LOCAL/seed-us-1-key.pem" "root@${US_IP}:/opt/Decentralized_VOOL/config/meet_clusters/do_ip_first_4node/tls/"
rsync -az -e "${rsync_ssh[*]}" "$TLS_DIR_LOCAL/seed-apac-1-cert.pem" "$TLS_DIR_LOCAL/seed-apac-1-key.pem" "root@${APAC_IP}:/opt/Decentralized_VOOL/config/meet_clusters/do_ip_first_4node/tls/"
rsync -az -e "${rsync_ssh[*]}" "$TLS_DIR_LOCAL/watch-edge-1-cert.pem" "$TLS_DIR_LOCAL/watch-edge-1-key.pem" "root@${WATCH_IP}:/opt/Decentralized_VOOL/config/meet_clusters/do_ip_first_4node/tls/"
for ip in "${NODES[@]}"; do
  run_ssh "$ip" "find /opt/Decentralized_VOOL/config/meet_clusters/do_ip_first_4node/tls -maxdepth 1 -name '*-key.pem' -exec chmod 600 {} + && find /opt/Decentralized_VOOL/config/meet_clusters/do_ip_first_4node/tls -maxdepth 1 \\( -name '*-cert.pem' -o -name 'cluster-ca.pem' \\) -exec chmod 644 {} +"
done

echo "[5/9] Install runtime dependencies"
for ip in "${NODES[@]}"; do
  run_ssh "$ip" "apt-get update && apt-get install -y python3 python3-venv rsync curl openssl && cd /opt/Decentralized_VOOL && python3 -m venv .venv && . .venv/bin/activate && pip install --upgrade pip && pip install -r requirements.txt"
done

echo "[6/9] Set strong meet auth tokens"
CLUSTER_MEET_TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
run_ssh "$EU_IP" "python3 -c \"import json; tok='${CLUSTER_MEET_TOKEN}'; p='/opt/Decentralized_VOOL/config/meet_clusters/do_ip_first_4node/seed-eu-1.json'; o=json.load(open(p)); o['auth_token']=tok; rc=dict(o.get('replication_config', {})); rc['auth_token']=tok; o['replication_config']=rc; json.dump(o,open(p,'w'),indent=2); print('EU token updated')\""
run_ssh "$US_IP" "python3 -c \"import json; tok='${CLUSTER_MEET_TOKEN}'; p='/opt/Decentralized_VOOL/config/meet_clusters/do_ip_first_4node/seed-us-1.json'; o=json.load(open(p)); o['auth_token']=tok; rc=dict(o.get('replication_config', {})); rc['auth_token']=tok; o['replication_config']=rc; json.dump(o,open(p,'w'),indent=2); print('US token updated')\""
run_ssh "$APAC_IP" "python3 -c \"import json; tok='${CLUSTER_MEET_TOKEN}'; p='/opt/Decentralized_VOOL/config/meet_clusters/do_ip_first_4node/seed-apac-1.json'; o=json.load(open(p)); o['auth_token']=tok; rc=dict(o.get('replication_config', {})); rc['auth_token']=tok; o['replication_config']=rc; json.dump(o,open(p,'w'),indent=2); print('APAC token updated')\""
run_ssh "$WATCH_IP" "python3 -c \"import json, pathlib; tok='${CLUSTER_MEET_TOKEN}'; p=pathlib.Path('/var/lib/vool/watch-edge-1/watch-edge-config.json'); p.parent.mkdir(parents=True, exist_ok=True); payload={'node_id':'watch-edge-1','public_url':'https://voolbook.com','bind_host':'127.0.0.1','bind_port':8788,'request_timeout_seconds':6,'upstream_base_urls':['https://${EU_IP}:8766','https://${US_IP}:8766','https://${APAC_IP}:8766'],'auth_tokens_by_base_url':{'https://${EU_IP}:8766':tok,'https://${US_IP}:8766':tok,'https://${APAC_IP}:8766':tok},'tls_insecure_skip_verify':True}; json.dump(payload,open(p,'w'),indent=2); print('Watch runtime config updated')\""

echo "[7/9] Start meet nodes"
run_ssh "$EU_IP" "python3 -c \"import os, signal, subprocess; needle='ops/run_meet_node_from_config.py --config config/meet_clusters/do_ip_first_4node/seed-eu-1.json'; ignore={os.getpid(), os.getppid()}; rows=subprocess.check_output(['ps','-eo','pid=,args='], text=True).splitlines(); [os.kill(int(parts[0]), signal.SIGTERM) for row in rows if needle in row and (parts:=row.strip().split(None, 1)) and int(parts[0]) not in ignore]; print('EU old process cleanup done')\"; mkdir -p /var/lib/vool/meet-eu-1 /var/log/vool; cd /opt/Decentralized_VOOL; nohup env PYTHONPATH=/opt/Decentralized_VOOL VOOL_HOME=/var/lib/vool/meet-eu-1 ${REMOTE_MESH_ENV} .venv/bin/python ops/run_meet_node_from_config.py --config config/meet_clusters/do_ip_first_4node/seed-eu-1.json >/var/log/vool/meet-eu-1.log 2>&1 </dev/null &"
run_ssh "$US_IP" "python3 -c \"import os, signal, subprocess; needle='ops/run_meet_node_from_config.py --config config/meet_clusters/do_ip_first_4node/seed-us-1.json'; ignore={os.getpid(), os.getppid()}; rows=subprocess.check_output(['ps','-eo','pid=,args='], text=True).splitlines(); [os.kill(int(parts[0]), signal.SIGTERM) for row in rows if needle in row and (parts:=row.strip().split(None, 1)) and int(parts[0]) not in ignore]; print('US old process cleanup done')\"; mkdir -p /var/lib/vool/meet-us-1 /var/log/vool; cd /opt/Decentralized_VOOL; nohup env PYTHONPATH=/opt/Decentralized_VOOL VOOL_HOME=/var/lib/vool/meet-us-1 ${REMOTE_MESH_ENV} .venv/bin/python ops/run_meet_node_from_config.py --config config/meet_clusters/do_ip_first_4node/seed-us-1.json >/var/log/vool/meet-us-1.log 2>&1 </dev/null &"
run_ssh "$APAC_IP" "python3 -c \"import os, signal, subprocess; needle='ops/run_meet_node_from_config.py --config config/meet_clusters/do_ip_first_4node/seed-apac-1.json'; ignore={os.getpid(), os.getppid()}; rows=subprocess.check_output(['ps','-eo','pid=,args='], text=True).splitlines(); [os.kill(int(parts[0]), signal.SIGTERM) for row in rows if needle in row and (parts:=row.strip().split(None, 1)) and int(parts[0]) not in ignore]; print('APAC old process cleanup done')\"; mkdir -p /var/lib/vool/meet-apac-1 /var/log/vool; cd /opt/Decentralized_VOOL; nohup env PYTHONPATH=/opt/Decentralized_VOOL VOOL_HOME=/var/lib/vool/meet-apac-1 ${REMOTE_MESH_ENV} .venv/bin/python ops/run_meet_node_from_config.py --config config/meet_clusters/do_ip_first_4node/seed-apac-1.json >/var/log/vool/meet-apac-1.log 2>&1 </dev/null &"

echo "[8/9] Start watch edge"
run_ssh "$WATCH_IP" "python3 -c \"import os, signal, subprocess; needle='run_brain_hive_watch_from_config.py'; ignore={os.getpid(), os.getppid()}; rows=subprocess.check_output(['ps','-eo','pid=,args='], text=True).splitlines(); [os.kill(int(parts[0]), signal.SIGTERM) for row in rows if needle in row and (parts:=row.strip().split(None, 1)) and int(parts[0]) not in ignore]; print('Watch old process cleanup done')\"; mkdir -p /var/lib/vool/watch-edge-1 /var/log/vool; cd /opt/Decentralized_VOOL; nohup env PYTHONPATH=/opt/Decentralized_VOOL VOOL_HOME=/var/lib/vool/watch-edge-1 ${REMOTE_MESH_ENV} .venv/bin/python /opt/Decentralized_VOOL/ops/run_brain_hive_watch_from_config.py --config /var/lib/vool/watch-edge-1/watch-edge-config.json >/var/log/vool/watch-edge-1.log 2>&1 </dev/null &"

echo "[9/9] Health checks"
sleep 3
curl --cacert "$TLS_DIR_LOCAL/cluster-ca.pem" -fsS "https://${EU_IP}:8766/v1/health" >/dev/null && echo "OK meet-eu health"
curl --cacert "$TLS_DIR_LOCAL/cluster-ca.pem" -fsS "https://${US_IP}:8766/v1/health" >/dev/null && echo "OK meet-us health"
curl --cacert "$TLS_DIR_LOCAL/cluster-ca.pem" -fsS "https://${APAC_IP}:8766/v1/health" >/dev/null && echo "OK meet-apac health"
run_ssh "$WATCH_IP" "curl -fsS http://127.0.0.1:8788/health >/dev/null && echo 'OK watch health'"
curl -fsS "https://voolbook.com/api/dashboard" >/dev/null && echo "OK public dashboard"

echo "Bootstrap complete."
echo "Watcher URL: https://voolbook.com/brain-hive"
