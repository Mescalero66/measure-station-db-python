"""Modbus TCP: a minimal client (read functions 1-4) and register decoding.

Copied unchanged from device-data-receiver's modbus_core.py (REG_TYPES, EXCEPTIONS,
FORMATS, ModbusError, ref, ModbusTcpClient and decode); its CSV logging, polling thread
and settings are left out. Keep these parts identical to the original: make changes
there and copy them over. Excluded from ruff, like the station's drivers.

Standard library only. Register numbers are protocol addresses (0-based),
as in the device documentation; ref() gives the 1-based "40001" style too.
"""

import math
import socket
import struct

# Register type -> (function code, CSV column prefix, reference digit, max per read)
REG_TYPES = {
    "Holding registers": (3, "hr", 4, 125),
    "Input registers": (4, "ir", 3, 125),
    "Coils": (1, "co", 0, 2000),
    "Discrete inputs": (2, "di", 1, 2000),
}

EXCEPTIONS = {1: "illegal function", 2: "illegal data address", 3: "illegal data value",
              4: "server device failure", 5: "acknowledge", 6: "server device busy",
              10: "gateway path unavailable", 11: "gateway target device failed to respond"}

FORMATS = ("uint16", "int16", "uint32", "int32", "float32")


class ModbusError(Exception):
    """The device answered with a Modbus exception response."""

    def __init__(self, code):
        self.code = code
        super().__init__("device replied with exception %d (%s)"
                         % (code, EXCEPTIONS.get(code, "unknown")))


def ref(reg_type, address):
    """The conventional 1-based reference, e.g. holding register 10 -> 40011."""
    digit = REG_TYPES[reg_type][2]
    return ("%d%04d" if address < 9999 else "%d%05d") % (digit, address + 1)


class ModbusTcpClient:
    """Reads coils/registers from one Modbus TCP device (functions 1-4).

    Network problems and malformed replies raise OSError (socket.timeout is
    one); a Modbus exception reply raises ModbusError.
    """

    def __init__(self, host, port=502, unit=1, timeout=3.0):
        self.unit = unit
        self.tid = 0
        self.sock = socket.create_connection((host, port), timeout=timeout)

    def read(self, fc, address, count):
        self.tid = (self.tid + 1) & 0xFFFF
        self.sock.sendall(struct.pack(">HHHBBHH", self.tid, 0, 6, self.unit, fc, address, count))
        tid, proto, length, _unit = struct.unpack(">HHHB", self._recv(7))
        if not 2 <= length <= 254:
            raise OSError("bad Modbus reply length %d" % length)
        pdu = self._recv(length - 1)
        # A reply to an earlier, timed-out request would have an older id.
        if tid != self.tid or proto != 0:
            raise OSError("Modbus reply does not match the request")
        if pdu[0] == fc | 0x80:
            raise ModbusError(pdu[1] if len(pdu) > 1 else 0)
        if pdu[0] != fc or len(pdu) < 2 or pdu[1] != len(pdu) - 2:
            raise OSError("malformed Modbus reply")
        data = pdu[2:]
        if fc in (3, 4):
            if len(data) != 2 * count:
                raise OSError("Modbus reply has %d registers, expected %d" % (len(data) // 2, count))
            return list(struct.unpack(">%dH" % count, data))
        if len(data) != (count + 7) // 8:
            raise OSError("Modbus reply has the wrong number of bits")
        return [(data[i // 8] >> (i % 8)) & 1 for i in range(count)]

    def _recv(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise OSError("connection closed by the device")
            buf += chunk
        return buf

    def close(self):
        self.sock.close()


def decode(values, offset, fmt):
    """The value at `offset` in a list of raw 16-bit registers, or None.

    32-bit formats use two registers, high word first (the Modbus norm).
    None if a register is missing/unreadable or a float is not finite.
    """
    words = 1 if fmt.endswith("16") else 2
    if values is None or offset < 0 or offset + words > len(values):
        return None
    w = values[offset:offset + words]
    if None in w:
        return None
    if fmt == "uint16":
        return w[0]
    if fmt == "int16":
        return w[0] - 0x10000 if w[0] & 0x8000 else w[0]
    v = (w[0] << 16) | w[1]
    if fmt == "uint32":
        return v
    if fmt == "int32":
        return v - 0x100000000 if v & 0x80000000 else v
    f = struct.unpack(">f", struct.pack(">I", v))[0]
    return f if math.isfinite(f) else None
