#!/bin/sh
# Container start for the netconf-lab profile: install the lab model and its initial data, then run the image's own supervisor
# (sysrepo + the Netopeer2 server on port 830). Installing before the server starts means no restart is needed.
set -eu
sysrepoctl --install /lab/smo-lab.yang
sysrepocfg --import=/lab/smo-lab-data.xml --datastore=startup --module=smo-lab
sysrepocfg --import=/lab/smo-lab-data.xml --datastore=running --module=smo-lab
exec /usr/bin/supervisord -c /etc/supervisord.conf
