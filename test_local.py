#!/usr/bin/env python3
"""Offline checks: parse real captured messages, then run a full client flow.

The fake client speaks the same wire format as the game: connect, init, key
exchange, encrypted auth, master, game join and event relay between two actors.

Usage: python3 test_local.py [capture.pcap]
"""
import asyncio
import os
import socket
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import crypto  # noqa: E402
import enet  # noqa: E402
import photon  # noqa: E402
import protocol18 as p18  # noqa: E402
from protocol18 import Byte, StrDict  # noqa: E402


def check_roundtrip():
    values = [None, True, False, Byte(0), Byte(200), 0, 5, 300, -7, 1.5, "текст", b"\x01\x02",
              ["a", "b"], [0], [2, 300], {Byte(255): Byte(10), "k": True}, StrDict(Uuid="u", Token="t")]
    for value in values:
        parsed = p18.Reader(p18.write(value)).value()
        assert parsed == value, (value, parsed)
    params = {254: 2, 245: b"abc", 252: [1, 2]}
    assert p18.Reader(p18.write_parameters(params)).parameters() == params
    print("ok: protocol18 round trip")


def check_capture(path):
    import pcap_photon as P
    parsed = {}
    for _, proto, _, sp, _, dp, payload in P.packets(path):
        if proto != "udp" or not {sp, dp} & P.PHOTON_PORTS:
            continue
        for _, kind, body in P.photon_commands(payload):
            if kind not in (6, 7, 11) or len(body) < 3 or body[0] != 0xF3 or body[1] & 0x80:
                continue
            message = body[1] & 0x7F
            if message == 2:
                op, params = p18.parse_request(body)
                parsed[("op", op)] = parsed.get(("op", op), 0) + 1
                assert isinstance(params, dict)
            elif message == 4:
                params = p18.Reader(body, 3).parameters()
                parsed[("event", body[2])] = parsed.get(("event", body[2]), 0) + 1
    assert parsed, "no messages parsed"
    print(f"ok: decoded every plain message in capture: {sorted(parsed.items(), key=str)}")


def check_crypto():
    a, b = crypto.Session(), crypto.Session()
    a.derive(b.public)
    b.derive(a.public)
    assert a.key == b.key
    assert b.decrypt(a.encrypt(b"hello" * 20)) == b"hello" * 20
    print("ok: key exchange and AES")


def check_addresses():
    class Conn:
        def __init__(self, ip):
            self.addr = (ip, 1)
    server = photon.Server("name", photon.FUSION, "192.168.1.70", {}, "t", "100.105.69.177")
    assert server.address_for(Conn("192.168.1.5"), "master") == "192.168.1.70:27001"
    assert server.address_for(Conn("100.105.1.2"), "game") == "100.105.69.177:27002"
    print("ok: address chosen by client network")


class Client:
    def __init__(self, port):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False)
        self.sock.connect(("127.0.0.1", port))
        self.seq = {}
        self.in_seq = {}
        self.session = crypto.Session()
        self.peer = 0xFFFF
        self.loop = asyncio.get_running_loop()
        self.pending_messages = []

    def _send(self, *commands):
        head = struct.pack(">HBBII", self.peer, 0, len(commands), 1, 0x11223344)
        self.sock.send(head + b"".join(commands))

    def _cmd(self, kind, channel, flags, seq, body=b"", extra=b""):
        return struct.pack(">BBBBII", kind, channel, flags, 4, 12 + len(extra) + len(body), seq) + extra + body

    def _next(self, channel):
        self.seq[channel] = self.seq.get(channel, 0) + 1
        return self.seq[channel]

    def send(self, message):
        self._send(self._cmd(enet.RELIABLE, 0, 1, self._next(0), message))

    def send_unsequenced(self, message):
        self._send(self._cmd(enet.UNSEQUENCED, 0, 2, 0, message, struct.pack(">I", 1)))

    def send_encrypted(self, message):
        self.send(bytes([message[0], message[1] | 0x80]) + self.session.encrypt(message[2:]))

    async def recv(self, want=None):
        """Next message of the wanted type, handling transport commands."""
        for i, item in enumerate(self.pending_messages):
            if want is None or item[1] == want:
                return self.pending_messages.pop(i)
        while True:
            data = await asyncio.wait_for(self.loop.sock_recv(self.sock, 4096), 2)
            count = data[3]
            offset = 12
            for _ in range(count):
                kind, channel, flags, _, size, seq = struct.unpack_from(">BBBBII", data, offset)
                head = {enet.UNSEQUENCED: 16}.get(kind, 12)
                body = data[offset + head:offset + size]
                offset += size
                if flags & 1:
                    self._send(self._cmd(enet.ACK, channel, 0, 0, struct.pack(">II", seq, 1)))
                if kind == enet.VERIFY:
                    self.peer = struct.unpack_from(">H", body)[0]
                    return ("verify", None, body)
                if kind in (enet.RELIABLE, enet.UNSEQUENCED):
                    message = body
                    if message[1] & 0x80:
                        message = message[:2] + self.session.decrypt(message[2:])
                    item = ("msg", message[1] & 0x7F, message)
                    if want is None or item[1] == want:
                        return item
                    self.pending_messages.append(item)

    async def connect(self, encrypt=True):
        self._send(self._cmd(enet.CONNECT, enet.CONTROL_CHANNEL, 1, 1, bytes(2) + b"\x04\xb0" + bytes(28)))
        assert (await self.recv())[0] == "verify"
        self.send(bytes([p18.MAGIC, p18.INIT, 1, 8]) + bytes(37))
        await self.recv(p18.INIT_RESPONSE)
        if encrypt:
            self.send(bytes([p18.MAGIC, p18.INTERNAL_OPERATION, 0]) + p18.write_parameters({1: self.session.public}))
            _, _, message = await self.recv(p18.INTERNAL_OPERATION_RESPONSE)
            self.session.derive(p18.Reader(message, 6).parameters()[1])

    async def response(self, op):
        while True:
            _, _, message = await self.recv(p18.OPERATION_RESPONSE)
            if message[2] == op:
                code = struct.unpack_from("<h", message, 3)[0]
                return code, p18.Reader(message, 6).parameters()

    def op(self, code, params, encrypted=False):
        message = bytes([p18.MAGIC, p18.OPERATION, code]) + p18.write_parameters(params)
        self.send_encrypted(message) if encrypted else self.send(message)


async def enter(room, mode, public_port=photon.FUSION):
    name = Client(public_port["name"])
    await name.connect()
    name.op(photon.OP_AUTH, {224: "test", 220: "226575", 210: photon.REGION}, encrypted=True)
    code, auth = await name.response(photon.OP_AUTH)
    assert code == 0 and set(auth[photon.K_DATA]) == {"Uuid", "Token"}
    master = Client(public_port["master"])
    await master.connect()
    master.op(photon.OP_AUTH, {photon.K_SECRET: auth[photon.K_SECRET]})
    assert (await master.response(photon.OP_AUTH))[0] == 0
    master.op(photon.OP_JOIN, {photon.K_ROOM: room, photon.K_JOIN_MODE: Byte(mode)})
    code, joined = await master.response(photon.OP_JOIN)
    assert code == 0, code
    game = Client(public_port["game"])
    await game.connect()
    game.op(photon.OP_AUTH, {photon.K_SECRET: joined[photon.K_SECRET]})
    assert (await game.response(photon.OP_AUTH))[0] == 0
    game.op(photon.OP_JOIN, {photon.K_ROOM: room, photon.K_JOIN_MODE: Byte(mode),
                             photon.K_GAME_PROPS: {Byte(255): Byte(10), "mapname": "Polus Point"}})
    code, entered = await game.response(photon.OP_JOIN)
    assert code == 0
    return game, entered


async def check_flow():
    task = asyncio.create_task(photon.run("127.0.0.1", "127.0.0.1"))
    await asyncio.sleep(0.2)

    master = Client(photon.FUSION["master"])
    await master.connect()
    master.op(photon.OP_AUTH, {})
    await master.response(photon.OP_AUTH)
    master.op(photon.OP_JOIN, {photon.K_ROOM: "NOPE", photon.K_JOIN_MODE: Byte(0)})
    assert (await master.response(photon.OP_JOIN))[0] == photon.ERR_GAME_NOT_FOUND
    print("ok: joining a missing room is refused")

    host, host_room = await enter("ABCDEF", 1)
    assert host_room[photon.K_ACTOR] == 1
    guest, guest_room = await enter("ABCDEF", 0)
    assert guest_room[photon.K_ACTOR] == 2 and guest_room[photon.K_ACTOR_LIST] == [1, 2]
    assert guest_room[photon.K_GAME_PROPS]["mapname"] == "Polus Point"
    print("ok: host creates room, guest joins by code")

    payload = bytes(range(200)) * 7
    guest.op(photon.OP_RAISE, {photon.K_TARGETS: [1], photon.K_CODE: Byte(photon.RELAY_CODE), photon.K_DATA: payload[:1000]})
    while True:
        _, _, message = await host.recv(p18.EVENT)
        if message[2] == photon.RELAY_CODE:
            break
    event = p18.Reader(message, 3).parameters()
    assert event[photon.K_DATA] == payload[:1000] and event[photon.K_ACTOR] == 2
    print("ok: event 101 relayed with sender")

    raw = bytes([p18.MAGIC, p18.OPERATION, photon.OP_RAISE]) + p18.write_parameters(
        {photon.K_TARGETS: [2], photon.K_CODE: Byte(photon.RELAY_CODE), photon.K_DATA: b"unseq"})
    host.send_unsequenced(raw)
    while True:
        _, _, message = await guest.recv(p18.EVENT)
        if message[2] == photon.RELAY_CODE:
            break
    assert p18.Reader(message, 3).parameters()[photon.K_DATA] == b"unseq"
    print("ok: unsequenced relay")

    # Nested voice-style payload: object[] holding Dictionary<byte, object>.
    nested = bytes([p18.OBJECT_ARRAY, 1, p18.DICT, p18.BYTE, 0, 1, 7]) + p18.write(5)
    voice_op = bytes([p18.MAGIC, p18.OPERATION, photon.OP_RAISE, 2, photon.K_CODE]) + p18.write(Byte(203))
    guest.send(voice_op + bytes([photon.K_DATA]) + nested)
    while True:
        _, _, message = await host.recv(p18.EVENT)
        if message[2] == 203:
            break
    assert nested in message
    print("ok: nested data relayed byte for byte")

    join = bytes.fromhex("010601000000010000000800000001010100000000000000")
    host.op(photon.OP_RAISE, {photon.K_TARGETS: [0], photon.K_CODE: Byte(photon.PLUGIN_CODE), photon.K_DATA: join})
    kinds = []
    while len(kinds) < 2:
        _, _, message = await host.recv(p18.EVENT)
        if message[2] == photon.PLUGIN_CODE:
            kinds.append(p18.Reader(message, 3).parameters()[photon.K_DATA])
    assert len(kinds[0]) == 34 and kinds[0][:2] == b"\x01\x06" and kinds[0][14] == 2
    assert len(kinds[1]) == 25 and kinds[1][0] == 5
    snapshot = bytes.fromhex("0606010000000100000008000000" "44010000") + bytes(40)
    host.op(photon.OP_RAISE, {photon.K_TARGETS: [0], photon.K_CODE: Byte(photon.PLUGIN_CODE), photon.K_DATA: snapshot})
    while True:
        _, _, message = await host.recv(p18.EVENT)
        if message[2] == photon.PLUGIN_CODE:
            reply = p18.Reader(message, 3).parameters()[photon.K_DATA]
            break
    assert reply.hex() == "060601000000010000000d00000044010000000000000200000000000000000000000000000000000000"
    print("ok: plugin join, start and snapshot match the captured sizes")

    client_join = join[:14] + bytes([1, 1, 2]) + join[17:]
    guest.op(photon.OP_RAISE, {photon.K_TARGETS: [0], photon.K_CODE: Byte(photon.PLUGIN_CODE), photon.K_DATA: client_join})
    received = []
    while len(received) < 2:
        _, _, message = await guest.recv(p18.EVENT)
        if message[2] == photon.PLUGIN_CODE:
            received.append(p18.Reader(message, 3).parameters()[photon.K_DATA])
    assert received[0][14] == 2 and received[0][16] == 2
    assert received[1][0] == 5 and struct.unpack_from("<iI", received[1], 14) == (1, 4)
    print("ok: client gets Start right after join confirmation")

    task.cancel()


def main():
    check_roundtrip()
    check_crypto()
    check_addresses()
    if len(sys.argv) > 1:
        check_capture(sys.argv[1])
    asyncio.run(check_flow())
    print("all ok")


if __name__ == "__main__":
    main()
