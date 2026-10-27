# MIT License
#
# Copyright (c) 2025 Nathan G. Barguss - 2E0NBS
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#

"""Serial transport for RT-950 Pro clone operations."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Optional, Sequence, Tuple
import random
import time

import serial

from .logging import get_logger

__all__ = [
    "CloneTransportError",
    "CloneSerialConfig",
    "CloneSegment",
    "CloneSerialTransport",
]

HANDSHAKE_STRING = b"PROGRAMBT9000U"

# Model strings the driver is allowed to WRITE to (exact match after the
# handshake strips NULs/spaces). Reads are always allowed so the string can be
# discovered from the log. An RT-950 Pro reports "RT-950" (no "Pro"), as noted
# in RADIO_PROTOCOL_WIP.md; anything else is refused so a sibling radio that
# shares the bootloader handshake cannot be overwritten with an RT-950 Pro image.
KNOWN_MODELS: Tuple[str, ...] = ("RT-950",)
ACK = b"\x06"
END_COMMAND = b"E"
READ_BLOCK = 0x80
BLOCK_HEADER = 4
# The factory CPS resends a block up to three times when the ACK is missing or
# wrong (RADIO_PROTOCOL_WIP.md). One corrupted byte on the serial link is enough
# for the radio to answer with something other than 0x06 (a 0x55 write header
# read as 0x54 makes it reply with a read block instead of an ACK). Rewriting
# the same bytes to the same address is harmless, so retry before giving up.
WRITE_ATTEMPTS = 3
WRITE_RETRY_DELAY = 0.3

ENCRYPT_STRINGS: Tuple[bytes, ...] = (
    b"BHT ",
    b"CO 7",
    b"A ES",
    b" EIY",
    b"M PQ",
    b"XN Y",
    b"RVB ",
    b" HQP",
    b"W RC",
    b"MS N",
    b" SAT",
    b"K DH",
    b"ZO R",
    b"C SL",
    b"6RB ",
    b" JCG",
    b"PN V",
    b"J PK",
    b"EK L",
    b"I LZ",
)


@dataclass(slots=True)
class CloneSegment:
    """Defines a contiguous clone memory region to read or write."""

    read_command: int
    write_command: int
    start: int
    length: int


DEFAULT_SEGMENTS: Tuple[CloneSegment, ...] = (
    CloneSegment(0x52, 0x57, 0x0000, 0x7800),
    CloneSegment(0x52, 0x57, 0x8000, 0x0100),
    CloneSegment(0x52, 0x57, 0x9000, 0x0100),
    CloneSegment(0x52, 0x57, 0xA000, 0x0200),
    CloneSegment(0x52, 0x57, 0xB000, 0x0200),
    CloneSegment(0x52, 0x57, 0xD000, 0x0300),
    CloneSegment(0x54, 0x55, 0x0000, 0x0080),
)


class CloneTransportError(RuntimeError):
    """Raised when the serial clone transport encounters a protocol error."""


@dataclass(slots=True)
class CloneSerialConfig:
    """Configuration for opening a serial connection to the radio."""

    port: str
    baudrate: int = 115200
    timeout: float = 1.0
    write_timeout: float = 1.0


class CloneSerialTransport:
    """Serial transport implementing the RT-950 Pro clone protocol."""

    def __init__(
        self,
        serial_port,
        *,
        logger=None,
        rng: Optional[random.Random] = None,
    ) -> None:
        self.serial = serial_port
        self.logger = logger or get_logger("transport")
        # Enforce sensible minimums to avoid premature timeouts during clone
        try:
            to = getattr(self.serial, "timeout", None)
            if to is None or float(to) < 3.0:
                self.serial.timeout = 3.0
        except Exception:
            self.serial.timeout = 3.0
        try:
            wto = getattr(self.serial, "write_timeout", None)
            if wto is None or float(wto) < 3.0:
                self.serial.write_timeout = 3.0
        except Exception:
            self.serial.write_timeout = 3.0
        self._rng = rng or random.Random()
        self._xor_key: Optional[bytearray] = None
        self._model: Optional[str] = None
        # Optional per-block progress callback: (done, total, phase)
        self.progress_cb: Optional[Callable[[int, int, str], None]] = None

    @classmethod
    def open(
        cls,
        config: CloneSerialConfig,
        *,
        logger=None,
        rng: Optional[random.Random] = None,
        serial_class=serial.Serial,
    ) -> "CloneSerialTransport":
        """Open a real serial port and return a configured transport."""

        port = serial_class(
            config.port,
            baudrate=config.baudrate,
            timeout=config.timeout,
            write_timeout=config.write_timeout,
        )
        return cls(port, logger=logger, rng=rng)

    def __enter__(self) -> "CloneSerialTransport":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[override]
        self.close()

    @property
    def model(self) -> Optional[str]:
        """Radio model string retrieved during handshake (if available)."""

        return self._model

    def close(self) -> None:
        """Close the underlying serial port."""

        try:
            self.serial.close()
        except Exception:  # pragma: no cover - defensive cleanup
            pass

    def handshake(self) -> str:
        """Perform the clone handshake and establish the XOR keystream."""

        self.logger.debug("Starting clone handshake")
        if hasattr(self.serial, "reset_input_buffer"):
            self.serial.reset_input_buffer()
        if hasattr(self.serial, "reset_output_buffer"):
            self.serial.reset_output_buffer()

        self._write(HANDSHAKE_STRING)
        if self._read_exact(1) != ACK:
            raise CloneTransportError("Handshake ACK not received")

        self._write(b"F")
        blob = self._read_exact(16)
        self.logger.debug("Received handshake blob: %s", blob.hex())

        self._write(b"M")
        model_raw = self._read_exact(12)
        self._model = model_raw.decode("ascii", errors="ignore").strip("\x00 ")
        if not self._model:
            raise CloneTransportError("Radio did not return a model identifier")
        self.logger.info("Radio model string: %r", self._model)

        frame, key = self._build_encryption_frame()
        self._write(frame)
        if self._read_exact(1) != ACK:
            raise CloneTransportError("Encryption ACK not received")
        self._xor_key = key
        self.logger.debug("Negotiated XOR key: %s", key.hex())
        return self._model

    def read_clone(self, *, segments: Sequence[CloneSegment] | None = None) -> bytes:
        """Read clone data according to ``segments`` and return the raw payload."""

        if segments is None:
            segments = DEFAULT_SEGMENTS
        if self._xor_key is None:
            self.handshake()

        raw = bytearray()
        total_blocks = sum(seg.length for seg in segments) // READ_BLOCK
        done = 0
        for command, address in self._iter_read_commands(segments):
            header = bytes((command, (address >> 8) & 0xFF, address & 0xFF, READ_BLOCK))
            self.logger.debug(
                "Requesting block: command=0x%02X address=0x%04X", command, address
            )
            self._write(header)
            block = self._read_exact(BLOCK_HEADER + READ_BLOCK)
            payload = bytearray(block[BLOCK_HEADER:])
            decrypted = self._apply_xor(payload)
            raw.extend(decrypted)
            done += 1
            if self.progress_cb:
                try:
                    self.progress_cb(done, total_blocks, "read")
                except Exception:
                    # Progress should never break the clone operation
                    pass
        self._write(END_COMMAND)
        return bytes(raw)

    def write_clone(self, data: bytes, *, segments: Sequence[CloneSegment] | None = None) -> None:
        """Write clone data back to the radio using the provided segments."""

        if segments is None:
            segments = DEFAULT_SEGMENTS
        if self._xor_key is None:
            self.handshake()
        self._require_known_model()

        expected = sum(segment.length for segment in segments)
        if len(data) != expected:
            raise CloneTransportError(f"Write buffer length {len(data)} does not match expected {expected}")

        offset = 0
        total_blocks = sum(seg.length for seg in segments) // READ_BLOCK
        done = 0
        for command, address in self._iter_write_commands(segments):
            chunk = data[offset : offset + READ_BLOCK]
            if len(chunk) != READ_BLOCK:
                raise CloneTransportError("Write chunk size mismatch")
            payload = self._apply_xor(bytearray(chunk))
            header = bytes((command, (address >> 8) & 0xFF, address & 0xFF, READ_BLOCK))
            self.logger.debug("Writing block: command=0x%02X address=0x%04X", command, address)
            self._write_block_with_retry(header + payload, address)
            offset += READ_BLOCK
            done += 1
            if self.progress_cb:
                try:
                    self.progress_cb(done, total_blocks, "write")
                except Exception:
                    pass

        self._write(END_COMMAND)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_known_model(self) -> None:
        """Refuse to write unless the radio identified itself as a known model."""

        model = (self._model or "").strip()
        if model not in KNOWN_MODELS:
            raise CloneTransportError(
                f"Refusing to write: radio reports model {model!r}, which is not in "
                f"KNOWN_MODELS {KNOWN_MODELS}. If this really is an RT-950 Pro, add "
                "that exact string to KNOWN_MODELS in transport.py."
            )

    def _write_block_with_retry(self, frame: bytes, address: int) -> None:
        """Send one write frame, resending it when the radio does not ACK."""

        last = b""
        for attempt in range(1, WRITE_ATTEMPTS + 1):
            self._write(frame)
            try:
                last = self._read_exact(1)
            except CloneTransportError:
                last = b""
            if last == ACK:
                self.logger.debug("Received ACK for 0x%04X", address)
                return
            self.logger.warning(
                "Write ACK mismatch at 0x%04X (attempt %d/%d): expected 0x06, got %s",
                address,
                attempt,
                WRITE_ATTEMPTS,
                last.hex() or "nothing",
            )
            if attempt < WRITE_ATTEMPTS:
                self._discard_input(WRITE_RETRY_DELAY)
        raise CloneTransportError(
            f"Write ACK mismatch at 0x{address:04X} after {WRITE_ATTEMPTS} attempts: "
            f"expected 0x06, got {last.hex() or 'nothing'}"
        )

    def _discard_input(self, delay: float) -> None:
        """Give the radio time to finish talking, then drop whatever it sent."""

        time.sleep(delay)
        if hasattr(self.serial, "reset_input_buffer"):
            self.serial.reset_input_buffer()

    def _write(self, data: bytes) -> None:
        if not data:
            return
        written = self.serial.write(data)
        if written is None:
            return
        if written != len(data):
            raise CloneTransportError("Serial write truncated")

    def _read_exact(self, size: int) -> bytes:
        buf = bytearray()
        while len(buf) < size:
            chunk = self.serial.read(size - len(buf))
            if not chunk:
                raise CloneTransportError("Serial read timed out")
            buf.extend(chunk)
        return bytes(buf)

    def _build_encryption_frame(self) -> Tuple[bytes, bytearray]:
        frame = bytearray(25)
        frame[0:4] = b"SEND"
        frame[4] = (self._rng.randint(1, 2) << 4) | self._rng.randint(0, 4)
        for index in range(19):
            frame[5 + index] = self._rng.randint(0, 19)
        code = frame[4]
        if code & 0x20:
            idx = (code - 0x20) * 2 + 1
        else:
            idx = (code - 0x10) * 2
        idx += 1
        symbol_index = frame[4 + idx]
        key = bytearray(ENCRYPT_STRINGS[symbol_index])
        return bytes(frame), key

    def _apply_xor(self, payload: bytearray) -> bytearray:
        assert self._xor_key is not None, "XOR key not initialised"
        key = self._xor_key
        key_idx = 0
        for i, value in enumerate(payload):
            k = key[key_idx]
            key_idx = (key_idx + 1) % len(key)
            if k != 0x20 and value not in (0x00, 0xFF) and value not in (k, k ^ 0xFF):
                payload[i] = value ^ k
        return payload

    @staticmethod
    def _iter_read_commands(segments: Sequence[CloneSegment]) -> Iterable[Tuple[int, int]]:
        for segment in segments:
            for offset in range(0, segment.length, READ_BLOCK):
                yield segment.read_command, segment.start + offset

    @staticmethod
    def _iter_write_commands(segments: Sequence[CloneSegment]) -> Iterable[Tuple[int, int]]:
        for segment in segments:
            for offset in range(0, segment.length, READ_BLOCK):
                yield segment.write_command, segment.start + offset
