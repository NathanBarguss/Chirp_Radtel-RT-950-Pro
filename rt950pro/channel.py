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

"""Channel record parsing for the RT-950 Pro."""
from __future__ import annotations

__all__ = [
    "ToneMode",
    "ToneSetting",
    "PowerLevel",
    "Bandwidth",
    "Modulation",
    "ChannelRecord",
]

from dataclasses import dataclass, field
from enum import Enum
import logging
import unicodedata
from typing import Optional

from .logging import get_logger

_LOG = get_logger("channel")

_DCS_CODES = [
    "D023N",
    "D025N",
    "D026N",
    "D031N",
    "D032N",
    "D036N",
    "D043N",
    "D047N",
    "D051N",
    "D053N",
    "D054N",
    "D065N",
    "D071N",
    "D072N",
    "D073N",
    "D074N",
    "D114N",
    "D115N",
    "D116N",
    "D122N",
    "D125N",
    "D131N",
    "D132N",
    "D134N",
    "D143N",
    "D145N",
    "D152N",
    "D155N",
    "D156N",
    "D162N",
    "D165N",
    "D172N",
    "D174N",
    "D205N",
    "D212N",
    "D223N",
    "D225N",
    "D226N",
    "D243N",
    "D244N",
    "D245N",
    "D246N",
    "D251N",
    "D252N",
    "D255N",
    "D261N",
    "D263N",
    "D265N",
    "D266N",
    "D271N",
    "D274N",
    "D306N",
    "D311N",
    "D315N",
    "D325N",
    "D331N",
    "D332N",
    "D343N",
    "D346N",
    "D351N",
    "D356N",
    "D364N",
    "D365N",
    "D371N",
    "D411N",
    "D412N",
    "D413N",
    "D423N",
    "D431N",
    "D432N",
    "D445N",
    "D446N",
    "D452N",
    "D454N",
    "D455N",
    "D462N",
    "D464N",
    "D465N",
    "D466N",
    "D503N",
    "D506N",
    "D516N",
    "D523N",
    "D526N",
    "D532N",
    "D546N",
    "D565N",
    "D606N",
    "D612N",
    "D624N",
    "D627N",
    "D631N",
    "D632N",
    "D645N",
    "D654N",
    "D662N",
    "D664N",
    "D703N",
    "D712N",
    "D723N",
    "D731N",
    "D732N",
    "D734N",
    "D743N",
    "D754N",
    "D023I",
    "D025I",
    "D026I",
    "D031I",
    "D032I",
    "D036I",
    "D043I",
    "D047I",
    "D051I",
    "D053I",
    "D054I",
    "D065I",
    "D071I",
    "D072I",
    "D073I",
    "D074I",
    "D114I",
    "D115I",
    "D116I",
    "D122I",
    "D125I",
    "D131I",
    "D132I",
    "D134I",
    "D143I",
    "D145I",
    "D152I",
    "D155I",
    "D156I",
    "D162I",
    "D165I",
    "D172I",
    "D174I",
    "D205I",
    "D212I",
    "D223I",
    "D225I",
    "D226I",
    "D243I",
    "D244I",
    "D245I",
    "D246I",
    "D251I",
    "D252I",
    "D255I",
    "D261I",
    "D263I",
    "D265I",
    "D266I",
    "D271I",
    "D274I",
    "D306I",
    "D311I",
    "D315I",
    "D325I",
    "D331I",
    "D332I",
    "D343I",
    "D346I",
    "D351I",
    "D356I",
    "D364I",
    "D365I",
    "D371I",
    "D411I",
    "D412I",
    "D413I",
    "D423I",
    "D431I",
    "D432I",
    "D445I",
    "D446I",
    "D452I",
    "D454I",
    "D455I",
    "D462I",
    "D464I",
    "D465I",
    "D466I",
    "D503I",
    "D506I",
    "D516I",
    "D523I",
    "D526I",
    "D532I",
    "D546I",
    "D565I",
    "D606I",
    "D612I",
    "D624I",
    "D627I",
    "D631I",
    "D632I",
    "D645I",
    "D654I",
    "D662I",
    "D664I",
    "D703I",
    "D712I",
    "D723I",
    "D731I",
    "D732I",
    "D734I",
    "D743I",
    "D754I",
]


class ToneMode(Enum):
    """Enumeration of tone encoding modes used by a channel slot."""

    OFF = "off"
    CTCSS = "ctcss"
    DCS = "dcs"


@dataclass(slots=True)
class ToneSetting:
    """Represents the transmit or receive tone configuration for a channel."""

    mode: ToneMode
    ctcss_hz: Optional[float] = None
    dcs_code: Optional[int] = None
    dcs_polarity: Optional[str] = None
    _raw_bytes: bytes = field(default=b"", repr=False, compare=False)
    _original_state: Optional[tuple] = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._original_state is None:
            object.__setattr__(self, "_original_state", self._state_tuple())

    def _state_tuple(self) -> tuple:
        return (
            self.mode,
            self.ctcss_hz,
            self.dcs_code,
            self.dcs_polarity,
        )

    @property
    def is_off(self) -> bool:
        """Return ``True`` when the tone is disabled."""

        return self.mode is ToneMode.OFF

    @classmethod
    def off(cls) -> "ToneSetting":
        """Build a tone setting with no encode/decode tones."""

        return cls(ToneMode.OFF)

    @classmethod
    def ctcss(cls, hz: float) -> "ToneSetting":
        """Build a CTCSS tone definition from the provided frequency."""

        return cls(ToneMode.CTCSS, ctcss_hz=hz)

    @classmethod
    def dcs(cls, code: int, polarity: str) -> "ToneSetting":
        """Build a DCS tone definition with the supplied code and polarity."""

        polarity = polarity.upper()
        # CHIRP spells inverted DCS polarity "R"; the radio's code table uses "I".
        if polarity == "R":
            polarity = "I"
        return cls(ToneMode.DCS, dcs_code=code, dcs_polarity=polarity)

    def to_display(self) -> str:
        """Return a human-readable representation of the tone setting."""

        if self.mode is ToneMode.OFF:
            return "OFF"
        if self.mode is ToneMode.CTCSS and self.ctcss_hz is not None:
            return f"{self.ctcss_hz:.1f}"
        if self.mode is ToneMode.DCS and self.dcs_code is not None and self.dcs_polarity:
            return f"D{self.dcs_code:03}{self.dcs_polarity.upper()}"
        return "OFF"

    def to_bytes(self) -> bytes:
        """Encode the tone information into the radio's two-byte format."""

        current_state = self._state_tuple()
        if (
            self._original_state is not None
            and current_state == self._original_state
            and self._raw_bytes
        ):
            return self._raw_bytes

        if self.mode is ToneMode.OFF:
            result = b"\x00\x00"
        elif self.mode is ToneMode.DCS and self.dcs_code is not None and self.dcs_polarity:
            token = f"D{self.dcs_code:03}{self.dcs_polarity.upper()}"
            try:
                index = _DCS_CODES.index(token)
            except ValueError as exc:
                raise ValueError(f"Unsupported DCS code {token}") from exc
            result = bytes((index + 1, 0x00))
        elif self.mode is ToneMode.CTCSS and self.ctcss_hz is not None:
            value = int(round(self.ctcss_hz * 10))
            if value <= 0 or value > 0xFFFF:
                raise ValueError(f"CTCSS value out of range: {self.ctcss_hz}")
            result = bytes((value & 0xFF, (value >> 8) & 0xFF))
        else:
            result = b"\x00\x00"

        object.__setattr__(self, "_raw_bytes", result)
        object.__setattr__(self, "_original_state", current_state)
        return result

    @staticmethod
    def from_bytes(raw: bytes) -> "ToneSetting":
        """Decode a two-byte tone structure into a :class:`ToneSetting`."""

        if len(raw) != 2:
            raise ValueError("Tone bytes must be length 2")
        first, second = raw
        if first == 0 and second == 0:
            setting = ToneSetting.off()
        elif second == 0:
            index = first
            if 1 <= index <= len(_DCS_CODES):
                token = _DCS_CODES[index - 1]
                code = int(token[1:4])
                polarity = token[4]
                setting = ToneSetting.dcs(code, polarity)
            else:
                _LOG.warning("Unknown DCS index %s", index)
                setting = ToneSetting.off()
        else:
            value = (second << 8) | first
            if value == 0xFFFF:
                setting = ToneSetting.off()
            else:
                hz = value / 10.0
                setting = ToneSetting.ctcss(hz)

        object.__setattr__(setting, "_raw_bytes", bytes(raw))
        object.__setattr__(setting, "_original_state", setting._state_tuple())
        return setting


class PowerLevel(Enum):
    """Output power selections available to the channel."""

    HIGH = 0
    MEDIUM = 1
    LOW = 2


class Bandwidth(Enum):
    """Bandwidth choices that control FM deviation."""

    NARROW = 0
    WIDE = 1


class Modulation(Enum):
    """Receive modulation mode of the channel."""

    FM = 0
    AM = 1


@dataclass(slots=True)
class ChannelRecord:
    """High-level representation of a 32-byte channel structure."""

    rx_hz: Optional[int]
    tx_hz: Optional[int]
    rx_tone: ToneSetting
    tx_tone: ToneSetting
    signalling_group: int
    ptt_id: int
    power: PowerLevel
    scrambler: int
    learn_fhss: bool
    bandwidth: Bandwidth
    encryption: int
    busy_lockout: bool
    scan_add: bool
    tx_enabled: bool
    rx_modulation: Modulation
    fhss_code: Optional[str]
    name: str
    _raw_bytes: bytes = field(default=b"", repr=False, compare=False)
    _original_state: Optional[tuple] = field(default=None, repr=False, compare=False)

    @classmethod
    def from_bytes(cls, data: bytes, *, logger: Optional[logging.Logger] = None) -> "ChannelRecord":
        """Parse a raw 32-byte channel record into a :class:`ChannelRecord`."""

        if len(data) != 32:
            raise ValueError("Channel record must be exactly 32 bytes")
        logger = logger or _LOG
        rx_hz = _decode_frequency(data[0:4])
        tx_hz = _decode_frequency(data[4:8])
        rx_tone = ToneSetting.from_bytes(data[8:10])
        tx_tone = ToneSetting.from_bytes(data[10:12])
        signalling_group = data[12] & 0x0F
        ptt_id = data[13] & 0x0F
        power = PowerLevel(min(data[14] & 0x0F, 2))
        scrambler = (data[14] >> 4) & 0x0F
        flags = data[15]
        learn_fhss = bool(flags & 0x80)
        # Radio uses bit6: 1 = NARROW, 0 = WIDE
        bandwidth = Bandwidth.NARROW if ((flags >> 6) & 0x01) else Bandwidth.WIDE
        encryption = (flags >> 4) & 0x03
        busy_lockout = bool(flags & 0x08)
        scan_add = bool(flags & 0x04)
        tx_enabled = bool(flags & 0x02)
        rx_modulation = Modulation(flags & 0x01)
        fhss_code = _decode_fhss_code(data[16:20])
        name = _decode_name(data[20:32], logger)
        record = cls(
            rx_hz=rx_hz,
            tx_hz=tx_hz,
            rx_tone=rx_tone,
            tx_tone=tx_tone,
            signalling_group=signalling_group,
            ptt_id=ptt_id,
            power=power,
            scrambler=scrambler,
            learn_fhss=learn_fhss,
            bandwidth=bandwidth,
            encryption=encryption,
            busy_lockout=busy_lockout,
            scan_add=scan_add,
            tx_enabled=tx_enabled,
            rx_modulation=rx_modulation,
            fhss_code=fhss_code,
            name=name,
        )
        record._raw_bytes = bytes(data)
        record._original_state = record._state_tuple()
        return record

    def to_bytes(self, *, logger: Optional[logging.Logger] = None) -> bytes:
        """Serialise the record back into the radio's 32-byte format."""

        logger = logger or _LOG

        current_state = self._state_tuple()
        if self._original_state is not None and current_state == self._original_state:
            if self._raw_bytes:
                return self._raw_bytes

        buf = bytearray(b"\xFF" * 32)
        buf[0:4] = _encode_frequency(self.rx_hz)
        buf[4:8] = _encode_frequency(self.tx_hz)
        buf[8:10] = self.rx_tone.to_bytes()
        buf[10:12] = self.tx_tone.to_bytes()
        buf[12] = self.signalling_group & 0x0F
        buf[13] = self.ptt_id & 0x0F
        scram = self.scrambler & 0x0F
        power_value = self.power.value & 0x0F
        buf[14] = (scram << 4) | power_value
        flags = 0
        if self.learn_fhss:
            flags |= 0x80
        # Radio uses bit6: 1 = NARROW, 0 = WIDE
        if self.bandwidth is Bandwidth.NARROW:
            flags |= 0x40
        flags |= (self.encryption & 0x03) << 4
        if self.busy_lockout:
            flags |= 0x08
        if self.scan_add:
            flags |= 0x04
        if self.tx_enabled:
            flags |= 0x02
        if self.rx_modulation is Modulation.AM:
            flags |= 0x01
        buf[15] = flags
        buf[16:20] = _encode_fhss_code(self.fhss_code)
        buf[20:32] = _encode_name(self.name, logger)
        result = bytes(buf)
        self._raw_bytes = result
        self._original_state = current_state
        return result

    def _state_tuple(self) -> tuple:
        return (
            self.rx_hz,
            self.tx_hz,
            self.rx_tone._state_tuple(),
            self.tx_tone._state_tuple(),
            self.signalling_group,
            self.ptt_id,
            self.power,
            self.scrambler,
            self.learn_fhss,
            self.bandwidth,
            self.encryption,
            self.busy_lockout,
            self.scan_add,
            self.tx_enabled,
            self.rx_modulation,
            self.fhss_code,
            self.name,
        )


def _decode_frequency(raw: bytes) -> Optional[int]:
    """Translate a four-byte packed BCD frequency into Hertz."""

    if len(raw) != 4:
        raise ValueError("Frequency field must be 4 bytes")
    if all(b in (0x00, 0xFF) for b in raw):
        return None
    digits = []
    for byte in raw:
        high = (byte >> 4) & 0x0F
        low = byte & 0x0F
        if high > 9 or low > 9:
            raise ValueError(f"Invalid BCD digit in frequency byte {byte:02X}")
        digits.append(high * 10 + low)
    value = 0
    for chunk in reversed(digits):
        value = value * 100 + chunk
    return value * 10


def _encode_frequency(hz: Optional[int]) -> bytes:
    """Pack an integer frequency in Hertz into the four-byte BCD layout."""

    if hz is None or hz == 0:
        return b"\xFF" * 4
    if hz % 10 != 0:
        raise ValueError("Frequency must align to 10 Hz steps")
    value = hz // 10
    digits = []
    for _ in range(4):
        digits.append(value % 100)
        value //= 100
    if value:
        raise ValueError("Frequency value exceeds 32-bit packed BCD range")
    out = bytearray()
    for chunk in digits:
        high = chunk // 10
        low = chunk % 10
        out.append((high << 4) | low)
    return bytes(out)


def _decode_fhss_code(raw: bytes) -> Optional[str]:
    """Extract the six-character FHSS identifier from its four-byte field."""

    if len(raw) != 4:
        raise ValueError("FHSS field must be 4 bytes")
    if raw[3] != 0xA0:
        return None
    digits = "0123456789ABCDEF"
    parts = []
    for byte in reversed(raw[:3]):
        parts.append(digits[(byte >> 4) & 0x0F])
        parts.append(digits[byte & 0x0F])
    return "".join(parts)


def _encode_fhss_code(code: Optional[str]) -> bytes:
    """Encode the FHSS identifier into the radio's storage format."""

    buf = bytearray(b"\xFF\xFF\xFF\xFF")
    if not code:
        return bytes(buf)
    token = code.strip().upper()
    if len(token) != 6 or any(c not in "0123456789ABCDEF" for c in token):
        raise ValueError(f"FHSS code must be 6 hex characters: {code}")
    buf[3] = 0xA0
    digits = "0123456789ABCDEF"
    buf[2] = (digits.index(token[0]) << 4) | digits.index(token[1])
    buf[1] = (digits.index(token[2]) << 4) | digits.index(token[3])
    buf[0] = (digits.index(token[4]) << 4) | digits.index(token[5])
    return bytes(buf)


def _decode_name(raw: bytes, logger: logging.Logger) -> str:
    """Decode a GB2312-encoded channel name from the twelve-byte field."""

    data = bytearray()
    for byte in raw:
        if byte in (0xFF, 0x00):
            break
        data.append(byte)
    if not data:
        return ""
    try:
        return data.decode("gb2312")
    except UnicodeDecodeError:
        name = data.decode("gb2312", errors="replace")
        logger.warning("Channel name contained invalid GB2312 bytes: %s", data.hex())
        return name


def _encode_name(name: str, logger: logging.Logger) -> bytes:
    """Encode a display name using GB2312 with logged fallbacks when needed."""

    if not name:
        return b"\xFF" * 12
    working = name
    try:
        working.encode("gb2312")
    except UnicodeEncodeError:
        normalised = unicodedata.normalize("NFKD", working)
        ascii_only = normalised.encode("ascii", errors="ignore").decode("ascii")
        if not ascii_only:
            ascii_only = normalised.encode("gb2312", errors="ignore").decode("gb2312", errors="ignore")
        logger.warning(
            "Channel name '%s' contains non-GB2312 characters; stored as '%s'",
            name,
            ascii_only,
        )
        working = ascii_only
    encoded = bytearray()
    for char in working:
        try:
            char_bytes = char.encode("gb2312")
        except UnicodeEncodeError:
            logger.warning(
                "Dropping character '%s' from channel name '%s' during encoding",
                char,
                name,
            )
            continue
        if len(encoded) + len(char_bytes) > 12:
            logger.warning("Channel name '%s' truncated to 12 bytes", name)
            break
        encoded.extend(char_bytes)
    while len(encoded) < 12:
        encoded.append(0xFF)
    return bytes(encoded[:12])
