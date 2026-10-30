"""Replacement for Photon's closed-source FusionPlugin.

Message layout, little-endian: type byte, protocol byte, three-int serialization
version, then fields. Layouts were read from captures of the real plugin and the
Fusion.Protocol classes.
"""
import logging
import secrets
import struct

log = logging.getLogger("fusion")

MSG_JOIN, MSG_REFLEXIVE, MSG_START, MSG_SNAPSHOT = 1, 3, 5, 6
PROTOCOL = 6
SERVER_VERSION = (1, 1, 13)
HEADER_SIZE = 14
JOIN_REQUEST, JOIN_CONFIRM = 1, 2
PEER_SERVER, PEER_CLIENT = 1, 2
REQUEST_REFLEXIVE = 4
START_WAIT_REFLEXIVE = 4
SNAPSHOT_CONFIRM = 2
UNIQUE_ID_SIZE = 8
ARRAY_HEADER = b"\x11\x00"  # bit 0: present, remaining bits: length 8
TAIL = bytes(3)
ADDRESS_SIZE = 24
NAT_UNKNOWN = 0x08
REFLEXIVE_TAIL = b"\x01\x00\x00"
SNAPSHOT_REPLY_PADDING = 16


def header(kind):
    return bytes([kind, PROTOCOL]) + struct.pack("<III", *SERVER_VERSION)


def join_confirm(game_mode, peer_mode, unique_id):
    body = bytes([JOIN_CONFIRM, game_mode, peer_mode])
    body += struct.pack("<I", REQUEST_REFLEXIVE) + ARRAY_HEADER + unique_id + TAIL
    return header(MSG_JOIN) + body


def start(server_actor, requests):
    return header(MSG_START) + struct.pack("<iI", server_actor, requests) + TAIL


def reflexive(actor, addresses, unique_id):
    body = struct.pack("<i", actor) + addresses + bytes([NAT_UNKNOWN])
    return header(MSG_REFLEXIVE) + body + ARRAY_HEADER + unique_id + REFLEXIVE_TAIL


def snapshot_confirm(tick):
    body = struct.pack("<iII", tick, 0, SNAPSHOT_CONFIRM) + bytes(SNAPSHOT_REPLY_PADDING)
    return header(MSG_SNAPSHOT) + body


class Plugin:
    """Fusion session state for one room. `send(actor, message)` delivers event 100."""

    def __init__(self, send):
        self.send = send
        self.unique_ids = {}
        self.addresses = {}
        self.server_actor = 0
        self.snapshot = None

    def on_message(self, actor, data):
        kind = data[0]
        if kind == MSG_JOIN:
            self.on_join(actor, data)
        elif kind == MSG_REFLEXIVE:
            self.on_reflexive(actor, data)
        elif kind == MSG_SNAPSHOT:
            self.on_snapshot(actor, data)
        else:
            log.warning("unhandled plugin message from actor %s: %s", actor, data[:64].hex())

    def on_join(self, actor, data):
        kind, game_mode, peer_mode = data[HEADER_SIZE:HEADER_SIZE + 3]
        if kind != JOIN_REQUEST:
            log.warning("unexpected join type %s", kind)
            return
        unique_id = secrets.token_bytes(UNIQUE_ID_SIZE)
        self.unique_ids[actor] = unique_id
        if peer_mode == PEER_SERVER:
            self.server_actor = actor
        self.send(actor, join_confirm(game_mode, peer_mode, unique_id))
        log.info("actor %s join confirmed, mode=%s peer=%s", actor, game_mode, peer_mode)
        if peer_mode == PEER_SERVER:
            self.send(actor, start(actor, 0))
        else:
            # The client sends its addresses only after Start, so Start cannot wait for them.
            self.send(actor, start(self.server_actor, START_WAIT_REFLEXIVE))

    def on_reflexive(self, actor, data):
        start_at = HEADER_SIZE + 4
        self.addresses[actor] = data[start_at:start_at + ADDRESS_SIZE * 2]
        log.info("actor %s sent addresses", actor)
        self.exchange_addresses(actor)

    def exchange_addresses(self, actor):
        server = self.server_actor
        if actor == server or server not in self.addresses or actor not in self.addresses:
            return
        self.send(server, reflexive(actor, self.addresses[actor], self.unique_ids[actor]))
        self.send(actor, reflexive(server, self.addresses[server], self.unique_ids[server]))
        log.info("addresses exchanged between actor %s and host actor %s", actor, server)

    def on_snapshot(self, actor, data):
        tick = struct.unpack_from("<i", data, HEADER_SIZE)[0]
        self.snapshot = (tick, data)
        self.send(actor, snapshot_confirm(tick))
        log.info("snapshot tick %s, %s bytes", tick, len(data))
