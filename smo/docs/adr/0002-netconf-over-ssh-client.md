# ADR 0002 — NETCONF over SSH: paramiko and a thin RFC 6242 layer

Status: accepted (`PR-SB-1.1`). Date: 2026-10-02.

## Context

`ran-nf-oam/app/netconf_client.py` sends RFC 6241 `<edit-config>` and `<get-config>` as XML over plain HTTP to a mock adaptor. A real
NF speaks NETCONF over SSH (RFC 6242: the `netconf` SSH subsystem, a `<hello>` exchange, then framed RPCs). Three libraries were
considered: ncclient, scrapli-netconf and asyncssh.

## Decision

1. **paramiko for the SSH session, our own ~150 lines for NETCONF framing and hello.** The framing (the `]]>]]>` end-of-message
   delimiter of base:1.0 and the chunked framing of base:1.1) is a page of code; the RPC builders and reply parsers already exist in
   `netconf_client.py` and are reused unchanged.
2. **Why not ncclient:** it is paramiko plus a large operations/capabilities layer we would not use, and it hides the framing and timeout
   behaviour we need to test and to map onto `NETCONF_TIMEOUT` / `NETCONF_UNREACHABLE` / `NETCONF_RPC_FAILED`.
3. **Why not scrapli-netconf:** it brings its own transport plugin system (system ssh, ssh2, paramiko, asyncssh), more surface for the same result.
4. **Why not asyncssh:** it is asyncio only. Every route in `ran-nf-oam` is synchronous and dispatches with blocking calls inside the request thread
   (`HISTORY.md` ST-9), so an async client would need a loop bridge per call. paramiko is blocking and fits the existing code.
5. **Host keys are checked, with no escape hatch.** A connection is refused unless the host key is in the file named by `NETCONF_SSH_KNOWN_HOSTS`;
   there is no trust-on-first-use and no switch to skip the check (CodeQL flags `AutoAddPolicy`, rightly). The lab compose profile (`SB-1.4`)
   writes a known_hosts file for its own server instead.
6. **Credentials are a stopgap until `SB-2`:** the user name is part of the registered `ssh://user@host:port` URI; the password
   (`NETCONF_SSH_PASSWORD`, `*_FILE` convention, `smo_shared/secretfile.py`) or a private key file (`NETCONF_SSH_KEY_FILE`) comes
   from the module's environment. `SB-2.1`/`2.2` replace this with a per-endpoint `credential_ref`.
7. **Selection by an explicit `transport` column** on `o1_adaptor_endpoint` (`http-mock` default, `ssh`), not by guessing from the
   URI scheme; registration refuses a URI whose scheme does not match the transport.

## Consequences

One new direct dependency (`paramiko`, with `bcrypt`, `pynacl`, `invoke` as its closure) in the hashed lock. The session wrapper is
tested against an in-process paramiko server, so it runs in the unit suite with no container; a real server (netopeer2) is exercised by
the `netconf-lab` compose profile (`SB-1.4`).
