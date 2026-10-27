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

from pathlib import Path
import random
from typing import List

import pytest

from rt950pro.image import RadioImage
from rt950pro.transport import (
    ACK,
    CloneSegment,
    CloneSerialTransport,
    CloneTransportError,
    DEFAULT_SEGMENTS,
    READ_BLOCK,
)

CLONE_FIXTURE = Path(__file__).resolve().parents[1] / "dumps" / "clean.bin"


class FakeSerial:
    """Simple serial stand-in for exercising the transport."""

    def __init__(self, initial_bytes: bytes = b"") -> None:
        self._buffer = bytearray(initial_bytes)
        self.write_history: List[bytes] = []
        self.timeout = 1.0
        self.write_timeout = 1.0

    def write(self, data: bytes) -> int:
        self.write_history.append(bytes(data))
        return len(data)

    def read(self, size: int) -> bytes:
        if not self._buffer:
            return b""
        chunk = self._buffer[:size]
        del self._buffer[:size]
        return bytes(chunk)

    def reset_input_buffer(self) -> None:
        pass

    def reset_output_buffer(self) -> None:
        pass

    def close(self) -> None:
        pass


TEST_SEGMENT = CloneSegment(0x52, 0x57, 0x0000, READ_BLOCK)


def _build_expected_key(rng: random.Random) -> bytes:
    frame = bytearray(25)
    frame[0:4] = b"SEND"
    frame[4] = (rng.randint(1, 2) << 4) | rng.randint(0, 4)
    for index in range(19):
        frame[5 + index] = rng.randint(0, 19)
    code = frame[4]
    if code & 0x20:
        idx = (code - 0x20) * 2 + 1
    else:
        idx = (code - 0x10) * 2
    idx += 1
    symbol_index = frame[4 + idx]
    from rt950pro.transport import ENCRYPT_STRINGS

    return ENCRYPT_STRINGS[symbol_index]


def _xor_payload(data: bytes, key: bytes) -> bytes:
    payload = bytearray(data)
    key_idx = 0
    for i, value in enumerate(payload):
        k = key[key_idx]
        key_idx = (key_idx + 1) % len(key)
        if k != 0x20 and value not in (0x00, 0xFF) and value not in (k, k ^ 0xFF):
            payload[i] = value ^ k
    return bytes(payload)


def _handshake_responses(model: bytes, encrypted_payload: bytes) -> bytes:
    responses = bytearray()
    responses.extend(ACK)
    responses.extend(b"\x00" * 16)
    responses.extend(model)
    responses.extend(ACK)
    responses.extend(bytes([ACK[0], 0x00, 0x00, READ_BLOCK]))
    responses.extend(encrypted_payload)
    return bytes(responses)


def test_handshake_and_read_minimal_plan() -> None:
    responses = _handshake_responses(b"RT-950      ", b"\xFF" * READ_BLOCK)
    fake = FakeSerial(responses)
    transport = CloneSerialTransport(fake, rng=random.Random(0))

    data = transport.read_clone(segments=[TEST_SEGMENT])

    assert data == b"\xFF" * READ_BLOCK
    assert transport.model == "RT-950"
    history = fake.write_history
    assert history[0] == b"PROGRAMBT9000U"
    assert history[1] == b"F"
    assert history[2] == b"M"
    assert history[3].startswith(b"SEND")
    assert len(history[4]) == 4  # read header
    assert history[-1] == b"E"


def test_handshake_ack_timeout() -> None:
    fake = FakeSerial()
    transport = CloneSerialTransport(fake)
    with pytest.raises(CloneTransportError):
        transport.handshake()


def test_read_applies_xor_key() -> None:
    rng_seed = 1234
    expected_key = _build_expected_key(random.Random(rng_seed))
    plain = bytes(((i % 120) + 1) for i in range(READ_BLOCK))
    encrypted = _xor_payload(plain, expected_key)
    responses = _handshake_responses(b"RT-950      ", encrypted)

    fake = FakeSerial(responses)
    transport = CloneSerialTransport(fake, rng=random.Random(rng_seed))

    data = transport.read_clone(segments=[TEST_SEGMENT])
    assert data == plain


def test_write_clone_applies_xor() -> None:
    rng_seed = 4321
    expected_key = _build_expected_key(random.Random(rng_seed))
    plain = bytes(((i % 100) + 2) for i in range(READ_BLOCK))
    fake = FakeSerial(_handshake_responses(b"RT-950      ", b"\xFF" * READ_BLOCK))
    transport = CloneSerialTransport(fake, rng=random.Random(rng_seed))

    transport.write_clone(plain, segments=[TEST_SEGMENT])

    payloads = [record for record in fake.write_history if len(record) == READ_BLOCK + 4]
    assert payloads, "Expected at least one write block"
    payload = payloads[-1][4:]
    assert payload == _xor_payload(plain, expected_key)


def test_read_clone_allows_unknown_model() -> None:
    responses = _handshake_responses(b"BT-9000     ", b"\xFF" * READ_BLOCK)
    transport = CloneSerialTransport(FakeSerial(responses), rng=random.Random(0))

    data = transport.read_clone(segments=[TEST_SEGMENT])

    assert data == b"\xFF" * READ_BLOCK
    assert transport.model == "BT-9000"


def test_write_clone_refuses_unknown_model() -> None:
    fake = FakeSerial(_handshake_responses(b"BT-9000     ", b"\xFF" * READ_BLOCK))
    transport = CloneSerialTransport(fake, rng=random.Random(0))

    with pytest.raises(CloneTransportError, match="Refusing to write"):
        transport.write_clone(b"\x00" * READ_BLOCK, segments=[TEST_SEGMENT])

    # The handshake completed, but no data block was sent to the radio.
    assert transport.model == "BT-9000"
    assert all(len(record) != READ_BLOCK + 4 for record in fake.write_history)


from rt950pro import cli


class DummyTransport:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self.written: List[bytes] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read_clone(self, *, segments=None):
        return self._payload

    def write_clone(self, data: bytes, *, segments=None):
        self.written.append(bytes(data))


def _monkey_transport(monkeypatch, transport):
    def fake_open(cls, config, *, logger=None, rng=None, serial_class=None):
        return transport

    monkeypatch.setattr(cli.CloneSerialTransport, "open", classmethod(fake_open))


def test_cli_clone_read(tmp_path, monkeypatch):
    output = tmp_path / "dump.bin"
    payload = b"TESTDATA"
    transport = DummyTransport(payload)

    _monkey_transport(monkeypatch, transport)

    exit_code = cli.main(["image", "clone-read", "--port", "COM1", "--baud", "57600", "--output", str(output)])
    assert exit_code == 0
    assert output.read_bytes() == payload


def test_cli_clone_write(tmp_path, monkeypatch):
    source = CLONE_FIXTURE
    payload = source.read_bytes()
    input_file = tmp_path / "clone.bin"
    input_file.write_bytes(payload)
    transport = DummyTransport(b"")

    _monkey_transport(monkeypatch, transport)

    exit_code = cli.main(
        [
            "image",
            "clone-write",
            "--port",
            "COM2",
            "--input",
            str(input_file),
        ]
    )
    assert exit_code == 0
    assert transport.written
    expected = payload
    assert transport.written[0] == expected



class ScriptedSerial(FakeSerial):
    """FakeSerial that answers each write block from a script of replies."""

    def __init__(self, initial_bytes: bytes, block_replies: List[bytes]) -> None:
        super().__init__(initial_bytes)
        self.block_replies = list(block_replies)
        self.flushes = 0

    def write(self, data: bytes) -> int:
        if len(data) == READ_BLOCK + 4:
            self.in_data_phase = True
            if self.block_replies:
                self._buffer.extend(self.block_replies.pop(0))
        return super().write(data)

    def reset_input_buffer(self) -> None:
        # The handshake flushes too; only count flushes once blocks are flowing.
        if getattr(self, "in_data_phase", False):
            self.flushes += 1
            self._buffer.clear()


def _handshake_only(model: bytes = b"RT-950      ") -> bytes:
    return bytes(ACK) + b"\x00" * 16 + model + bytes(ACK)


def _block_frames(fake: FakeSerial) -> List[bytes]:
    return [record for record in fake.write_history if len(record) == READ_BLOCK + 4]


def test_write_clone_resends_block_after_bad_ack(monkeypatch) -> None:
    from rt950pro import transport as transport_module

    monkeypatch.setattr(transport_module.time, "sleep", lambda _s: None)
    # First reply looks like the radio answering an APRS *read* (0x54 header +
    # data) instead of an ACK; the second attempt is acknowledged.
    fake = ScriptedSerial(_handshake_only(), [b"T" + b"\x00" * 131, bytes(ACK)])
    transport = CloneSerialTransport(fake, rng=random.Random(0))

    transport.write_clone(b"\x01" * READ_BLOCK, segments=[TEST_SEGMENT])

    frames = _block_frames(fake)
    assert len(frames) == 2 and frames[0] == frames[1]
    assert fake.flushes == 1, "stale reply bytes must be discarded before the resend"
    assert fake.write_history[-1] == b"E"


def test_write_clone_treats_timeout_as_failed_attempt(monkeypatch) -> None:
    from rt950pro import transport as transport_module

    monkeypatch.setattr(transport_module.time, "sleep", lambda _s: None)
    fake = ScriptedSerial(_handshake_only(), [b"", bytes(ACK)])
    transport = CloneSerialTransport(fake, rng=random.Random(0))

    transport.write_clone(b"\x01" * READ_BLOCK, segments=[TEST_SEGMENT])

    assert len(_block_frames(fake)) == 2


def test_write_clone_gives_up_after_three_attempts(monkeypatch) -> None:
    from rt950pro import transport as transport_module

    monkeypatch.setattr(transport_module.time, "sleep", lambda _s: None)
    fake = ScriptedSerial(_handshake_only(), [b"T", b"T", b"T"])
    transport = CloneSerialTransport(fake, rng=random.Random(0))

    with pytest.raises(CloneTransportError, match=r"after 3 attempts.*got 54"):
        transport.write_clone(b"\x01" * READ_BLOCK, segments=[TEST_SEGMENT])

    assert len(_block_frames(fake)) == 3
    assert fake.write_history[-1] != b"E", "session must not be closed after a failed write"
