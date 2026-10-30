"""Photon's UDP transport: connections, acknowledgements, fragments, resends."""
import asyncio
import secrets
import struct
import time

ACK, CONNECT, VERIFY, DISCONNECT, PING, RELIABLE, UNRELIABLE, FRAGMENT = 1, 2, 3, 4, 5, 6, 7, 8
UNSEQUENCED, SERVER_TIME = 11, 12
CONTROL_CHANNEL = 255
FLAG_RELIABLE, FLAG_UNSEQUENCED = 1, 2
FRAGMENT_SIZE = 1152
RESEND_AFTER = 0.3
GIVE_UP_AFTER = 20.0
IDLE_TIMEOUT = 60.0
PING_PACKET_SIZE = 13
PING_PREFIX = b"\x7d" * 12


def now_ms():
    return int(time.monotonic() * 1000) & 0xFFFFFFFF


class Connection:
    """One client on one port."""

    def __init__(self, transport, addr, challenge, handler):
        self.transport = transport
        self.addr = addr
        self.challenge = challenge
        self.handler = handler
        self.peer_id = secrets.randbelow(0xFFFE) + 1
        self.out_seq = {}
        self.out_group = {}
        self.in_expected = {}
        self.in_buffer = {}
        self.fragments = {}
        self.pending = {}
        self.last_seen = time.monotonic()
        self.closed = False
        self.state = {}

    def _packet(self, *commands):
        head = struct.pack(">HBBII", 0, 0, len(commands), now_ms(), self.challenge)
        self.transport.sendto(head + b"".join(commands), self.addr)

    def _command(self, kind, channel, flags, seq, body=b"", extra=b""):
        size = 12 + len(extra) + len(body)
        return struct.pack(">BBBBII", kind, channel, flags, 0, size, seq) + extra + body

    def _next_seq(self, channel):
        seq = self.out_seq.get(channel, 0) + 1
        self.out_seq[channel] = seq
        return seq

    def _send_tracked(self, channel, seq, command):
        self.pending[(channel, seq)] = [command, time.monotonic(), time.monotonic()]
        self._packet(command)

    def send_reliable(self, message, channel=0):
        if len(message) <= FRAGMENT_SIZE:
            seq = self._next_seq(channel)
            self._send_tracked(channel, seq, self._command(RELIABLE, channel, FLAG_RELIABLE, seq, message))
            return
        count = (len(message) + FRAGMENT_SIZE - 1) // FRAGMENT_SIZE
        start = self.out_seq.get(channel, 0) + 1
        for number in range(count):
            seq = self._next_seq(channel)
            offset = number * FRAGMENT_SIZE
            extra = struct.pack(">IIIII", start, count, number, len(message), offset)
            command = self._command(FRAGMENT, channel, FLAG_RELIABLE, seq, message[offset:offset + FRAGMENT_SIZE], extra)
            self._send_tracked(channel, seq, command)

    def send_unsequenced(self, message, channel=0):
        group = self.out_group.get(channel, 0) + 1
        self.out_group[channel] = group
        self._packet(self._command(UNSEQUENCED, channel, FLAG_UNSEQUENCED, 0, message, struct.pack(">I", group)))

    def _ack(self, channel, seq, sent):
        self._packet(self._command(ACK, channel, 0, 0, struct.pack(">II", seq, sent)))

    def handle(self, data):
        self.last_seen = time.monotonic()
        _, _, count, sent, _ = struct.unpack_from(">HBBII", data)
        offset = 12
        for _ in range(count):
            if offset + 12 > len(data):
                return
            kind, channel, flags, _, size, seq = struct.unpack_from(">BBBBII", data, offset)
            if size < 12 or offset + size > len(data):
                return
            self._command_in(kind, channel, flags, seq, data[offset + 12:offset + size], sent)
            offset += size

    def _command_in(self, kind, channel, flags, seq, payload, sent):
        if flags & FLAG_RELIABLE:
            self._ack(channel, seq, sent)
        if kind == ACK:
            acked, _ = struct.unpack_from(">II", payload)
            self.pending.pop((channel, acked), None)
        elif kind == CONNECT:
            if not self.out_seq.get(CONTROL_CHANNEL):
                body = struct.pack(">H", self.peer_id) + payload[2:12] + bytes(20)
                seq_out = self._next_seq(CONTROL_CHANNEL)
                self._send_tracked(CONTROL_CHANNEL, seq_out, self._command(VERIFY, CONTROL_CHANNEL, FLAG_RELIABLE, seq_out, body))
        elif kind == DISCONNECT:
            self.close()
        elif kind in (RELIABLE, FRAGMENT):
            self._reliable_in(kind, channel, seq, payload)
        elif kind == UNRELIABLE:
            self.handler.on_message(self, payload[4:], False)
        elif kind == UNSEQUENCED:
            self.handler.on_message(self, payload[4:], False)

    def _reliable_in(self, kind, channel, seq, payload):
        expected = self.in_expected.get(channel, 1)
        if seq < expected:
            return
        self.in_buffer[(channel, seq)] = (kind, payload)
        while (channel, expected) in self.in_buffer:
            item_kind, item = self.in_buffer.pop((channel, expected))
            expected += 1
            self.in_expected[channel] = expected
            if item_kind == RELIABLE:
                self.handler.on_message(self, item, True)
            else:
                self._fragment_in(item)

    def _fragment_in(self, payload):
        start, count, number, total, offset = struct.unpack_from(">IIIII", payload)
        entry = self.fragments.setdefault(start, [bytearray(total), set()])
        chunk = payload[20:]
        entry[0][offset:offset + len(chunk)] = chunk
        entry[1].add(number)
        if len(entry[1]) == count:
            del self.fragments[start]
            self.handler.on_message(self, bytes(entry[0]), True)

    def tick(self):
        current = time.monotonic()
        if current - self.last_seen > IDLE_TIMEOUT:
            self.close()
            return
        for key, item in list(self.pending.items()):
            if current - item[2] > GIVE_UP_AFTER:
                self.close()
                return
            if current - item[1] > RESEND_AFTER:
                item[1] = current
                self._packet(item[0])

    def close(self):
        if not self.closed:
            self.closed = True
            self.handler.on_close(self)


class Listener(asyncio.DatagramProtocol):
    def __init__(self, handler):
        self.handler = handler
        self.connections = {}
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if len(data) == PING_PACKET_SIZE and data[:12] == PING_PREFIX:
            self.transport.sendto(data, addr)
            return
        if len(data) < 12:
            return
        connection = self.connections.get(addr)
        is_connect = len(data) >= 24 and data[12] == CONNECT
        if is_connect and (connection is None or connection.closed):
            challenge = struct.unpack_from(">I", data, 8)[0]
            connection = Connection(self.transport, addr, challenge, self.handler)
            self.connections[addr] = connection
        if connection is not None and not connection.closed:
            connection.handle(data)

    def tick(self):
        for addr, connection in list(self.connections.items()):
            connection.tick()
            if connection.closed:
                del self.connections[addr]
