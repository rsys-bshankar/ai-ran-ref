"""An in-process NETCONF-over-SSH server for tests (paramiko server side, RFC 6242 framing), so the SSH session wrapper is exercised
without a container. `behaviour` picks what it does: capabilities offered in its hello, whether it answers, what it answers."""

import re
import socket
import threading
import time

import paramiko

EOM = b"]]>]]>"
NS = "urn:ietf:params:xml:ns:netconf:base:1.0"


def hello(caps: list[str]) -> bytes:
    body = "".join(f"<capability>{c}</capability>" for c in caps)
    return f'<hello xmlns="{NS}"><capabilities>{body}</capabilities></hello>'.encode() + EOM


class Behaviour:
    def __init__(self, caps=("urn:ietf:params:netconf:base:1.0", "urn:ietf:params:netconf:base:1.1"), silent=False,
                 edit_reply="<ok/>", data="<managed-object ref='ME-1'><administrativeState>UNLOCKED</administrativeState></managed-object>",
                 hello_text=None, close_after_hello=False, split=False, step_replies=None, edit_replies=None):
        self.caps, self.silent, self.edit_reply, self.data = list(caps), silent, edit_reply, data
        self.hello_text, self.close_after_hello, self.split = hello_text, close_after_hello, split
        self.step_replies = step_replies or {}            # candidate steps ('lock', 'commit', 'discard', 'unlock') -> the inner reply to give
        self.edit_replies = list(edit_replies or [])      # one inner reply per edit-config, in order; once used up `edit_reply` applies
        self.received: list[str] = []


class _Server(paramiko.ServerInterface):
    def __init__(self, password, no_subsystem):
        self.password, self.no_subsystem = password, no_subsystem

    def check_auth_password(self, username, password):
        ok = username == "netconf" and password == self.password
        return paramiko.AUTH_SUCCESSFUL if ok else paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return "password"

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_subsystem_request(self, channel, name):
        return name == "netconf" and not self.no_subsystem and super().check_channel_subsystem_request(channel, name)


class NetconfTestServer:
    def __init__(self, behaviour: Behaviour | None = None, password="secret", no_subsystem=False):
        self.behaviour = behaviour or Behaviour()
        self.password, self.no_subsystem = password, no_subsystem
        self.host_key = paramiko.RSAKey.generate(2048)
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(5)
        self.port = self._sock.getsockname()[1]
        self._running = True
        self._threads: list[threading.Thread] = []
        threading.Thread(target=self._accept, daemon=True).start()

    @property
    def uri(self) -> str:
        return f"ssh://netconf@127.0.0.1:{self.port}"

    def known_hosts_line(self) -> str:
        return f"[127.0.0.1]:{self.port} {self.host_key.get_name()} {self.host_key.get_base64()}\n"

    def close(self):
        self._running = False
        self._sock.close()

    def _accept(self):
        while self._running:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        transport = paramiko.Transport(conn)
        transport.add_server_key(self.host_key)
        behaviour = self.behaviour

        class Handler(paramiko.SubsystemHandler):
            def start_subsystem(self, name, transport, channel):
                _converse(channel, behaviour)

        transport.set_subsystem_handler("netconf", Handler)
        try:
            transport.start_server(server=_Server(self.password, self.no_subsystem))
            transport.join(30)
        except Exception:                                       # noqa: BLE001 - a client that hangs up mid-handshake is fine
            pass


def _read_until(channel, buffer, marker):
    while marker not in buffer:
        data = channel.recv(65536)
        if not data:
            return None, b""
        buffer += data
    message, _, rest = buffer.partition(marker)
    return message, rest


def _converse(channel, b: Behaviour):
    if b.hello_text is not None:
        channel.sendall(b.hello_text.encode() + EOM)
    else:
        channel.sendall(hello(b.caps))
    if b.close_after_hello:
        time.sleep(0.3)             # let the client finish its subsystem request first: closing at once races it into a different error
        channel.close()
        return
    chunked = "urn:ietf:params:netconf:base:1.1" in b.caps
    buffer = b""
    message, buffer = _read_until(channel, buffer, EOM)             # the client hello
    if message is None:
        return
    while True:
        if chunked:
            while not re.match(rb"\n#\d+\n", buffer):
                data = channel.recv(65536)
                if not data:
                    return
                buffer += data
            m = re.match(rb"\n#(\d+)\n", buffer)
            size = int(m.group(1))
            buffer = buffer[m.end():]
            while len(buffer) < size + 4:
                data = channel.recv(65536)
                if not data:
                    return
                buffer += data
            request, buffer = buffer[:size], buffer[size + 4:]      # + "\n##\n"
        else:
            request, buffer = _read_until(channel, buffer, EOM)
            if request is None:
                return
        text = request.decode()
        b.received.append(text)
        if b.silent:
            continue
        mid = re.search(r'message-id="([^"]*)"', text).group(1)
        step = re.search(r'message-id="[^"]*-(lock|commit|discard|unlock)"', text)
        if step and step.group(1) in b.step_replies:
            inner = b.step_replies[step.group(1)]
        elif "<edit-config>" in text:
            inner = b.edit_replies.pop(0) if b.edit_replies else b.edit_reply
        elif "<get-config>" in text:
            inner = f"<data>{b.data}</data>"
        else:
            inner = "<ok/>"
        reply = f'<rpc-reply xmlns="{NS}" message-id="{mid}">{inner}</rpc-reply>'.encode()
        if chunked:
            parts = [reply[:10], reply[10:]] if b.split else [reply]
            out = b"".join(b"\n#%d\n%s" % (len(p), p) for p in parts) + b"\n##\n"
        else:
            out = reply + EOM
        if b.split:
            channel.sendall(out[:7])
            channel.sendall(out[7:])
        else:
            channel.sendall(out)
