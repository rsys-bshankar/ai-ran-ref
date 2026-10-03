#!/bin/sh
# PR-SB-2.5: a NETCONF-over-TLS listener (RFC 7589, port 6513) for the netconf-lab server. Sourced by start.sh before the server starts.
#
# Makes a throwaway PKI in /pki (a CA, a server certificate for 127.0.0.1, a client certificate signed by that CA; valid for two days), then
# configures Netopeer2 the way its own example_configuration/tls_*.xml does: the server key and certificate in ietf-keystore, the CA and the client
# certificate in ietf-truststore, and a TLS endpoint in ietf-netconf-server that requires a client certificate and maps it to the NETCONF user
# `netconf` (cert-to-name, a SHA-256 fingerprint). The files are merged (`--edit`), never imported over the module, so the SSH host key in the same
# keystore and the SSH endpoint are left alone. /pki is a host directory (compose mounts ./netconf-lab/pki) so the client files reach the caller.
set -eu
PKI=${PKI:-/pki}
mkdir -p "$PKI"
cd "$PKI"
openssl req -x509 -newkey rsa:2048 -nodes -keyout ca.key -out ca.crt -days 2 -subj "/CN=smo-lab-ca" 2>/dev/null
issue() {  # name, extendedKeyUsage, subjectAltName
  openssl req -newkey rsa:2048 -nodes -keyout "$1.key" -out "$1.csr" -subj "/CN=$1" 2>/dev/null
  printf 'extendedKeyUsage=%s\n%s\n' "$2" "$3" > "$1.ext"
  openssl x509 -req -in "$1.csr" -CA ca.crt -CAkey ca.key -CAcreateserial -out "$1.crt" -days 2 -extfile "$1.ext" 2>/dev/null
}
issue server serverAuth "subjectAltName=IP:127.0.0.1"
issue client clientAuth "subjectAltName=DNS:client"
chmod 644 ./*.key ./*.crt                                  # a lab: the caller's user must read the client key

der() { openssl x509 -in "$1" -outform DER | base64 -w0; }
priv=$(openssl pkcs8 -topk8 -nocrypt -in server.key -outform DER | base64 -w0)
pub=$(openssl rsa -in server.key -pubout -outform DER 2>/dev/null | base64 -w0)
fp="04:$(openssl x509 -in client.crt -noout -fingerprint -sha256 | cut -d= -f2)"          # 04 = SHA-256 in the x509c2n fingerprint format

cat > /tmp/tls_keystore.xml <<EOF
<keystore xmlns="urn:ietf:params:xml:ns:yang:ietf-keystore">
  <asymmetric-keys>
    <asymmetric-key>
      <name>serverkey</name>
      <algorithm>rsa2048</algorithm>
      <public-key>$pub</public-key>
      <private-key>$priv</private-key>
      <certificates>
        <certificate>
          <name>servercert</name>
          <cert>$(der server.crt)</cert>
        </certificate>
      </certificates>
    </asymmetric-key>
  </asymmetric-keys>
</keystore>
EOF
cat > /tmp/tls_truststore.xml <<EOF
<truststore xmlns="urn:ietf:params:xml:ns:yang:ietf-truststore">
  <certificates>
    <name>clientcerts</name>
    <certificate>
      <name>clientcert</name>
      <cert>$(der client.crt)</cert>
    </certificate>
  </certificates>
  <certificates>
    <name>cacerts</name>
    <certificate>
      <name>cacert</name>
      <cert>$(der ca.crt)</cert>
    </certificate>
  </certificates>
</truststore>
EOF
cat > /tmp/tls_listen.xml <<EOF
<netconf-server xmlns="urn:ietf:params:xml:ns:yang:ietf-netconf-server">
  <listen>
    <endpoint>
      <name>smo-lab-tls</name>
      <tls>
        <tcp-server-parameters>
          <local-address>0.0.0.0</local-address>
          <local-port>6513</local-port>
        </tcp-server-parameters>
        <tls-server-parameters>
          <server-identity>
            <keystore-reference>
              <asymmetric-key>serverkey</asymmetric-key>
              <certificate>servercert</certificate>
            </keystore-reference>
          </server-identity>
          <client-authentication>
            <required/>
            <ca-certs>cacerts</ca-certs>
            <client-certs>clientcerts</client-certs>
            <cert-maps>
              <cert-to-name>
                <id>1</id>
                <fingerprint>$fp</fingerprint>
                <map-type xmlns:x509c2n="urn:ietf:params:xml:ns:yang:ietf-x509-cert-to-name">x509c2n:specified</map-type>
                <name>netconf</name>
              </cert-to-name>
            </cert-maps>
          </client-authentication>
        </tls-server-parameters>
      </tls>
    </endpoint>
  </listen>
</netconf-server>
EOF
for ds in startup running; do
  sysrepocfg --edit=/tmp/tls_keystore.xml --datastore=$ds --module=ietf-keystore
  sysrepocfg --edit=/tmp/tls_truststore.xml --datastore=$ds --module=ietf-truststore
  sysrepocfg --edit=/tmp/tls_listen.xml --datastore=$ds --module=ietf-netconf-server
done
rm -f /tmp/tls_keystore.xml /tmp/tls_truststore.xml /tmp/tls_listen.xml ca.key ca.srl ./*.csr ./*.ext server.key   # keep only what the caller needs
