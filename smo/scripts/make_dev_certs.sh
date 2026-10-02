#!/usr/bin/env bash
# Makes a development CA and a server certificate for the TLS edge (PR-SEC-1.1).
#
#   scripts/make_dev_certs.sh [--force] [DIR]          DIR defaults to smo/certs (git-ignored)
#   SMO_TLS_NAMES="smo.example.test,10.0.0.5" scripts/make_dev_certs.sh    extra names for the certificate
#
# Writes, in DIR:
#   ca.crt, ca.key       the development CA. Trust ca.crt in your browser or pass it to curl (--cacert); keep
#                        ca.key to yourself: it can sign a certificate for any site your machine trusts it for.
#   server.crt/.key      the edge's certificate (valid 365 days), signed by the CA, for localhost, 127.0.0.1, ::1,
#                        r1-termination, gui and any SMO_TLS_NAMES (DNS names or IP addresses).
#
# An existing set is kept unless --force. Not for production: use a certificate from your own CA or a public one,
# mounted the same way (docs/ARCHITECTURE.md, TLS at the edge). ECDSA P-256 keys, so generation takes milliseconds.
#
# server.crt and server.key are mode 0644 so the unprivileged nginx (uid 101) can read them as Compose secret
# files; the directory is 0700, which keeps other accounts on this machine out.
set -euo pipefail

force=no
dir=""
for arg in "$@"; do
  case "$arg" in
    --force) force=yes ;;
    -h|--help) sed -n '2,17p' "${BASH_SOURCE[0]}"; exit 0 ;;
    -*) echo "unknown option: $arg" >&2; exit 2 ;;
    *) dir="$arg" ;;
  esac
done
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
dir="${dir:-$here/certs}"

umask 077
mkdir -p "$dir"
chmod 700 "$dir"
if [ "$force" = no ] && [ -s "$dir/ca.crt" ] && [ -s "$dir/server.crt" ] && [ -s "$dir/server.key" ]; then
  echo "kept: $dir (already has a CA and a server certificate; --force makes new ones)"
  exit 0
fi

san="DNS:localhost,DNS:r1-termination,DNS:gui,IP:127.0.0.1,IP:::1"
IFS=',' read -r -a extra <<< "${SMO_TLS_NAMES:-}"
for name in "${extra[@]:-}"; do
  [ -n "$name" ] || continue
  if [[ "$name" =~ ^[0-9.]+$ || "$name" == *:* ]]; then san="$san,IP:$name"; else san="$san,DNS:$name"; fi
done

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

openssl ecparam -name prime256v1 -genkey -noout -out "$dir/ca.key" 2>/dev/null
openssl req -x509 -new -key "$dir/ca.key" -sha256 -days 3650 -subj "/O=AI-RAN SMO development/CN=AI-RAN SMO development CA" \
  -addext "basicConstraints=critical,CA:TRUE,pathlen:0" -addext "keyUsage=critical,keyCertSign,cRLSign" -out "$dir/ca.crt"

openssl ecparam -name prime256v1 -genkey -noout -out "$dir/server.key" 2>/dev/null
openssl req -new -key "$dir/server.key" -subj "/O=AI-RAN SMO development/CN=localhost" -out "$work/server.csr"
cat > "$work/server.ext" <<EXT
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature
extendedKeyUsage=serverAuth
subjectAltName=$san
EXT
openssl x509 -req -in "$work/server.csr" -CA "$dir/ca.crt" -CAkey "$dir/ca.key" -CAcreateserial -CAserial "$work/ca.srl" \
  -sha256 -days 365 -extfile "$work/server.ext" -out "$dir/server.crt" 2>/dev/null

chmod 600 "$dir/ca.key"
chmod 644 "$dir/ca.crt" "$dir/server.crt" "$dir/server.key"
echo "created: $dir/ca.crt  $dir/server.crt  $dir/server.key  (names: $san)"
