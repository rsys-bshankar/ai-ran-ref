#!/bin/sh
# Container start for the netconf-lab profile: install the lab model and its initial data, then run the image's own supervisor
# (sysrepo + the Netopeer2 server on port 830). Installing before the server starts means no restart is needed.
set -eu
sysrepoctl --install /lab/smo-lab.yang
sysrepocfg --import=/lab/smo-lab-data.xml --datastore=startup --module=smo-lab
sysrepocfg --import=/lab/smo-lab-data.xml --datastore=running --module=smo-lab
# The image's NACM (RFC 8341) refuses writes by any user but the recovery session, so the lab user would be refused every edit-config
# (PR-SB-1.6 saw `access-denied ... NACM authorization failed`). This is a lab: reads, writes and RPCs are permitted by default.
sysrepocfg --import=/lab/nacm.xml --datastore=startup --module=ietf-netconf-acm
sysrepocfg --import=/lab/nacm.xml --datastore=running --module=ietf-netconf-acm
# PR-SB-2.5: a TLS listener on 6513 with a throwaway PKI (netconf-lab/tls.sh); /pki carries the client files out
. /lab/tls.sh
exec /usr/bin/supervisord -c /etc/supervisord.conf
