"""Photon Protocol18 serialization: the typed values inside Photon messages."""
import struct


class Byte(int):
    """Marks a Python int to be written as a single Photon byte."""


class StrDict(dict):
    """Marks a dict to be written as Dictionary<string, object>, not a Hashtable."""


BOOL, BYTE, SHORT, FLOAT, DOUBLE, STRING, NULL = 2, 3, 4, 5, 6, 7, 8
CINT, CLONG, INT1, INT1_NEG, INT2, INT2_NEG, LONG1, LONG1_NEG, LONG2, LONG2_NEG = 9, 10, 11, 12, 13, 14, 15, 16, 17, 18
CUSTOM, DICT, HASHTABLE, OBJECT_ARRAY = 19, 20, 21, 23
FALSE, TRUE, SHORT_ZERO, INT_ZERO, LONG_ZERO, FLOAT_ZERO, DOUBLE_ZERO, BYTE_ZERO = 27, 28, 29, 30, 31, 32, 33, 34
ARRAY = 0x40


class Reader:
    def __init__(self, data, pos=0):
        self.data = data
        self.pos = pos

    def take(self, n):
        chunk = self.data[self.pos:self.pos + n]
        if len(chunk) != n:
            raise ValueError("truncated Photon value")
        self.pos += n
        return chunk

    def byte(self):
        return self.take(1)[0]

    def varint(self):
        value = shift = 0
        while True:
            b = self.byte()
            value |= (b & 0x7F) << shift
            if not b & 0x80:
                return value
            shift += 7

    def zigzag(self):
        v = self.varint()
        return (v >> 1) ^ -(v & 1)

    def string(self):
        return self.take(self.varint()).decode("utf-8")

    def value(self, kind=None):
        kind = self.byte() if kind is None else kind
        if kind == 0:
            return self.value()
        if kind & ARRAY:
            return self.array(kind & ~ARRAY)
        if kind >= 0x80:
            custom_type = kind & 0x7F
            return ("custom", custom_type, self.take(self.varint()))
        return self.scalar(kind)

    def scalar(self, kind):
        if kind == BYTE:
            return Byte(self.byte())
        if kind == BOOL:
            return self.byte() != 0
        if kind == SHORT:
            return struct.unpack("<h", self.take(2))[0]
        if kind == FLOAT:
            return struct.unpack("<f", self.take(4))[0]
        if kind == DOUBLE:
            return struct.unpack("<d", self.take(8))[0]
        if kind == STRING:
            return self.string()
        if kind == NULL:
            return None
        if kind in (CINT, CLONG):
            return self.zigzag()
        if kind in (INT1, LONG1):
            return self.byte()
        if kind in (INT1_NEG, LONG1_NEG):
            return -self.byte()
        if kind in (INT2, LONG2):
            return struct.unpack("<H", self.take(2))[0]
        if kind in (INT2_NEG, LONG2_NEG):
            return -struct.unpack("<H", self.take(2))[0]
        if kind == CUSTOM:
            custom_type = self.byte()
            return ("custom", custom_type, self.take(self.varint()))
        if kind == HASHTABLE:
            return {self.value(): self.value() for _ in range(self.varint())}
        if kind == DICT:
            return self.dictionary()
        if kind == OBJECT_ARRAY:
            return [self.value() for _ in range(self.varint())]
        if kind in (FALSE, TRUE):
            return kind == TRUE
        if kind in (SHORT_ZERO, INT_ZERO, LONG_ZERO, BYTE_ZERO):
            return Byte(0) if kind == BYTE_ZERO else 0
        if kind in (FLOAT_ZERO, DOUBLE_ZERO):
            return 0.0
        raise ValueError(f"unknown Photon type {kind}")

    def dictionary(self):
        key_kind, value_kind = self.byte(), self.byte()
        result = StrDict()
        for _ in range(self.varint()):
            key = self.value(key_kind or None)
            result[key] = self.value(value_kind or None)
        return result

    def array(self, kind):
        count = self.varint()
        if kind == BYTE:
            return self.take(count)
        if kind == BOOL:
            packed = self.take((count + 7) // 8)
            return [bool(packed[i // 8] >> (i % 8) & 1) for i in range(count)]
        return [self.scalar(kind) for _ in range(count)]

    def parameters(self):
        return {self.byte(): self.value() for _ in range(self.byte())}

    def raw_parameters(self):
        """Like parameters(), but also return each value's original bytes."""
        values, raw = {}, {}
        for _ in range(self.byte()):
            key = self.byte()
            start = self.pos
            values[key] = self.value()
            raw[key] = self.data[start:self.pos]
        return values, raw


def varint(value):
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def zigzag(value):
    return varint(((value << 1) ^ (value >> 63)) & 0xFFFFFFFFFFFFFFFF)


def write_string(text):
    raw = text.encode("utf-8")
    return varint(len(raw)) + raw


def write_int(value):
    if value == 0:
        return bytes([INT_ZERO])
    if 0 < value <= 0xFF:
        return bytes([INT1, value])
    return bytes([CINT]) + zigzag(value)


def write(value):
    if value is None:
        return bytes([NULL])
    if isinstance(value, bool):
        return bytes([TRUE if value else FALSE])
    if isinstance(value, Byte):
        return bytes([BYTE_ZERO]) if value == 0 else bytes([BYTE, value])
    if isinstance(value, int):
        return write_int(value)
    if isinstance(value, float):
        return bytes([DOUBLE]) + struct.pack("<d", value)
    if isinstance(value, str):
        return bytes([STRING]) + write_string(value)
    if isinstance(value, (bytes, bytearray)):
        return bytes([ARRAY | BYTE]) + varint(len(value)) + bytes(value)
    if isinstance(value, StrDict):
        body = b"".join(write_string(k) + write(v) for k, v in value.items())
        return bytes([DICT, STRING, 0]) + varint(len(value)) + body
    if isinstance(value, dict):
        body = b"".join(write(k) + write(v) for k, v in value.items())
        return bytes([HASHTABLE]) + varint(len(value)) + body
    if isinstance(value, list):
        if value and all(isinstance(v, str) for v in value):
            return bytes([ARRAY | STRING]) + varint(len(value)) + b"".join(write_string(v) for v in value)
        if all(isinstance(v, int) and not isinstance(v, bool) for v in value):
            return bytes([ARRAY | CINT]) + varint(len(value)) + b"".join(zigzag(v) for v in value)
        return bytes([OBJECT_ARRAY]) + varint(len(value)) + b"".join(write(v) for v in value)
    raise TypeError(f"cannot write {type(value)}")


def write_parameters(params):
    return bytes([len(params)]) + b"".join(bytes([k]) + write(v) for k, v in params.items())


# Message framing. Byte 0 is always 0xF3; bit 7 of byte 1 marks encryption.
MAGIC = 0xF3
INIT, INIT_RESPONSE, OPERATION, OPERATION_RESPONSE, EVENT = 0, 1, 2, 3, 4
INTERNAL_OPERATION, INTERNAL_OPERATION_RESPONSE = 6, 7


def operation_response(op, params, code=0, kind=OPERATION_RESPONSE):
    head = bytes([MAGIC, kind, op]) + struct.pack("<h", code) + bytes([NULL])
    return head + write_parameters(params)


def event(code, params):
    return bytes([MAGIC, EVENT, code]) + write_parameters(params)


def raw_event(code, raw_params):
    """Build an event from already serialized values, keeping their exact types."""
    body = b"".join(bytes([k]) + v for k, v in raw_params.items())
    return bytes([MAGIC, EVENT, code, len(raw_params)]) + body


def parse_raw_request(message):
    """Return (opcode, params, raw_params) for a decrypted Operation message."""
    return (message[2], *Reader(message, 3).raw_parameters())


def parse_request(message):
    """Return (opcode, params) for a decrypted Operation message."""
    reader = Reader(message, 3)
    return message[2], reader.parameters()
