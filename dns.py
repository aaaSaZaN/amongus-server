#!/usr/bin/env python3
"""Tiny DNS server: answers one name with the private server's address.

Peers reach it through a NetBird nameserver entry limited to that name, so
every other lookup keeps going to their normal DNS.

Usage: python3 dns.py --bind <ip> --port 5300 --answer <server ip>
"""
import argparse
import asyncio
import logging
import socket
import struct

log = logging.getLogger("dns")

NAME = "ns.photonengine.io"
TYPE_A = 1
TTL = 30
FLAGS_OK, FLAGS_REFUSED = 0x8180, 0x8185
POINTER_TO_QUESTION = 0xC00C


def question(packet):
    labels = []
    offset = 12
    while packet[offset]:
        size = packet[offset]
        labels.append(packet[offset + 1:offset + 1 + size].decode("ascii", "replace"))
        offset += 1 + size
    offset += 1
    kind = struct.unpack_from(">H", packet, offset)[0]
    return ".".join(labels).lower(), kind, packet[12:offset + 4]


def respond(packet, answer):
    query_id = packet[:2]
    name, kind, section = question(packet)
    if name != NAME:
        log.info("refused %s", name)
        return query_id + struct.pack(">HHHHH", FLAGS_REFUSED, 1, 0, 0, 0) + section
    log.info("answered %s type %s", name, kind)
    if kind != TYPE_A:
        return query_id + struct.pack(">HHHHH", FLAGS_OK, 1, 0, 0, 0) + section
    record = struct.pack(">HHHIH", POINTER_TO_QUESTION, TYPE_A, 1, TTL, 4) + socket.inet_aton(answer)
    return query_id + struct.pack(">HHHHH", FLAGS_OK, 1, 1, 0, 0) + section + record


class Server(asyncio.DatagramProtocol):
    def __init__(self, answer):
        self.answer = answer
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if len(data) >= 12:
            self.transport.sendto(respond(data, self.answer), addr)


async def run(bind, port, answer):
    loop = asyncio.get_running_loop()
    await loop.create_datagram_endpoint(lambda: Server(answer), local_addr=(bind, port))
    log.info("dns on %s:%s, %s -> %s", bind, port, NAME, answer)
    await asyncio.Event().wait()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", required=True)
    parser.add_argument("--port", type=int, default=5300)
    parser.add_argument("--answer", required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    asyncio.run(run(args.bind, args.port, args.answer))


if __name__ == "__main__":
    main()
