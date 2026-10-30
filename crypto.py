"""Photon payload encryption: Diffie-Hellman key exchange plus AES-256-CBC."""
import hashlib
import secrets

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# RFC 2409 group 1, 768 bits, with the generator Photon uses.
PRIME = int(
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74"
    "020BBEA63B139B22514A08798E3404DDEF9519B3CD3A431B302B0A6DF25F1437"
    "4FE1356D6D51C245E485B576625E7EC6F44C42E9A63A3620FFFFFFFFFFFFFFFF", 16)
GENERATOR = 22
KEY_BYTES = 96
# Photon payload encryption uses a fixed all-zero IV and sends no IV on the wire.
ZERO_IV = bytes(16)


def to_bytes(value):
    return value.to_bytes((value.bit_length() + 7) // 8, "big")


class Session:
    def __init__(self):
        self.private = secrets.randbits(160) | 1
        self.key = None
        public = pow(GENERATOR, self.private, PRIME)
        self.public = public.to_bytes(KEY_BYTES, "big")

    def derive(self, peer_public):
        shared = pow(int.from_bytes(peer_public, "big"), self.private, PRIME)
        self.key = hashlib.sha256(to_bytes(shared)).digest()

    def encrypt(self, plain):
        padder = padding.PKCS7(128).padder()
        data = padder.update(plain) + padder.finalize()
        enc = Cipher(algorithms.AES(self.key), modes.CBC(ZERO_IV)).encryptor()
        return enc.update(data) + enc.finalize()

    def decrypt(self, data):
        dec = Cipher(algorithms.AES(self.key), modes.CBC(ZERO_IV)).decryptor()
        plain = dec.update(data) + dec.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        return unpadder.update(plain) + unpadder.finalize()
