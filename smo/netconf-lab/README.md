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

This is a lab, not part of the stack: it is only started by its profile, runs as root (the image's supervisor needs to) and publishes its port on
the loopback interface only. The image is pinned by digest like the others.
