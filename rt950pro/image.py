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

"""Representation of RT-950 Pro radio images."""
from __future__ import annotations

__all__ = [
    "CHANNEL_COUNT",
    "CHANNEL_SIZE",
    "CHANNEL_SECTION_BYTES",
    "RadioImage",
]

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence
import logging

from .channel import ChannelRecord
from .logging import get_logger
from .sections import (
    APRSSettings,
    DTMFSettings,
    FunctionSettings,
    ModulationSettings,
    VFOSettings,
    encode_aprs_section,
    encode_dtmf_section,
    encode_function_section,
    encode_modulation_sections,
    encode_vfo_section,
    parse_aprs_section,
    parse_dtmf_section,
    parse_function_section,
    parse_modulation_sections,
    parse_vfo_section,
)

_CHANNEL_LOG = get_logger("image")

CHANNEL_COUNT = 960
"""Total number of memory channels supported by the radio."""

CHANNEL_SIZE = 32
"""Size in bytes of a single channel record."""

CHANNEL_SECTION_BYTES = CHANNEL_COUNT * CHANNEL_SIZE
"""Total byte length of the channel section within the clone image."""

VFO_DATA_BYTES = 96
VFO_SEGMENT_BYTES = 0x100
FUNCTION_DATA_BYTES = 96
FUNCTION_SEGMENT_BYTES = 0x100
DTMF_DATA_BYTES = 384
DTMF_SEGMENT_BYTES = 0x200
MODULATION_PARAM_DATA_BYTES = 256
MODULATION_PARAM_SEGMENT_BYTES = 0x200
MODULATION_NAME_SEGMENT_BYTES = 0x300
APRS_SEGMENT_BYTES = 0x80

KNOWN_SEGMENT_BYTES = (
    VFO_SEGMENT_BYTES
    + FUNCTION_SEGMENT_BYTES
    + DTMF_SEGMENT_BYTES
    + MODULATION_PARAM_SEGMENT_BYTES
    + MODULATION_NAME_SEGMENT_BYTES
    + APRS_SEGMENT_BYTES
)


def _chunk(iterable: Sequence[int], size: int) -> Iterable[bytes]:
    """Yield fixed-size chunks from `iterable` using the provided `size`."""

    for i in range(0, len(iterable), size):
        chunk = iterable[i : i + size]
        if len(chunk) == size:
            yield bytes(chunk)


@dataclass
class RadioImage:
    """Container for clone image sections."""

    channels: List[ChannelRecord]
    vfo: Optional[List[VFOSettings]] = None
    function: Optional[FunctionSettings] = None
    dtmf: Optional[DTMFSettings] = None
    modulation: Optional[ModulationSettings] = None
    aprs: Optional[APRSSettings] = None
    remainder: bytes = b""

    @classmethod
    def from_bytes(
        cls,
        blob: bytes,
        *,
        logger: Optional[logging.Logger] = None,
    ) -> "RadioImage":
        """Parse a raw clone image into structured data."""

        logger = logger or _CHANNEL_LOG
        if len(blob) < CHANNEL_SECTION_BYTES:
            raise ValueError(
                f"Clone image is too small ({len(blob)} bytes); expected at least {CHANNEL_SECTION_BYTES}"
            )

        channel_bytes = memoryview(blob)[:CHANNEL_SECTION_BYTES]
        channels: List[ChannelRecord] = []
        for index, chunk in enumerate(_chunk(channel_bytes, CHANNEL_SIZE)):
            try:
                record = ChannelRecord.from_bytes(chunk, logger=logger)
            except ValueError as exc:
                # One unreadable slot must not make the whole radio unreadable.
                # Present it as empty but keep the original bytes so a later
                # upload leaves the slot untouched unless the user edits it.
                logger.warning(
                    "Channel %d could not be decoded (%s); showing as empty. Raw: %s",
                    index, exc, bytes(chunk).hex(),
                )
                record = ChannelRecord.from_bytes(b"\xFF" * CHANNEL_SIZE, logger=logger)
                record._raw_bytes = bytes(chunk)
            channels.append(record)

        offset = CHANNEL_SECTION_BYTES
        vfo: Optional[List[VFOSettings]] = None
        function: Optional[FunctionSettings] = None
        dtmf: Optional[DTMFSettings] = None
        modulation: Optional[ModulationSettings] = None
        aprs: Optional[APRSSettings] = None

        if len(blob) >= offset + VFO_SEGMENT_BYTES:
            vfo_segment = bytes(blob[offset : offset + VFO_SEGMENT_BYTES])
            vfo = parse_vfo_section(vfo_segment[:VFO_DATA_BYTES])
        offset += VFO_SEGMENT_BYTES

        if len(blob) >= offset + FUNCTION_SEGMENT_BYTES:
            function_segment = bytes(blob[offset : offset + FUNCTION_SEGMENT_BYTES])
            function = parse_function_section(function_segment[:FUNCTION_DATA_BYTES])
        offset += FUNCTION_SEGMENT_BYTES

        if len(blob) >= offset + DTMF_SEGMENT_BYTES:
            dtmf_segment = bytes(blob[offset : offset + DTMF_SEGMENT_BYTES])
            dtmf = parse_dtmf_section(dtmf_segment[:DTMF_DATA_BYTES])
        offset += DTMF_SEGMENT_BYTES

        if len(blob) >= offset + MODULATION_PARAM_SEGMENT_BYTES + MODULATION_NAME_SEGMENT_BYTES:
            params_segment = bytes(blob[offset : offset + MODULATION_PARAM_SEGMENT_BYTES])
            names_segment = bytes(
                blob[
                    offset
                    + MODULATION_PARAM_SEGMENT_BYTES : offset
                    + MODULATION_PARAM_SEGMENT_BYTES
                    + MODULATION_NAME_SEGMENT_BYTES
                ]
            )
            modulation = parse_modulation_sections(
                params_segment[:MODULATION_PARAM_DATA_BYTES],
                names_segment[:MODULATION_NAME_SEGMENT_BYTES],
            )
        offset += MODULATION_PARAM_SEGMENT_BYTES + MODULATION_NAME_SEGMENT_BYTES

        if len(blob) >= offset + APRS_SEGMENT_BYTES:
            aprs_data = bytes(blob[offset : offset + APRS_SEGMENT_BYTES])
            aprs = parse_aprs_section(aprs_data[:APRS_SEGMENT_BYTES])

        remainder = bytes(blob[CHANNEL_SECTION_BYTES:])
        return cls(
            channels=channels,
            vfo=vfo,
            function=function,
            dtmf=dtmf,
            modulation=modulation,
            aprs=aprs,
            remainder=remainder,
        )

    @classmethod
    def from_file(
        cls,
        path: Path,
        *,
        logger: Optional[logging.Logger] = None,
    ) -> "RadioImage":
        """Load an image from `path` then parse it via :meth:rom_bytes."""

        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ValueError(f"Unable to read image file {path}: {exc}") from exc
        return cls.from_bytes(data, logger=logger)

    def to_bytes(self, *, logger: Optional[logging.Logger] = None) -> bytes:
        """Serialise the image back into clone format."""

        logger = logger or _CHANNEL_LOG
        if len(self.channels) != CHANNEL_COUNT:
            raise ValueError(
                f"Image must contain {CHANNEL_COUNT} channels; has {len(self.channels)}"
            )

        tail = bytearray(self.remainder)
        if len(tail) < KNOWN_SEGMENT_BYTES:
            tail.extend(b"\xFF" * (KNOWN_SEGMENT_BYTES - len(tail)))

        offset = 0

        vfo_segment = bytearray(tail[offset : offset + VFO_SEGMENT_BYTES])
        vfo_encoded = encode_vfo_section(self.vfo, bytes(vfo_segment[:VFO_DATA_BYTES]))
        vfo_segment[:VFO_DATA_BYTES] = vfo_encoded
        tail[offset : offset + VFO_SEGMENT_BYTES] = vfo_segment
        offset += VFO_SEGMENT_BYTES

        function_segment = bytearray(tail[offset : offset + FUNCTION_SEGMENT_BYTES])
        function_encoded = encode_function_section(
            self.function, bytes(function_segment[:FUNCTION_DATA_BYTES])
        )
        function_segment[:FUNCTION_DATA_BYTES] = function_encoded
        tail[offset : offset + FUNCTION_SEGMENT_BYTES] = function_segment
        offset += FUNCTION_SEGMENT_BYTES

        dtmf_segment = bytearray(tail[offset : offset + DTMF_SEGMENT_BYTES])
        dtmf_encoded = encode_dtmf_section(self.dtmf, bytes(dtmf_segment[:DTMF_DATA_BYTES]))
        dtmf_segment[:DTMF_DATA_BYTES] = dtmf_encoded
        tail[offset : offset + DTMF_SEGMENT_BYTES] = dtmf_segment
        offset += DTMF_SEGMENT_BYTES

        params_offset = offset
        names_offset = params_offset + MODULATION_PARAM_SEGMENT_BYTES

        params_segment = bytearray(tail[params_offset : params_offset + MODULATION_PARAM_SEGMENT_BYTES])
        names_segment = bytearray(tail[names_offset : names_offset + MODULATION_NAME_SEGMENT_BYTES])
        params_raw = bytes(params_segment[:MODULATION_PARAM_DATA_BYTES])
        names_raw = bytes(names_segment[:MODULATION_NAME_SEGMENT_BYTES])
        mod_params, mod_names = encode_modulation_sections(
            self.modulation, params_raw, names_raw
        )
        params_segment[:MODULATION_PARAM_DATA_BYTES] = mod_params
        names_segment[:MODULATION_NAME_SEGMENT_BYTES] = mod_names
        tail[params_offset : params_offset + MODULATION_PARAM_SEGMENT_BYTES] = params_segment
        tail[names_offset : names_offset + MODULATION_NAME_SEGMENT_BYTES] = names_segment
        offset = names_offset + MODULATION_NAME_SEGMENT_BYTES

        aprs_segment = bytearray(tail[offset : offset + APRS_SEGMENT_BYTES])
        aprs_encoded = encode_aprs_section(self.aprs, bytes(aprs_segment))
        tail[offset : offset + APRS_SEGMENT_BYTES] = aprs_encoded

        buffer = bytearray(CHANNEL_SECTION_BYTES + len(tail))
        for index, channel in enumerate(self.channels):
            start = index * CHANNEL_SIZE
            buffer[start : start + CHANNEL_SIZE] = channel.to_bytes(logger=logger)

        buffer[CHANNEL_SECTION_BYTES:] = tail
        return bytes(buffer)

    def empty_slot_indexes(self) -> List[int]:
        """Return indexes of channels that contain no receive frequency."""

        return [idx for idx, channel in enumerate(self.channels) if channel.rx_hz is None]

    def iter_populated_channels(self) -> Iterable[tuple[int, ChannelRecord]]:
        """Iterate over channels that have a defined receive frequency."""

        for index, channel in enumerate(self.channels):
            if channel.rx_hz is not None:
                yield index, channel

    def save(self, path: Path, *, logger: Optional[logging.Logger] = None) -> None:
        """Write the current image representation to `path`."""

        data = self.to_bytes(logger=logger)
        try:
            path.write_bytes(data)
        except OSError as exc:
            raise ValueError(f"Unable to write image file {path}: {exc}") from exc

