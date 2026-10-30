#!/usr/bin/env python3
"""Private Photon replacement for Among Us 3D VR: Name, Master and Game servers.

Handles authentication, rooms, event relay and the Fusion plugin messages.

Usage: python3 photon.py --public <address reachable by the headsets>
"""
import argparse
import asyncio
import ipaddress
import logging
import secrets
import uuid

import crypto
import enet
import fusion_plugin
import protocol18 as p18
from protocol18 import Byte, StrDict

log = logging.getLogger("photon")

OP_JOIN_LOBBY, OP_CREATE, OP_JOIN = 229, 227, 226
OP_AUTH, OP_GET_REGIONS, OP_RAISE, OP_SET_PROPS, OP_LEAVE = 230, 220, 253, 248, 254
EV_JOIN, EV_LEAVE, EV_PROPS, EV_APP_STATS = 255, 254, 253, 226

K_ACTOR, K_ROOM, K_GAME_PROPS, K_ACTOR_PROPS, K_ACTOR_LIST = 254, 255, 248, 249, 252
K_ADDRESS, K_SECRET, K_USER, K_DATA, K_CLUSTER, K_MASTER = 230, 221, 225, 245, 196, 203
K_REGIONS, K_JOIN_MODE, K_CODE, K_TARGETS, K_PROPS, K_NICK = 210, 215, 244, 252, 251, 202
K_RECEIVERS = 246

ERR_GAME_NOT_FOUND, ERR_GAME_EXISTS = 32758, 32766

FUSION = {"name": 27000, "master": 27001, "game": 27002}
VOICE = {"name": 5058, "master": 5055, "game": 5056}
REGION = "eu"
RELAY_CODE, PLUGIN_CODE, FUSION_TARGET_PLUGIN = 101, 100, 0
PLUGIN_CHANNEL = 1
NETBIRD_RANGE = ipaddress.ip_network("100.64.0.0/10")
USER_NAMESPACE = uuid.UUID("6f2d1b1e-7c1a-4f4e-9a55-3d1c2b7e8a10")


class Room:
    def __init__(self, name, properties):
        self.name = name
        self.properties = properties
        self.actors = {}
        self.next_actor = 1
        self.master = 0
        self.plugin = fusion_plugin.Plugin(self.send_plugin)

    def send_plugin(self, actor, data):
        conn = self.actors.get(actor)
        if conn is not None:
            conn.send_reliable(p18.event(PLUGIN_CODE, {K_DATA: data}), PLUGIN_CHANNEL)

    def add(self, connection, properties):
        number = self.next_actor
        self.next_actor += 1
        self.actors[number] = connection
        connection.state.update(room=self, actor=number, props=properties or {})
        self.master = self.master or number
        return number

    def others(self, number):
        return [c for n, c in self.actors.items() if n != number]


class Server:
    """Shared message handling; one instance per listening port."""

    def __init__(self, role, ports, public, rooms, name, netbird=None):
        self.role = role
        self.ports = ports
        self.public = public
        self.netbird = netbird
        self.rooms = rooms
        self.name = name

    def address_for(self, conn, role):
        """Give each client this server's address on the network the client came from."""
        host = self.public
        if self.netbird and ipaddress.ip_address(conn.addr[0]) in NETBIRD_RANGE:
            host = self.netbird
        return f"{host}:{self.ports[role]}"

    def reply(self, conn, op, params, code=0, encrypted=False):
        message = p18.operation_response(op, params, code)
        self.send(conn, message, encrypted)

    def send(self, conn, message, encrypted=False):
        if encrypted and conn.state.get("crypto"):
            cipher = conn.state["crypto"].encrypt(message[2:])
            message = bytes([message[0], message[1] | 0x80]) + cipher
        conn.send_reliable(message, conn.state.get("channel", 0))

    def on_message(self, conn, message, reliable):
        if len(message) < 2 or message[0] != p18.MAGIC:
            log.warning("%s non-Photon message %s", self.name, message[:8].hex())
            return
        kind, encrypted = message[1] & 0x7F, bool(message[1] & 0x80)
        if encrypted:
            message = message[:2] + conn.state["crypto"].decrypt(message[2:])
        if kind == p18.INIT:
            conn.send_reliable(bytes([p18.MAGIC, p18.INIT_RESPONSE, 0]))
            log.info("%s init from %s", self.name, conn.addr)
        elif kind == p18.INTERNAL_OPERATION and message[2] == 0:
            self.key_exchange(conn, message)
        elif kind == p18.OPERATION:
            self.operation(conn, message, reliable)
        else:
            log.warning("%s unhandled message type %s", self.name, kind)

    def key_exchange(self, conn, message):
        _, params = p18.parse_request(message)
        session = crypto.Session()
        session.derive(params[1])
        response = p18.operation_response(0, {1: session.public}, kind=p18.INTERNAL_OPERATION_RESPONSE)
        conn.send_reliable(response)
        conn.state["crypto"] = session
        log.info("%s key exchange done", self.name)

    def operation(self, conn, message, reliable):
        try:
            op, params, raw = p18.parse_raw_request(message)
        except ValueError as error:
            log.warning("%s bad operation len=%s head=%s: %s", self.name, len(message), message[:4].hex(), error)
            return
        handler = getattr(self, f"op_{op}", None)
        if op != OP_RAISE:
            log.info("%s op %s keys=%s", self.name, op, sorted(params))
        if handler is None:
            log.warning("%s no handler for op %s", self.name, op)
            self.reply(conn, op, {})
            return
        if op == OP_RAISE:
            self.op_253(conn, params, reliable, raw)
            return
        handler(conn, params, reliable)

    def op_230(self, conn, params, reliable):
        if self.role == "name":
            user = str(uuid.uuid5(USER_NAMESPACE, str(params.get(K_USER, ""))))
            conn.state["user"] = user
            address = self.address_for(conn, "master")
            data = StrDict(Uuid=user, Token=secrets.token_urlsafe(32))
            self.reply(conn, OP_AUTH, {
                K_ADDRESS: address, K_USER: user, K_SECRET: secrets.token_urlsafe(24),
                K_CLUSTER: "default", K_DATA: data,
            }, encrypted=True)
            log.info("%s issued master address %s", self.name, address)
        elif self.role == "master":
            self.reply(conn, OP_AUTH, {K_USER: params.get(K_USER, ""), K_SECRET: secrets.token_urlsafe(24)}, encrypted=True)
            conn.send_reliable(p18.event(EV_APP_STATS, {227: 0, 228: 0, 229: 1}))
        else:
            self.reply(conn, OP_AUTH, {})

    def op_220(self, conn, params, reliable):
        self.reply(conn, OP_GET_REGIONS, {
            K_REGIONS: [REGION], K_ADDRESS: [self.address_for(conn, "master")],
        }, encrypted=True)

    def op_229(self, conn, params, reliable):
        self.reply(conn, OP_JOIN_LOBBY, {})

    def op_227(self, conn, params, reliable):
        self.op_226(conn, {**params, K_JOIN_MODE: Byte(1)}, reliable)

    def op_226(self, conn, params, reliable):
        name = params.get(K_ROOM) or secrets.token_hex(3).upper()
        mode = params.get(K_JOIN_MODE, 0)
        if self.role == "master":
            if name not in self.rooms and mode == 0:
                self.reply(conn, OP_JOIN, {}, code=ERR_GAME_NOT_FOUND, encrypted=True)
                log.info("%s join failed, no room %s", self.name, name)
                return
            address = self.address_for(conn, "game")
            self.reply(conn, OP_JOIN, {K_ADDRESS: address, K_ROOM: name, K_SECRET: secrets.token_urlsafe(24)}, encrypted=True)
            log.info("%s sent room %s to %s", self.name, name, address)
            return
        self.join_game(conn, name, params)

    def join_game(self, conn, name, params):
        room = self.rooms.get(name)
        if room is None:
            room = Room(name, params.get(K_GAME_PROPS, {}))
            self.rooms[name] = room
            log.info("%s created room %s", self.name, name)
        conn.state["channel"] = 0
        actor = room.add(conn, params.get(K_ACTOR_PROPS))
        actors = list(room.actors)
        all_props = {n: c.state["props"] for n, c in room.actors.items()}
        self.reply(conn, OP_JOIN, {
            K_ACTOR: actor, K_GAME_PROPS: room.properties, K_ACTOR_PROPS: all_props,
            K_ACTOR_LIST: actors, K_MASTER: room.master,
        }, encrypted=True)
        joined = p18.event(EV_JOIN, {K_ACTOR: actor, K_ACTOR_PROPS: conn.state["props"], K_ACTOR_LIST: actors})
        for target in room.actors.values():
            self.send(target, joined, encrypted=True)
        log.info("%s actor %s joined %s (%s total)", self.name, actor, name, len(actors))

    def op_253(self, conn, params, reliable, raw):
        room = conn.state.get("room")
        if room is None:
            return
        code, data = params.get(K_CODE), params.get(K_DATA)
        sender = conn.state["actor"]
        targets = params.get(K_TARGETS)
        if code == PLUGIN_CODE and targets == [FUSION_TARGET_PLUGIN]:
            room.plugin.on_message(sender, bytes(data))
            return
        if code == PLUGIN_CODE:
            log.warning("%s plugin code to targets %s from actor %s", self.name, targets, sender)
        message = p18.raw_event(code, {K_DATA: raw.get(K_DATA, p18.write(None)), K_ACTOR: p18.write(sender)})
        receivers = [room.actors[t] for t in targets if t in room.actors] if targets else room.others(sender)
        for target in receivers:
            if reliable:
                target.send_reliable(message, target.state.get("channel", 0))
            else:
                target.send_unsequenced(message)

    def op_248(self, conn, params, reliable):
        self.reply(conn, OP_SET_PROPS, {})
        room = conn.state.get("room")
        if room is None:
            return
        props = params.get(K_PROPS, {})
        room.properties.update(props)
        message = p18.event(EV_PROPS, {K_PROPS: props, K_ACTOR: conn.state["actor"]})
        for target in room.others(conn.state["actor"]):
            target.send_reliable(message)

    def op_254(self, conn, params, reliable):
        self.reply(conn, OP_LEAVE, {})
        self.leave(conn)

    def leave(self, conn):
        room = conn.state.pop("room", None)
        if room is None:
            return
        actor = conn.state["actor"]
        room.actors.pop(actor, None)
        if room.master == actor:
            room.master = min(room.actors, default=0)
        log.info("%s actor %s left %s", self.name, actor, room.name)
        if not room.actors:
            self.rooms.pop(room.name, None)
            return
        message = p18.event(EV_LEAVE, {K_ACTOR: actor, K_ACTOR_LIST: list(room.actors), K_MASTER: room.master})
        for target in room.actors.values():
            target.send_reliable(message)

    def on_close(self, conn):
        log.info("%s closed %s", self.name, conn.addr)
        self.leave(conn)


async def run(public, host, netbird=None):
    loop = asyncio.get_running_loop()
    listeners = []
    for label, ports in (("fusion", FUSION), ("voice", VOICE)):
        rooms = {}
        for role, port in ports.items():
            server = Server(role, ports, public, rooms, f"{label}-{role}", netbird)
            _, listener = await loop.create_datagram_endpoint(lambda s=server: enet.Listener(s), local_addr=(host, port))
            listeners.append(listener)
            log.info("listening on %s:%s as %s-%s", host, port, label, role)
    while True:
        await asyncio.sleep(0.1)
        for listener in listeners:
            listener.tick()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--public", required=True, help="address headsets use to reach this server")
    parser.add_argument("--netbird", help="this server's NetBird address, given to NetBird clients")
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    asyncio.run(run(args.public, args.host, args.netbird))


if __name__ == "__main__":
    main()
