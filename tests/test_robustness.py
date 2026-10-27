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

"""Regression tests for DCS polarity, airband retune, and tolerant parsing.

These build channel records by hand so they run without ``dumps/clean.bin``
and without CHIRP installed.
"""

from rt950pro.channel import _DCS_CODES, ChannelRecord, ToneSetting, _encode_frequency
from rt950pro.chirp_driver import RT950ProRadio, chirp_common
from rt950pro.image import CHANNEL_SECTION_BYTES, CHANNEL_SIZE, RadioImage

TONE_OFF = b"\x00\x00"


def _dcs_bytes(token: str) -> bytes:
    """Return the radio's two-byte encoding for a ``_DCS_CODES`` token."""

    return bytes((_DCS_CODES.index(token) + 1, 0x00))


def _channel(rx_hz: int, tx_hz: int, rx_tone: bytes, tx_tone: bytes, flags: int = 0x06) -> ChannelRecord:
    data = bytearray(b"\xFF" * CHANNEL_SIZE)
    data[0:4] = _encode_frequency(rx_hz)
    data[4:8] = _encode_frequency(tx_hz)
    data[8:10] = rx_tone
    data[10:12] = tx_tone
    data[12:16] = bytes((0x00, 0x00, 0x00, flags))  # flags: tx_enabled | scan_add
    return ChannelRecord.from_bytes(bytes(data))


def _driver_with(record: ChannelRecord) -> RT950ProRadio:
    driver = RT950ProRadio()
    driver._image = RadioImage(channels=[record])
    return driver


def test_dcs_accepts_chirp_reverse_polarity_spelling():
    assert ToneSetting.dcs(23, "R").to_bytes() == _dcs_bytes("D023I")
    assert ToneSetting.dcs(23, "I").to_bytes() == _dcs_bytes("D023I")
    assert ToneSetting.dcs(23, "N").to_bytes() == _dcs_bytes("D023N")


def test_inverted_dcs_from_radio_reads_as_chirp_polarity():
    record = _channel(146_520_000, 146_520_000, _dcs_bytes("D023N"), _dcs_bytes("D023I"))
    mem = _driver_with(record).get_memory(0)

    assert mem.tmode == "DTCS"
    assert mem.dtcs == 23
    assert mem.rx_dtcs == 23
    assert mem.dtcs_polarity == "RN"


def test_dcs_polarity_round_trips_through_set_memory():
    driver = _driver_with(_channel(146_520_000, 146_520_000, TONE_OFF, TONE_OFF))
    mem = driver.get_memory(0)
    mem.tmode = "DTCS"
    mem.dtcs = 23
    mem.rx_dtcs = 23
    mem.dtcs_polarity = "RR"

    driver.set_memory(mem)

    encoded = driver._image.channels[0].to_bytes()
    assert encoded[8:10] == _dcs_bytes("D023I")
    assert encoded[10:12] == _dcs_bytes("D023I")
    assert driver.get_memory(0).dtcs_polarity == "RR"


def test_retune_from_airband_re_enables_tx():
    driver = _driver_with(_channel(146_520_000, 146_520_000, TONE_OFF, TONE_OFF))
    mem = driver.get_memory(0)

    mem.freq = 118_500_000
    mem.mode = "AM"
    driver.set_memory(mem)
    assert driver._image.channels[0].tx_enabled is False

    mem.freq = 146_520_000
    mem.mode = "FM"
    driver.set_memory(mem)
    assert driver._image.channels[0].tx_enabled is True


def test_corrupt_channel_is_shown_empty_and_left_untouched():
    bad = bytearray(b"\xFF" * CHANNEL_SIZE)
    bad[0] = 0x0A  # non-BCD nibble in the RX frequency
    blob = bytes(bad) + b"\xFF" * (CHANNEL_SECTION_BYTES - CHANNEL_SIZE)

    image = RadioImage.from_bytes(blob)

    assert image.channels[0].rx_hz is None
    assert image.channels[1].rx_hz is None
    # The slot is written back exactly as it was read.
    assert image.to_bytes()[:CHANNEL_SIZE] == bytes(bad)


def test_features_available_without_chirp_installed():
    assert 23 in chirp_common.DTCS_CODES
    rf = RT950ProRadio().get_features()
    assert rf.valid_dtcs_codes
