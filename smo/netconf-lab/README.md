# netconf-lab

A real NETCONF-over-SSH server for exercising the SSH transport of RAN NF OAM (`PR-SB-1`): the `sysrepo/sysrepo-netopeer2` image
(Netopeer2 on port 830, user `netconf`, password `netconf`, a development image with a host key baked in) with one small model, `smo-lab`
(`smo-lab.yang`), and two cells of initial data (`smo-lab-data.xml`). `start.sh` installs the model and data before the server starts.

```bash
cd smo
scripts/init_secrets.sh && cp .env.example .env            # once
docker compose --profile netconf-lab up -d netconf-lab      # SSH on 127.0.0.1:8830
ssh-keyscan -p 8830 127.0.0.1 > /tmp/lab_known_hosts        # RAN NF OAM refuses a host key it does not know
NETCONF_SSH_KNOWN_HOSTS=/tmp/lab_known_hosts NETCONF_SSH_PASSWORD=netconf \
  PYTHONPATH=ran-nf-oam:shared python scripts/netconf_lab_check.py
```

`scripts/netconf_lab_check.py` connects with the session wrapper (`ran-nf-oam/app/netconf_ssh.py`), checks the `<hello>`, and reads the model's
data with `<get-config>`, then writes a value through the model path (`?model=smo-lab`), reads it back, restores it, and checks that an out-of-range value is refused with the server's reason. CI runs it in the job "NETCONF lab (netopeer2)" of `.github/workflows/smo-tests.yml`.

**NETCONF over TLS (PR-SB-2.5).** The server also listens on `127.0.0.1:6513` (RFC 7589). At start `tls.sh` makes a throwaway PKI in `netconf-lab/pki/` (a CA, a server certificate for 127.0.0.1, a client certificate; two days; git-ignored) and configures Netopeer2 as its own `example_configuration/tls_*.xml` does: server key and certificate in `ietf-keystore`, CA and client certificate in `ietf-truststore`, a TLS endpoint that requires a client certificate and maps it (SHA-256 fingerprint) to the user `netconf`. Register an endpoint with `transport: tls`, `adaptorUri` `tls://127.0.0.1:6513?model=smo-lab` and a `credentialRef` whose `NETCONF_CRED_<NAME>_CERT_FILE`, `_KEY_FILE` and `_CA_FILE` are `netconf-lab/pki/client.crt`, `client.key` and `ca.crt`. `scripts/netconf_lab_check.py` does this when `NETCONF_LAB_TLS_URI` is set, and CI runs it.

This is a lab, not part of the stack: it is only started by its profile, runs as root (the image's supervisor needs to) and publishes its port on
the loopback interface only. The image is pinned by digest like the others.
