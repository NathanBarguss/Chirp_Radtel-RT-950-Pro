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

"""CHIRP driver integration for the Radtel RT-950 Pro."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Tuple

from .channel import _DCS_CODES, Bandwidth, ChannelRecord, Modulation, PowerLevel, ToneMode
from .image import CHANNEL_COUNT, CHANNEL_SECTION_BYTES, CHANNEL_SIZE, RadioImage
from .settings_api import (
    SettingsError,
    aprs_keys,
    function_keys,
    get_aprs_value,
    get_dtmf_code_group,
    get_dtmf_current_id,
    get_dtmf_ptt_mode,
    get_function_value,
    set_aprs_value,
    set_dtmf_code_group,
    set_dtmf_current_id,
    set_dtmf_ptt_mode,
    set_function_value,
)
from .transport import CloneSerialTransport, DEFAULT_SEGMENTS, READ_BLOCK

LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CHIRP compatibility layer -------------------------------------------------

# ---------------------------------------------------------------------------

try:  # pragma: no cover - executed when real CHIRP is available
    from chirp import chirp_common, directory, errors, memmap  # type: ignore
    from chirp.settings import (
        RadioSetting,
        RadioSettingGroup,
        RadioSettingValueBoolean,
        RadioSettingValueInteger,
        RadioSettingValueList,
        RadioSettingValueString,
        RadioSettings,
    )

except ImportError:  # pragma: no cover - fallback for local development

    class _Memory:
        def __init__(self) -> None:
            self.number = 0
            self.freq = 0
            self.offset = 0
            self.duplex = ""
            self.tmode = ""
            self.rtone = 0.0
            self.ctone = 0.0
            self.dtcs = 0
            self.dtcs_pol = "N"
            self.mode = "FM"
            self.power = ""
            self.skip = ""
            self.name = ""
            self.empty = False
            self.extra = {}

    class _RadioFeatures:
        def __init__(self) -> None:
            self.has_bank = False
            self.has_bank_names = False
            self.has_name = True
            self.has_ctone = True
            self.has_dtcs = True
            self.has_dtcs_polarity = True
            self.has_mode = True
            self.has_offset = True
            self.has_tuning_step = False
            self.can_delete = True
            self.can_odd_split = True
            self.memory_bounds = (0, 0)
            self.valid_bands = []
            self.valid_duplexes = ["", "+", "-", "split"]
            self.valid_tmodes = ["", "Tone", "TSQL", "DTCS"]
            self.valid_modes = ["FM", "NFM", "AM"]
            self.valid_power_levels = ["Low", "Medium", "High"]
            self.valid_skips = ["", "S"]
            self.valid_name_length = 12

    class _CloneModeRadio:
        VENDOR = ""
        MODEL = ""
        BAUD_RATE = 115200

        def __init__(self, *args, **kwargs) -> None:
            self.pipe = kwargs.get("pipe")

    class _Directory:
        @staticmethod
        def register(cls):
            return cls

    class _Errors:

        class RadioError(Exception):
            pass

    class _MemoryMapBytes(bytearray):
        def get_packed(self):
            return bytes(self)

    class memmap:  # type: ignore
        MemoryMapBytes = _MemoryMapBytes

    class _PowerLevel(str):
        def __new__(cls, label, watts=0.0):
            obj = str.__new__(cls, label)
            obj.watts = watts
            return obj

    class _RadioSettingValue:
        def __init__(self, value=None):
            self._value = value

        def initialize(self):
            pass

        def set_value(self, value):
            self._value = value

        def get_value(self):
            return self._value

        def queue_current(self, value):
            self._value = value

        def __int__(self):
            return int(self._value)

        def __bool__(self):
            return bool(self._value)

        def __str__(self):
            return str(self._value)

    class RadioSettingValueBoolean(_RadioSettingValue):
        def __init__(self, current, mem_vals=(0, 1)):
            super().__init__(bool(current))

        def set_value(self, value):
            super().set_value(bool(value))

    class RadioSettingValueInteger(_RadioSettingValue):
        def __init__(self, minval, maxval, current, step=1):
            self._min = minval
            self._max = maxval
            super().__init__(int(current))

        def set_value(self, value):
            value = int(value)
            if value < self._min or value > self._max:
                raise ValueError
            super().set_value(value)

    class RadioSettingValueList(_RadioSettingValue):
        def __init__(self, choices, current_index=0):
            self.choices = list(choices)
            index = int(current_index) if self.choices else 0
            super().__init__(self.choices[index] if self.choices else None)

        def set_value(self, value):
            if isinstance(value, int):
                if value < 0 or value >= len(self.choices):
                    raise ValueError
                self._value = self.choices[value]
            else:
                if value not in self.choices:
                    raise ValueError
                self._value = value

        def __int__(self):
            if self._value in self.choices:
                return self.choices.index(self._value)
            raise ValueError

    class RadioSettingValueString(_RadioSettingValue):
        def __init__(self, minlength, maxlength, current, autopad=True, charset=None, mem_pad_char=' '):
            self.maxlength = maxlength
            self.autopad = autopad
            self.mem_pad_char = mem_pad_char
            super().__init__(current or "")

        def set_value(self, value):
            value = str(value)
            if len(value) > self.maxlength:
                raise ValueError
            super().set_value(value)

    class RadioSettingGroup:
        def __init__(self, name, label, *elements):
            self.name = name
            self.label = label
            self._children = []
            for element in elements:
                self.append(element)

        def append(self, element):
            self._children.append(element)

        def __iter__(self):
            return iter(self._children)

        def get(self, name, default=None):
            for child in self._children:
                if isinstance(child, RadioSetting) and child.name == name:
                    value_obj = getattr(child, 'value', None)
                    if hasattr(value_obj, 'get_value'):
                        return value_obj.get_value()
                    return value_obj
            return default

        def __getitem__(self, name):
            result = self.get(name, None)
            if result is None:
                raise KeyError(name)
            return result

        def walk(self):
            for child in self._children:
                if isinstance(child, RadioSetting):
                    yield child
                elif isinstance(child, RadioSettingGroup):
                    yield from child.walk()

    class RadioSetting(RadioSettingGroup):
        def __init__(self, name, label, value):
            super().__init__(name, label, value)
            self._value = value
            self._apply = None
        @property
        def value(self):
            return self._value

        def set_apply_callback(self, callback, *args):
            self._apply = lambda: callback(self, *args)

        def has_apply_callback(self):
            return self._apply is not None

        def run_apply_callback(self):
            if self._apply:
                self._apply()

        def get_name(self):
            return self.name

    class RadioSettings(RadioSettingGroup):
        def __init__(self, *groups):
            super().__init__('root', 'root', *groups)

    class chirp_common:  # type: ignore
        Memory = _Memory
        RadioFeatures = _RadioFeatures
        CloneModeRadio = _CloneModeRadio
        PowerLevel = _PowerLevel
        # get_features() and _apply_tones() read these from chirp_common; the
        # fallback must provide them so the driver also works without CHIRP
        # installed (tests, CLI tooling).
        DTCS_CODES = tuple(sorted({int(token[1:4]) for token in _DCS_CODES}))
        CHARSET_ASCII = "".join(chr(code) for code in range(32, 127))

        @staticmethod
        def format_freq(hz):
            hz = 0 if hz in (None, "") else int(hz)
            return f"{hz / 1_000_000:.6f}"

        @staticmethod
        def parse_freq(text):
            value = str(text).strip().lower()
            if not value:
                return 0
            multiplier = 1_000_000
            if value.endswith('mhz'):
                value = value[:-3].strip()
                multiplier = 1_000_000
            elif value.endswith('khz'):
                value = value[:-3].strip()
                multiplier = 1_000
            elif value.endswith('hz'):
                value = value[:-2].strip()
                multiplier = 1
            try:
                numeric = float(value)
            except ValueError as exc:
                raise ValueError(f"Invalid frequency '{text}'") from exc
            if numeric < 0:
                raise ValueError('Frequency must be non-negative')
            if multiplier == 1 and numeric >= 1_000_000:
                return int(round(numeric))
            if multiplier == 1_000_000 and numeric >= 1_000_000:
                return int(round(numeric))
            return int(round(numeric * multiplier))
    directory = _Directory()  # type: ignore
    errors = _Errors()  # type: ignore

    class settings:  # type: ignore
        RadioSettings = RadioSettings
        RadioSettingGroup = RadioSettingGroup
        RadioSetting = RadioSetting
        RadioSettingValueBoolean = RadioSettingValueBoolean
        RadioSettingValueInteger = RadioSettingValueInteger
        RadioSettingValueList = RadioSettingValueList
        RadioSettingValueString = RadioSettingValueString
    RadioSettings = RadioSettings
    RadioSettingGroup = RadioSettingGroup
    RadioSetting = RadioSetting
    RadioSettingValueBoolean = RadioSettingValueBoolean
    RadioSettingValueInteger = RadioSettingValueInteger
    RadioSettingValueList = RadioSettingValueList
    RadioSettingValueString = RadioSettingValueString

# ---------------------------------------------------------------------------
# Helper utilities ----------------------------------------------------------

# ---------------------------------------------------------------------------

_SEGMENT_LENGTH = sum(segment.length for segment in DEFAULT_SEGMENTS)

_CHIRP_POWER_LEVELS = [
    chirp_common.PowerLevel('Low', watts=1.0),
    chirp_common.PowerLevel('Medium', watts=5.0),
    chirp_common.PowerLevel('High', watts=10.0),
]

_POWER_ENUM_TO_CHIRP = {
    PowerLevel.LOW: _CHIRP_POWER_LEVELS[0],
    PowerLevel.MEDIUM: _CHIRP_POWER_LEVELS[1],
    PowerLevel.HIGH: _CHIRP_POWER_LEVELS[2],
}

_POWER_LABEL_TO_ENUM = {str(level).upper(): enum for enum, level in _POWER_ENUM_TO_CHIRP.items()}

_FUNCTION_UI = {
    'sql': {'label': 'Squelch Level', 'type': 'int', 'min': 0, 'max': 9},
    'save_mode': {'label': 'Battery Save', 'type': 'int', 'min': 0, 'max': 3},
    'vox': {'label': 'VOX Gain', 'type': 'int', 'min': 0, 'max': 9},
    'vox_delay': {'label': 'VOX Delay', 'type': 'int', 'min': 0, 'max': 9},
    'auto_backlight': {'label': 'Auto Backlight', 'type': 'int', 'min': 0, 'max': 9},
    'tot': {'label': 'Time-out Timer', 'type': 'int', 'min': 0, 'max': 9},
    'beep_prompt': {'label': 'Key Beep', 'type': 'bool'},
    'voice_prompt': {'label': 'Voice Prompt', 'type': 'enum', 'choices': [('Off', 0), ('English', 1), ('Chinese', 2)]},
    'language': {'label': 'Menu Language', 'type': 'enum', 'choices': [('English', 0), ('Chinese', 1), ('Other', 2)]},
    'dtmf_mode': {'label': 'DTMF Mode', 'type': 'enum', 'choices': [('Off', 0), ('DT-ST', 1), ('ANI-ID', 2), ('DTMF', 3)]},
    'scan_mode': {'label': 'Scan Mode', 'type': 'enum', 'choices': [('Time', 0), ('Carrier', 1), ('Search', 2)]},
    'ptt_id': {'label': 'PTT ID', 'type': 'enum', 'choices': [('Off', 0), ('BOT', 1), ('EOT', 2), ('Both', 3)]},
    'display_mode_a': {'label': 'Display Mode A', 'type': 'enum', 'choices': [('Channel', 0), ('Frequency', 1), ('Name', 2)]},
    'display_mode_b': {'label': 'Display Mode B', 'type': 'enum', 'choices': [('Channel', 0), ('Frequency', 1), ('Name', 2)]},
    'display_mode_c': {'label': 'Display Mode C', 'type': 'enum', 'choices': [('Channel', 0), ('Frequency', 1), ('Name', 2)]},
    'auto_key_lock': {'label': 'Auto Key Lock', 'type': 'bool'},
    'alarm_mode': {'label': 'Alarm Mode', 'type': 'enum', 'choices': [('Local', 0), ('Remote', 1), ('Tone', 2)]},
    'alarm_sound': {'label': 'Alarm Sound', 'type': 'enum', 'choices': [('Off', 0), ('Type 1', 1), ('Type 2', 2)]},
    'tail_noise_clear': {'label': 'Tail Noise Clear', 'type': 'bool'},
    'pass_repeater_noise_clear': {'label': 'Repeater Noise Clear', 'type': 'bool'},
    'sound_tx_end': {'label': 'Roger Beep', 'type': 'bool'},
    'fm_radio': {'label': 'FM Radio', 'type': 'bool'},
    'lock_keyboard': {'label': 'Keypad Lock', 'type': 'bool'},
    'power_on_message': {'label': 'Power-on Message', 'type': 'enum', 'choices': [('Full', 0), ('Message', 1), ('Voltage', 2)]},
    'bt_write_switch': {'label': 'Bluetooth Enable', 'type': 'bool'},
    'vox_switch': {'label': 'VOX Enable', 'type': 'bool'},
}

_APRS_UI = {
    'aprs_switch': {'label': 'APRS Enable', 'type': 'bool'},
    'gps_switch': {'label': 'GPS Enable', 'type': 'bool'},
    'call_sign': {'label': 'Callsign', 'type': 'string', 'length': 6},
    'ssid': {'label': 'SSID', 'type': 'enum', 'choices': [(str(i), i) for i in range(16)]},
    'aprs_priority': {'label': 'Priority', 'type': 'enum', 'choices': [('Low', 0), ('Normal', 1), ('High', 2)]},
    'data_tx_delay': {'label': 'Data TX Delay', 'type': 'int', 'min': 0, 'max': 9},
    'aprs_decode_prompt_tone': {'label': 'RX Prompt Tone', 'type': 'bool'},
    'aprs_rx_auto_popup': {'label': 'RX Auto Popup', 'type': 'bool'},
    'aprs_forward_channel': {'label': 'Forward Channel', 'type': 'enum', 'choices': [(str(i), i) for i in range(16)]},
    'aprs_wait_forward': {'label': 'Forward Wait', 'type': 'int', 'min': 0, 'max': 9},
    'custom_messages': {'label': 'Custom Message', 'type': 'string', 'length': 40},
}

_DTMF_GROUP_LIMIT = 5

_DTMF_ID_MAXLEN = 5

_DTMF_CODE_MAXLEN = 6

_DTMF_MODE_CHOICES = [('Off', 0), ('BOT', 1), ('EOT', 2), ('Both', 3)]


_VFO_LABELS = ['A', 'B', 'C']
_VFO_OFFSET_CHOICES = [('Simplex', 0), ('Plus (+)', 1), ('Minus (-)', 2), ('Split', 3)]
_VFO_BANDWIDTH_CHOICES = [('Narrow', Bandwidth.NARROW), ('Wide', Bandwidth.WIDE)]
_VFO_MODULATION_CHOICES = [('FM', Modulation.FM), ('AM', Modulation.AM)]
_VFO_POWER_CHOICES = [('Low', PowerLevel.LOW), ('Medium', PowerLevel.MEDIUM), ('High', PowerLevel.HIGH)]
_VFO_ENCRYPTION_CHOICES = [('Off', 0), ('Type 1', 1), ('Type 2', 2), ('Type 3', 3)]
_VFO_BAND_CHOICES = [
    ('50-76 MHz', 0),
    ('108-136 MHz', 1),
    ('137-174 MHz', 2),
    ('174-350 MHz', 3),
    ('350-400 MHz', 4),
    ('400-470 MHz', 5),
    ('470-600 MHz', 6),
]
_VFO_STEP_CHOICES = [
    ('2.5 kHz', 0),
    ('5.0 kHz', 1),
    ('6.25 kHz', 2),
    ('10.0 kHz', 3),
    ('12.5 kHz', 4),
    ('25.0 kHz', 5),
]



def _build_memory_extra(channel: ChannelRecord) -> RadioSettingGroup:
    group = RadioSettingGroup('rt950_extra', 'RT-950 Pro Extras')
    scrambler_value = RadioSettingValueInteger(0, 8, channel.scrambler or 0)
    scrambler = RadioSetting('scrambler', 'Scrambler Code', scrambler_value)
    group.append(scrambler)
    encryption_raw = channel.encryption if channel.encryption is not None else 0
    encryption_value = RadioSettingValueInteger(0, 3, encryption_raw)
    encryption = RadioSetting('encryption', 'Encryption Mode', encryption_value)
    group.append(encryption)
    return group


def _extract_memory_extra(mem) -> Dict[str, int]:
    extras: Dict[str, int] = {}
    container = getattr(mem, 'extra', None)
    if isinstance(container, dict):
        for key in ('scrambler', 'encryption'):
            value = container.get(key)
            if value is not None:
                extras[key] = value
        return extras
    if container is None:
        return extras
    if hasattr(container, 'walk'):
        iterable = list(container.walk())
    else:
        try:
            iterable = list(container)
        except TypeError:
            iterable = []
    for setting in iterable:
        if not isinstance(setting, RadioSetting):
            continue
        value_obj = getattr(setting, 'value', None)
        if hasattr(value_obj, 'get_value'):
            raw = value_obj.get_value()
        else:
            raw = value_obj
        extras[setting.get_name()] = raw
    return extras





def _format_frequency(hz: Optional[int]) -> str:
    if hz in (None, 0):
        return ""
    return chirp_common.format_freq(hz)


def _parse_frequency(text: str) -> Optional[int]:
    value = text.strip()
    if not value:
        return None
    hz = chirp_common.parse_freq(value)
    if hz <= 0:
        raise ValueError("Frequency must be positive")
    return hz


def _make_bool_setting(name: str, label: str, current: bool, callback, *args) -> RadioSetting:
    value = RadioSettingValueBoolean(bool(current))
    setting = RadioSetting(name, label, value)
    setting.set_apply_callback(callback, *args)
    return setting


def _make_integer_setting(name: str, label: str, current: Optional[int], minimum: int, maximum: int, callback, *args) -> RadioSetting:
    active = minimum if current is None else int(current)
    setting = RadioSetting(name, label, RadioSettingValueInteger(minimum, maximum, active))
    setting.set_apply_callback(callback, *args)
    return setting


def _build_vfo_group(vfos: Optional[List[VFOSettings]]) -> RadioSettingGroup:
    group = RadioSettingGroup('vfo', 'VFO Profiles')
    if not vfos:
        return group
    for idx, vfo in enumerate(vfos):
        label = _VFO_LABELS[idx] if idx < len(_VFO_LABELS) else str(idx)
        subgroup = RadioSettingGroup(f'vfo.{idx}', f'VFO {label}')

        freq_value = RadioSettingValueString(0, 12, _format_frequency(vfo.rx_hz) or '', autopad=False)
        freq_setting = RadioSetting(f'vfo.{idx}.freq', 'RX Frequency', freq_value)
        freq_setting.set_apply_callback(_apply_vfo_frequency, vfo)
        subgroup.append(freq_setting)

        offset_labels = [label for label, _ in _VFO_OFFSET_CHOICES]
        offset_values = [value for _, value in _VFO_OFFSET_CHOICES]
        offset_index = offset_values.index(vfo.offset_direction) if vfo.offset_direction in offset_values else 0
        offset_setting = RadioSetting(
            f'vfo.{idx}.offset_direction',
            'Offset Direction',
            RadioSettingValueList(offset_labels, current_index=offset_index),
        )
        offset_setting.set_apply_callback(_apply_vfo_offset_direction, vfo)
        subgroup.append(offset_setting)

        offset_value = RadioSettingValueString(0, 12, _format_frequency(vfo.offset_hz) or '', autopad=False)
        offset_setting_value = RadioSetting(f'vfo.{idx}.offset', 'Offset Frequency', offset_value)
        offset_setting_value.set_apply_callback(_apply_vfo_offset, vfo)
        subgroup.append(offset_setting_value)

        power_labels = [label for label, _ in _VFO_POWER_CHOICES]
        power_values = [value for _, value in _VFO_POWER_CHOICES]
        power_index = power_values.index(vfo.tx_power) if vfo.tx_power in power_values else 0
        power_setting = RadioSetting(
            f'vfo.{idx}.power',
            'TX Power',
            RadioSettingValueList(power_labels, current_index=power_index),
        )
        power_setting.set_apply_callback(_apply_vfo_power, vfo)
        subgroup.append(power_setting)

        bandwidth_labels = [label for label, _ in _VFO_BANDWIDTH_CHOICES]
        bandwidth_values = [value for _, value in _VFO_BANDWIDTH_CHOICES]
        bandwidth_index = bandwidth_values.index(vfo.bandwidth) if vfo.bandwidth in bandwidth_values else 0
        bandwidth_setting = RadioSetting(
            f'vfo.{idx}.bandwidth',
            'Bandwidth',
            RadioSettingValueList(bandwidth_labels, current_index=bandwidth_index),
        )
        bandwidth_setting.set_apply_callback(_apply_vfo_bandwidth, vfo)
        subgroup.append(bandwidth_setting)

        modulation_labels = [label for label, _ in _VFO_MODULATION_CHOICES]
        modulation_values = [value for _, value in _VFO_MODULATION_CHOICES]
        modulation_index = modulation_values.index(vfo.rx_modulation) if vfo.rx_modulation in modulation_values else 0
        modulation_setting = RadioSetting(
            f'vfo.{idx}.modulation',
            'RX Modulation',
            RadioSettingValueList(modulation_labels, current_index=modulation_index),
        )
        modulation_setting.set_apply_callback(_apply_vfo_modulation, vfo)
        subgroup.append(modulation_setting)

        subgroup.append(_make_bool_setting(f'vfo.{idx}.busy_lockout', 'Busy Lockout', vfo.busy_lockout, _apply_vfo_busy_lockout, vfo))
        subgroup.append(_make_bool_setting(f'vfo.{idx}.fhss', 'Learn FHSS', vfo.learn_fhss, _apply_vfo_learn_fhss, vfo))

        scrambler_setting = RadioSetting(
            f'vfo.{idx}.scrambler',
            'Scrambler Code',
            RadioSettingValueInteger(0, 9, int(vfo.scrambler)),
        )
        scrambler_setting.set_apply_callback(_apply_vfo_scrambler, vfo)
        subgroup.append(scrambler_setting)

        encryption_labels = [label for label, _ in _VFO_ENCRYPTION_CHOICES]
        encryption_values = [value for _, value in _VFO_ENCRYPTION_CHOICES]
        encryption_index = encryption_values.index(vfo.encryption) if vfo.encryption in encryption_values else 0
        encryption_setting = RadioSetting(
            f'vfo.{idx}.encryption',
            'Encryption Mode',
            RadioSettingValueList(encryption_labels, current_index=encryption_index),
        )
        encryption_setting.set_apply_callback(_apply_vfo_encryption, vfo)
        subgroup.append(encryption_setting)

        step_labels = [label for label, _ in _VFO_STEP_CHOICES]
        step_values = [value for _, value in _VFO_STEP_CHOICES]
        step_index = step_values.index(vfo.step_freq_index) if vfo.step_freq_index in step_values else 0
        step_setting = RadioSetting(
            f'vfo.{idx}.step',
            'Step Size',
            RadioSettingValueList(step_labels, current_index=step_index),
        )
        step_setting.set_apply_callback(_apply_vfo_step, vfo)
        subgroup.append(step_setting)

        band_labels = [label for label, _ in _VFO_BAND_CHOICES]
        band_values = [value for _, value in _VFO_BAND_CHOICES]
        band_index = band_values.index(vfo.freq_band) if vfo.freq_band in band_values else 0
        freq_band_setting = RadioSetting(
            f'vfo.{idx}.freq_band',
            'Frequency Band',
            RadioSettingValueList(band_labels, current_index=band_index),
        )
        freq_band_setting.set_apply_callback(_apply_vfo_freq_band, vfo)
        subgroup.append(freq_band_setting)

        signalling_setting = RadioSetting(
            f'vfo.{idx}.signalling_group',
            'Signalling Group',
            RadioSettingValueInteger(0, 15, int(vfo.signalling_group)),
        )
        signalling_setting.set_apply_callback(_apply_vfo_signalling, vfo)
        subgroup.append(signalling_setting)

        group.append(subgroup)
    return group


def _build_modulation_group(modulation: ModulationSettings) -> RadioSettingGroup:
    group = RadioSettingGroup('modulation', 'Broadcast/Modulation')
    global_group = RadioSettingGroup('modulation.global', 'Global Settings')
    global_group.append(_make_integer_setting('modulation.fm_current_channel', 'FM Current Channel', modulation.fm_current_channel, 0, 15, _apply_modulation_int, modulation, 'fm_current_channel'))
    global_group.append(_make_integer_setting('modulation.am_current_channel', 'AM Current Channel', modulation.am_current_channel, 0, 15, _apply_modulation_int, modulation, 'am_current_channel'))
    global_group.append(_make_integer_setting('modulation.ssb_current_channel', 'SSB Current Channel', modulation.ssb_current_channel, 0, 15, _apply_modulation_int, modulation, 'ssb_current_channel'))
    global_group.append(_make_integer_setting('modulation.work_mode', 'Work Mode', modulation.work_mode, 0, 3, _apply_modulation_int, modulation, 'work_mode'))
    global_group.append(_make_integer_setting('modulation.modulation_mode', 'Modulation Mode', modulation.modulation_mode, 0, 5, _apply_modulation_int, modulation, 'modulation_mode'))
    global_group.append(_make_integer_setting('modulation.am_step', 'AM Step Index', modulation.am_step_index, 0, 7, _apply_modulation_int, modulation, 'am_step_index'))
    global_group.append(_make_integer_setting('modulation.am_rx_gain', 'AM RX Gain', modulation.am_rx_gain, 0, 255, _apply_modulation_int, modulation, 'am_rx_gain'))
    global_group.append(_make_integer_setting('modulation.ssb_step', 'SSB Step Index', modulation.ssb_step_index, 0, 7, _apply_modulation_int, modulation, 'ssb_step_index'))
    global_group.append(_make_integer_setting('modulation.ssb_rx_gain', 'SSB RX Gain', modulation.ssb_rx_gain, 0, 255, _apply_modulation_int, modulation, 'ssb_rx_gain'))
    group.append(global_group)

    if modulation.channels:
        group.append(_build_modulation_channel_group('modulation.fm_channels', 'FM Broadcast Channels', modulation, 'fm'))
        group.append(_build_modulation_channel_group('modulation.am_channels', 'AM Broadcast Channels', modulation, 'am'))
        group.append(_build_modulation_channel_group('modulation.ssb_channels', 'SSB Channels', modulation, 'ssb'))
    return group


def _build_modulation_channel_group(name_prefix: str, label: str, modulation: ModulationSettings, mode: str) -> RadioSettingGroup:
    subgroup = RadioSettingGroup(name_prefix, label)
    for idx, channel in enumerate(modulation.channels):
        channel_group = RadioSettingGroup(f'{name_prefix}.{idx}', f'Channel {idx + 1}')
        if mode == 'fm':
            freq_setting = RadioSetting(
                f'{name_prefix}.{idx}.freq',
                'Frequency',
                RadioSettingValueString(0, 12, _format_frequency(channel.fm_frequency) or '', autopad=False),
            )
            freq_setting.set_apply_callback(_apply_modulation_channel_frequency, modulation, idx, 'fm_frequency')
            channel_group.append(freq_setting)
            name_setting = RadioSetting(
                f'{name_prefix}.{idx}.name',
                'Name',
                RadioSettingValueString(0, 16, channel.fm_name or '', autopad=False),
            )
            name_setting.set_apply_callback(_apply_modulation_channel_name, modulation, idx, 'fm_name')
            channel_group.append(name_setting)
        elif mode == 'am':
            freq_setting = RadioSetting(
                f'{name_prefix}.{idx}.freq',
                'Frequency',
                RadioSettingValueString(0, 12, _format_frequency(channel.am_frequency) or '', autopad=False),
            )
            freq_setting.set_apply_callback(_apply_modulation_channel_frequency, modulation, idx, 'am_frequency')
            channel_group.append(freq_setting)
            name_setting = RadioSetting(
                f'{name_prefix}.{idx}.name',
                'Name',
                RadioSettingValueString(0, 16, channel.am_name or '', autopad=False),
            )
            name_setting.set_apply_callback(_apply_modulation_channel_name, modulation, idx, 'am_name')
            channel_group.append(name_setting)
        elif mode == 'ssb':
            freq_setting = RadioSetting(
                f'{name_prefix}.{idx}.freq',
                'Frequency',
                RadioSettingValueString(0, 12, _format_frequency(channel.ssb_frequency) or '', autopad=False),
            )
            freq_setting.set_apply_callback(_apply_modulation_channel_frequency, modulation, idx, 'ssb_frequency')
            channel_group.append(freq_setting)
            bandwidth_setting = _make_integer_setting(
                f'{name_prefix}.{idx}.bandwidth',
                'Bandwidth',
                channel.ssb_bandwidth,
                0,
                255,
                _apply_modulation_channel_int,
                modulation,
                idx,
                'ssb_bandwidth'
            )
            channel_group.append(bandwidth_setting)
            beat_setting = RadioSetting(
                f'{name_prefix}.{idx}.beat',
                'Beat Offset',
                RadioSettingValueInteger(-32768, 32767, int(channel.ssb_beat_offset or 0)),
            )
            beat_setting.set_apply_callback(_apply_modulation_channel_beat, modulation, idx)
            channel_group.append(beat_setting)
            name_setting = RadioSetting(
                f'{name_prefix}.{idx}.name',
                'Name',
                RadioSettingValueString(0, 16, channel.ssb_name or '', autopad=False),
            )
            name_setting.set_apply_callback(_apply_modulation_channel_name, modulation, idx, 'ssb_name')
            channel_group.append(name_setting)
        subgroup.append(channel_group)
    return subgroup


def _apply_modulation_int(rsetting, modulation, attr):
    setattr(modulation, attr, max(0, _value_as_int(rsetting.value)))


def _apply_modulation_channel_frequency(rsetting, modulation, index, attr):
    try:
        value = _value_as_string(rsetting.value).strip()
    except AttributeError:
        value = str(rsetting.value).strip()
    freq = _parse_frequency(value) if value else None
    setattr(modulation.channels[index], attr, freq)


def _apply_modulation_channel_name(rsetting, modulation, index, attr):
    name = _value_as_string(rsetting.value)
    setattr(modulation.channels[index], attr, name)


def _apply_modulation_channel_int(rsetting, modulation, index, attr):
    value = max(0, _value_as_int(rsetting.value))
    setattr(modulation.channels[index], attr, value)


def _apply_modulation_channel_beat(rsetting, modulation, index):
    value = _value_as_int(rsetting.value)
    if value < -32768:
        value = -32768
    elif value > 32767:
        value = 32767
    modulation.channels[index].ssb_beat_offset = value



def _apply_vfo_frequency(rsetting, vfo):
    try:
        value = _value_as_string(rsetting.value).strip()
    except AttributeError:
        value = str(rsetting.value).strip()
    if not value:
        vfo.rx_hz = None
        return
    try:
        vfo.rx_hz = _parse_frequency(value)
    except ValueError as exc:
        raise errors.RadioError(str(exc)) from exc


def _apply_vfo_offset_direction(rsetting, vfo):
    index = _value_as_index(rsetting.value, _VFO_OFFSET_CHOICES)
    vfo.offset_direction = _VFO_OFFSET_CHOICES[index][1]


def _apply_vfo_offset(rsetting, vfo):
    try:
        value = _value_as_string(rsetting.value).strip()
    except AttributeError:
        value = str(rsetting.value).strip()
    if not value:
        vfo.offset_hz = None
        return
    try:
        vfo.offset_hz = _parse_frequency(value)
    except ValueError as exc:
        raise errors.RadioError(str(exc)) from exc


def _apply_vfo_power(rsetting, vfo):
    index = _value_as_index(rsetting.value, _VFO_POWER_CHOICES)
    vfo.tx_power = _VFO_POWER_CHOICES[index][1]


def _apply_vfo_bandwidth(rsetting, vfo):
    index = _value_as_index(rsetting.value, _VFO_BANDWIDTH_CHOICES)
    vfo.bandwidth = _VFO_BANDWIDTH_CHOICES[index][1]


def _apply_vfo_modulation(rsetting, vfo):
    index = _value_as_index(rsetting.value, _VFO_MODULATION_CHOICES)
    vfo.rx_modulation = _VFO_MODULATION_CHOICES[index][1]


def _apply_vfo_busy_lockout(rsetting, vfo):
    vfo.busy_lockout = bool(_value_as_bool(rsetting.value))


def _apply_vfo_learn_fhss(rsetting, vfo):
    vfo.learn_fhss = bool(_value_as_bool(rsetting.value))


def _apply_vfo_scrambler(rsetting, vfo):
    vfo.scrambler = max(0, _value_as_int(rsetting.value)) & 0x0F


def _apply_vfo_encryption(rsetting, vfo):
    index = _value_as_index(rsetting.value, _VFO_ENCRYPTION_CHOICES)
    vfo.encryption = _VFO_ENCRYPTION_CHOICES[index][1]


def _apply_vfo_step(rsetting, vfo):
    index = _value_as_index(rsetting.value, _VFO_STEP_CHOICES)
    vfo.step_freq_index = _VFO_STEP_CHOICES[index][1]


def _apply_vfo_freq_band(rsetting, vfo):
    vfo.freq_band = max(0, _value_as_int(rsetting.value)) & 0x0F


def _apply_vfo_signalling(rsetting, vfo):
    vfo.signalling_group = max(0, _value_as_int(rsetting.value)) & 0x0F

def _build_setting_value(meta, current):
    kind = meta['type']
    if kind == 'bool':
        return RadioSettingValueBoolean(bool(current))
    if kind == 'int':
        minimum = meta.get('min', 0)
        maximum = meta.get('max', minimum)
        active = minimum if current is None else int(current)
        return RadioSettingValueInteger(minimum, maximum, active)
    if kind == 'enum':
        choices = meta['choices']
        labels = [label for label, _ in choices]
        values = [value for _, value in choices]
        active = values[0]
        if current in values:
            active = current
        index = values.index(active)
        return RadioSettingValueList(labels, current_index=index)
    if kind == 'string':
        length = meta['length']
        return RadioSettingValueString(0, length, (current or ''), autopad=False)
    raise SettingsError(f"Unsupported setting type: {kind}")

def _value_as_bool(value_obj):
    if hasattr(value_obj, 'get_value'):
        return bool(value_obj.get_value())
    return bool(value_obj)

def _value_as_int(value_obj, default=0):
    try:
        if hasattr(value_obj, 'get_value'):
            raw = value_obj.get_value()
        else:
            raw = value_obj
        return int(raw)
    except Exception:
        return default

def _value_as_index(value_obj, choices):
    try:
        return int(value_obj)
    except Exception:
        if hasattr(value_obj, 'get_value'):
            raw = value_obj.get_value()
        else:
            raw = value_obj
        labels = [label for label, _ in choices]
        try:
            return labels.index(raw)
        except ValueError:
            return 0

def _value_as_string(value_obj):
    if hasattr(value_obj, 'get_value'):
        raw = value_obj.get_value()
    else:
        raw = value_obj
    return str(raw)

def _create_function_setting(func_settings, key, meta):
    try:
        current = get_function_value(func_settings, key)
    except KeyError:
        LOG.debug('Skipping unknown function key %s', key)
        return None
    value_obj = _build_setting_value(meta, current)
    rsetting = RadioSetting(f'function.{key}', meta['label'], value_obj)
    rsetting.set_apply_callback(_apply_function_setting, func_settings, key, meta)
    return rsetting

def _create_aprs_setting(aprs_settings, key, meta):
    try:
        current = get_aprs_value(aprs_settings, key)
    except KeyError:
        LOG.debug('Skipping unknown APRS key %s', key)
        return None
    value_obj = _build_setting_value(meta, current)
    rsetting = RadioSetting(f'aprs.{key}', meta['label'], value_obj)
    rsetting.set_apply_callback(_apply_aprs_setting, aprs_settings, key, meta)
    return rsetting

def _build_function_group(func_settings):
    group = RadioSettingGroup('function', 'Function Settings')
    for key, meta in _FUNCTION_UI.items():
        setting = _create_function_setting(func_settings, key, meta)
        if setting is not None:
            group.append(setting)
    return group

def _build_aprs_group(aprs_settings):
    group = RadioSettingGroup('aprs', 'APRS Settings')
    for key, meta in _APRS_UI.items():
        setting = _create_aprs_setting(aprs_settings, key, meta)
        if setting is not None:
            group.append(setting)
    return group

def _build_dtmf_group(dtmf_settings):
    group = RadioSettingGroup('dtmf', 'DTMF Settings')
    current_id = get_dtmf_current_id(dtmf_settings)
    value_id = RadioSettingValueString(0, _DTMF_ID_MAXLEN, current_id or '', autopad=False)
    rset_id = RadioSetting('dtmf.current_id', 'Current ID', value_id)
    rset_id.set_apply_callback(_apply_dtmf_id, dtmf_settings)
    group.append(rset_id)
    mode = get_dtmf_ptt_mode(dtmf_settings)
    labels = [label for label, _ in _DTMF_MODE_CHOICES]
    values = [value for _, value in _DTMF_MODE_CHOICES]
    index = values.index(mode) if mode in values else 0
    mode_value = RadioSettingValueList(labels, current_index=index)
    rset_mode = RadioSetting('dtmf.ptt_mode', 'PTT ID Mode', mode_value)
    rset_mode.set_apply_callback(_apply_dtmf_mode, dtmf_settings)
    group.append(rset_mode)
    for idx in range(_DTMF_GROUP_LIMIT):
        try:
            code = get_dtmf_code_group(dtmf_settings, idx)
        except IndexError:
            code = ''
        code_value = RadioSettingValueString(0, _DTMF_CODE_MAXLEN, code or '', autopad=False)
        rset_code = RadioSetting(f'dtmf.code_group_{idx + 1}', f'Code Group {idx + 1}', code_value)
        rset_code.set_apply_callback(_apply_dtmf_group, dtmf_settings, idx)
        group.append(rset_code)
    return group

def _apply_function_setting(rsetting, func_settings, key, meta):
    try:
        kind = meta['type']
        if kind == 'bool':
            set_function_value(func_settings, key, _value_as_bool(rsetting.value))
        elif kind == 'int':
            set_function_value(func_settings, key, _value_as_int(rsetting.value))
        elif kind == 'enum':
            index = _value_as_index(rsetting.value, meta['choices'])
            set_function_value(func_settings, key, meta['choices'][index][1])
        else:
            raise SettingsError(f"Unsupported function setting type {kind}")
    except (SettingsError, KeyError, IndexError, ValueError) as exc:
        raise errors.RadioError(str(exc)) from exc

def _apply_aprs_setting(rsetting, aprs_settings, key, meta):
    try:
        kind = meta['type']
        if kind == 'bool':
            set_aprs_value(aprs_settings, key, _value_as_bool(rsetting.value))
        elif kind == 'int':
            set_aprs_value(aprs_settings, key, _value_as_int(rsetting.value))
        elif kind == 'enum':
            index = _value_as_index(rsetting.value, meta['choices'])
            set_aprs_value(aprs_settings, key, meta['choices'][index][1])
        elif kind == 'string':
            set_aprs_value(aprs_settings, key, _value_as_string(rsetting.value).strip())
        else:
            raise SettingsError(f"Unsupported APRS setting type {kind}")
    except (SettingsError, KeyError, IndexError, ValueError) as exc:
        raise errors.RadioError(str(exc)) from exc

def _apply_dtmf_id(rsetting, dtmf_settings):
    try:
        set_dtmf_current_id(dtmf_settings, _value_as_string(rsetting.value).strip())
    except SettingsError as exc:
        raise errors.RadioError(str(exc)) from exc

def _apply_dtmf_mode(rsetting, dtmf_settings):
    try:
        index = _value_as_index(rsetting.value, _DTMF_MODE_CHOICES)
        set_dtmf_ptt_mode(dtmf_settings, _DTMF_MODE_CHOICES[index][1])
    except SettingsError as exc:
        raise errors.RadioError(str(exc)) from exc

def _apply_dtmf_group(rsetting, dtmf_settings, index):
    try:
        set_dtmf_code_group(dtmf_settings, index, _value_as_string(rsetting.value).strip())
    except (SettingsError, IndexError) as exc:
        raise errors.RadioError(str(exc)) from exc

def _build_clone_payload(image: RadioImage) -> bytes:
    """Compose the raw buffer expected by the radio from ``image``."""
    buffer = image.to_bytes()
    payload = bytearray()
    payload.extend(buffer[:CHANNEL_SECTION_BYTES])
    cursor = CHANNEL_SECTION_BYTES
    for segment in DEFAULT_SEGMENTS[1:]:
        end = cursor + segment.length
        payload.extend(buffer[cursor:end])
        cursor = end
    if len(payload) != _SEGMENT_LENGTH:
        raise ValueError(
            f"Composed payload length {len(payload)} does not match expected {_SEGMENT_LENGTH}"
        )
    return bytes(payload)

def _pol_to_chirp(polarity: Optional[str]) -> str:
    """Map the radio's inverted-DCS marker ("I") to CHIRP's spelling ("R")."""

    pol = (polarity or "N").upper()
    return "R" if pol == "I" else pol

@dataclass
class _ToneInfo:
    mode: str = ""
    rtone: float = 0.0
    ctone: float = 0.0
    dtcs: int = 0
    polarity: str = "N"

# ---------------------------------------------------------------------------
# Driver implementation -----------------------------------------------------

# ---------------------------------------------------------------------------

class RT950ProRadio(chirp_common.CloneModeRadio):
    _memsize = 33152
    """RT-950 Pro CHIRP driver."""
    VENDOR = "Radtel"
    MODEL = "RT-950 Pro"
    BAUD_RATE = 115200

    def __init__(self, *args, **kwargs) -> None:
        self._image: Optional[RadioImage] = None
        self._memory_cache: dict[int, chirp_common.Memory] = {}
        super().__init__(*args, **kwargs)
    # ------------------------------------------------------------------
    # CHIRP hooks
    # ------------------------------------------------------------------

    def get_features(self):  # type: ignore[override]
        rf = chirp_common.RadioFeatures()
        rf.memory_bounds = (0, CHANNEL_COUNT - 1)
        rf.valid_bands = [
            (18_000_000, 64_000_000),   # Low HF confirmed RX/TX (FM only)
            (118_000_000, 137_000_000), # Airband RX (AM only, TX disabled)
            (70_000_000, 118_000_000),  # Lower VHF before airband
            (136_000_000, 174_000_000),
            (174_000_000, 400_000_000),
            (400_000_000, 480_000_000),
            (480_000_000, 580_000_000),
        ]
        rf.has_bank = False
        rf.has_bank_names = False
        rf.has_settings = True
        rf.has_name = True
        rf.has_ctone = True
        rf.has_dtcs = True
        rf.has_dtcs_polarity = True
        rf.has_mode = True
        rf.has_offset = True
        rf.has_tuning_step = False
        rf.can_delete = True
        rf.can_odd_split = True
        rf.valid_tmodes = ["", "Tone", "TSQL", "DTCS"]
        rf.valid_duplexes = ["", "+", "-", "split"]
        rf.valid_modes = ["FM", "NFM", "AM"]
        rf.valid_power_levels = _CHIRP_POWER_LEVELS
        rf.valid_skips = ["", "S"]
        rf.valid_dtcs_pols = ["NN", "NR", "RN", "RR"]
        # Use CHIRP's standard DCS code list; do not include 0/"000"
        # to avoid invalid DTCS state during CSV export.
        rf.valid_dtcs_codes = list(chirp_common.DTCS_CODES)
        rf.valid_tuning_steps = [2.5, 5.0, 6.25, 8.33, 10.0, 12.5, 25.0]
        rf.valid_characters = chirp_common.CHARSET_ASCII
        rf.valid_name_length = 12
        return rf

    def process_mmap(self):  # type: ignore[override]
        mmap_obj = getattr(self, '_mmap', None)
        if mmap_obj is None:
            self._image = None
            return
        if hasattr(mmap_obj, 'get_packed'):
            data = mmap_obj.get_packed()
        else:
            data = bytes(mmap_obj)
        if not data:
            self._image = None
            return
        try:
            self._image = RadioImage.from_bytes(data)
        except ValueError as exc:
            raise errors.RadioError(f'Failed to parse memory map: {exc}') from exc
        self._memory_cache.clear()

    def sync_in(self):  # type: ignore[override]
        if self.pipe is None:
            raise errors.RadioError("Serial pipe not initialised")
        transport = CloneSerialTransport(self.pipe, logger=LOG)
        # Hook progress into CHIRP status if available
        status = None
        try:
            status = chirp_common.Status()
            status.msg = "Cloning from radio…"
            status.max = 0
        except Exception:
            status = None
        if status is not None and hasattr(self, "status_fn"):
            blocks_per_segment = [seg.length // READ_BLOCK for seg in DEFAULT_SEGMENTS]
            seg_labels = [
                "Channels",
                "VFO settings",
                "Function settings",
                "DTMF settings",
                "Modulation params",
                "Modulation names",
                "APRS settings",
            ]

            def _progress(done: int, total: int, phase: str) -> None:
                try:
                    block_index = max(0, int(done) - 1)
                    seg_idx = 0
                    offset_blocks = block_index
                    for count in blocks_per_segment:
                        if offset_blocks < count:
                            break
                        offset_blocks -= count
                        seg_idx += 1
                    direction = "Reading" if phase == "read" else "Writing"
                    if seg_idx == 0:
                        per_block = max(1, READ_BLOCK // CHANNEL_SIZE)
                        start_ch = offset_blocks * per_block + 1
                        end_ch = min(start_ch + per_block - 1, CHANNEL_SECTION_BYTES // CHANNEL_SIZE)
                        status.msg = f"{direction} channels {start_ch:03d}-{end_ch:03d}…"
                    else:
                        status.msg = f"{direction} {seg_labels[seg_idx]}…"
                    status.cur = done
                    status.max = total
                    self.status_fn(status)
                except Exception:
                    pass
            transport.progress_cb = _progress
        try:
            raw = transport.read_clone()
        except Exception as exc:
            raise errors.RadioError(f'Clone read failed: {exc}') from exc
        LOG.info("Radio model string: %r", transport.model)
        try:
            self._image = RadioImage.from_bytes(raw)
        except ValueError as exc:
            raise errors.RadioError(f'Downloaded image could not be parsed: {exc}') from exc
        self._mmap = memmap.MemoryMapBytes(raw)
        self._metadata = {}
        self._memory_cache.clear()

    def sync_out(self):  # type: ignore[override]
        if self.pipe is None:
            raise errors.RadioError("Serial pipe not initialised")
        if self._image is None:
            raise errors.RadioError("No image loaded")
        try:
            payload = _build_clone_payload(self._image)
        except ValueError as exc:
            raise errors.RadioError(f'Image could not be encoded for upload: {exc}') from exc
        transport = CloneSerialTransport(self.pipe, logger=LOG)
        status = None
        try:
            status = chirp_common.Status()
            status.msg = "Cloning to radio…"
            status.max = 0
        except Exception:
            status = None
        if status is not None and hasattr(self, "status_fn"):
            blocks_per_segment = [seg.length // READ_BLOCK for seg in DEFAULT_SEGMENTS]
            seg_labels = [
                "Channels",
                "VFO settings",
                "Function settings",
                "DTMF settings",
                "Modulation params",
                "Modulation names",
                "APRS settings",
            ]

            def _progress(done: int, total: int, phase: str) -> None:
                try:
                    block_index = max(0, int(done) - 1)
                    seg_idx = 0
                    offset_blocks = block_index
                    for count in blocks_per_segment:
                        if offset_blocks < count:
                            break
                        offset_blocks -= count
                        seg_idx += 1
                    direction = "Reading" if phase == "read" else "Writing"
                    if seg_idx == 0:
                        per_block = max(1, READ_BLOCK // CHANNEL_SIZE)
                        start_ch = offset_blocks * per_block + 1
                        end_ch = min(start_ch + per_block - 1, CHANNEL_SECTION_BYTES // CHANNEL_SIZE)
                        status.msg = f"{direction} channels {start_ch:03d}-{end_ch:03d}…"
                    else:
                        status.msg = f"{direction} {seg_labels[seg_idx]}…"
                    status.cur = done
                    status.max = total
                    self.status_fn(status)
                except Exception:
                    pass
            transport.progress_cb = _progress
        try:
            transport.write_clone(payload)
        except Exception as exc:
            raise errors.RadioError(f'Clone write failed: {exc}') from exc
        self._mmap = memmap.MemoryMapBytes(payload)
        self._metadata = {}
    # ------------------------------------------------------------------
    # Settings integration
    # ------------------------------------------------------------------

    def get_settings(self):  # type: ignore[override]
        image = self._require_image()
        groups = []
        if getattr(image, 'vfo', None):
            groups.append(_build_vfo_group(image.vfo))
        if getattr(image, 'modulation', None) is not None:
            groups.append(_build_modulation_group(image.modulation))
        if getattr(image, 'function', None) is not None:
            groups.append(_build_function_group(image.function))
        if getattr(image, 'aprs', None) is not None:
            groups.append(_build_aprs_group(image.aprs))
        if getattr(image, 'dtmf', None) is not None:
            groups.append(_build_dtmf_group(image.dtmf))
        if not groups:
            return RadioSettings()
        return RadioSettings(*groups)

    def set_settings(self, settings):  # type: ignore[override]
        self._require_image()
        if not isinstance(settings, RadioSettings):
            raise errors.RadioError('Unexpected settings container')
        walker = getattr(settings, 'walk', None)
        if walker is None:
            raise errors.RadioError('Settings container missing walk()')
        for element in walker():
            if isinstance(element, RadioSetting) and element.has_apply_callback():
                element.run_apply_callback()
        self._memory_cache.clear()
    # ------------------------------------------------------------------
    # Memory translation helpers
    # ------------------------------------------------------------------

    def _require_image(self) -> RadioImage:
        if self._image is None:
            raise errors.RadioError("Memory image not loaded")
        return self._image

    def get_memory(self, number: int):  # type: ignore[override]
        image = self._require_image()
        if not (0 <= number < len(image.channels)):
            raise errors.RadioError(f"Memory index {number} out of range")
        if number in self._memory_cache:
            return self._memory_cache[number]
        channel = image.channels[number]
        mem = self._channel_to_memory(number, channel)
        self._memory_cache[number] = mem
        return mem

    def set_memory(self, mem):  # type: ignore[override]
        image = self._require_image()
        if not (0 <= mem.number < len(image.channels)):
            raise errors.RadioError(f"Memory index {mem.number} out of range")
        channel = image.channels[mem.number]
        self._apply_memory_to_channel(mem, channel)
        sanitized = self._channel_to_memory(mem.number, channel)
        for attr, value in sanitized.__dict__.items():
            if attr.startswith('_'):
                continue
            object.__setattr__(mem, attr, value)
        self._memory_cache[mem.number] = sanitized
    # ------------------------------------------------------------------
    # Conversion routines
    # ------------------------------------------------------------------

    def _channel_to_memory(self, number: int, channel: ChannelRecord):
        mem = chirp_common.Memory()
        mem.number = number
        if channel.rx_hz is None:
            mem.empty = True
            return mem
        mem.freq = channel.rx_hz
        mem.empty = False
        mem.name = (channel.name or "").strip()
        self._apply_offset(mem, channel)
        self._apply_tones(mem, channel)
        if channel.rx_modulation is Modulation.AM:
            mem.mode = "AM"
        else:
            mem.mode = "NFM" if channel.bandwidth is Bandwidth.NARROW else "FM"
        mem.power = _POWER_ENUM_TO_CHIRP.get(channel.power, _CHIRP_POWER_LEVELS[0])
        mem.skip = "" if channel.scan_add else "S"
        mem.extra = _build_memory_extra(channel)
        return mem

    def _apply_offset(self, mem, channel: ChannelRecord) -> None:
        if channel.tx_hz is None or channel.tx_hz == channel.rx_hz:
            mem.duplex = ""
            mem.offset = 0
            return
        diff = channel.tx_hz - channel.rx_hz
        if diff > 0 and channel.tx_hz == channel.rx_hz + diff:
            mem.duplex = "+"
            mem.offset = abs(diff)
        elif diff < 0 and channel.tx_hz == channel.rx_hz + diff:
            mem.duplex = "-"
            mem.offset = abs(diff)
        else:
            mem.duplex = "split"
            mem.offset = channel.tx_hz

    def _apply_tones(self, mem, channel: ChannelRecord) -> None:
        tx = channel.tx_tone
        rx = channel.rx_tone
        # CHIRP's CSV exporter validates DtcsCode/RxDtcsCode even when
        # tmode is not DTCS. Use a valid default (e.g. 023) instead of 000
        # to avoid exporter errors.
        default_dcs = chirp_common.DTCS_CODES[0]
        mem.dtcs = default_dcs
        mem.rx_dtcs = default_dcs
        mem.dtcs_polarity = "NN"
        if tx.is_off and rx.is_off:
            mem.tmode = ""
            return
        if tx.mode is ToneMode.CTCSS and rx.is_off:
            mem.tmode = "Tone"
            mem.rtone = tx.ctcss_hz or 0.0
            return
        if tx.mode is ToneMode.CTCSS and rx.mode is ToneMode.CTCSS:
            mem.tmode = "TSQL"
            tone = tx.ctcss_hz or rx.ctcss_hz or 0.0
            mem.rtone = tone
            mem.ctone = tone
            return
        if tx.mode is ToneMode.DCS and tx.dcs_code is not None:
            tx_pol = _pol_to_chirp(tx.dcs_polarity)
            if rx.mode is ToneMode.DCS and rx.dcs_code is not None:
                mem.tmode = "DTCS"
                mem.dtcs = tx.dcs_code
                mem.rx_dtcs = rx.dcs_code
                rx_pol = _pol_to_chirp(rx.dcs_polarity)
            else:
                mem.tmode = "DTCS"
                mem.dtcs = tx.dcs_code
                mem.rx_dtcs = tx.dcs_code
                rx_pol = "N"
            mem.dtcs_polarity = tx_pol + rx_pol
            return
        if rx.mode is ToneMode.DCS and rx.dcs_code is not None:
            mem.tmode = "DTCS"
            mem.dtcs = rx.dcs_code
            mem.rx_dtcs = rx.dcs_code
            mem.dtcs_polarity = "N" + _pol_to_chirp(rx.dcs_polarity)
            return
        mem.tmode = ""

    def _apply_memory_to_channel(self, mem, channel: ChannelRecord) -> None:
        if getattr(mem, "empty", False):
            channel.rx_hz = None
            channel.tx_hz = None
            channel.name = ""
            return
        channel.rx_hz = mem.freq
        if mem.duplex == "+":
            channel.tx_hz = mem.freq + mem.offset
        elif mem.duplex == "-":
            channel.tx_hz = mem.freq - mem.offset
        elif mem.duplex == "split":
            channel.tx_hz = mem.offset
        else:
            channel.tx_hz = mem.freq
        channel.name = (mem.name or "").strip()
        self._update_tones_from_memory(mem, channel)

        extras = _extract_memory_extra(mem)
        scrambler_raw = extras.get('scrambler', getattr(mem, '_rt950_scrambler', None))
        encryption_raw = extras.get('encryption', getattr(mem, '_rt950_encryption', None))
        try:
            channel.scrambler = int(scrambler_raw)
        except (TypeError, ValueError):
            channel.scrambler = 0
        if channel.scrambler < 0 or channel.scrambler > 8:
            channel.scrambler = 0
        try:
            channel.encryption = int(encryption_raw)
        except (TypeError, ValueError):
            channel.encryption = 0
        if channel.encryption not in (0, 1, 2):
            channel.encryption = 0
        channel.learn_fhss = False
        channel.fhss_code = None

        mode = (mem.mode or "").upper()
        if mode not in {"AM", "FM", "NFM"}:
            mode = "AM" if channel.rx_modulation is Modulation.AM else "FM"

        # Band-specific constraints
        freq = channel.rx_hz or 0
        in_airband = 118_000_000 <= freq <= 137_000_000
        in_low_hf = 18_000_000 <= freq <= 64_000_000

        if in_airband:
            # Airband: AM only, TX disabled
            channel.rx_modulation = Modulation.AM
            channel.bandwidth = Bandwidth.WIDE
            channel.tx_enabled = False
        elif in_low_hf:
            # Low HF: FM only
            channel.rx_modulation = Modulation.FM
            channel.bandwidth = Bandwidth.NARROW if mode == "NFM" else Bandwidth.WIDE
            channel.tx_enabled = True  # undo the airband TX-disable on retune
        else:
            # Default behavior for VHF/UHF
            channel.tx_enabled = True  # undo the airband TX-disable on retune
            if mode == "AM":
                channel.rx_modulation = Modulation.AM
                channel.bandwidth = Bandwidth.WIDE
            else:
                channel.rx_modulation = Modulation.FM
                channel.bandwidth = Bandwidth.NARROW if mode == "NFM" else Bandwidth.WIDE
        if isinstance(mem.power, chirp_common.PowerLevel):
            key = str(mem.power).upper()
        elif isinstance(mem.power, str) and mem.power:
            key = mem.power.upper()
        else:
            key = None
        if key and key in _POWER_LABEL_TO_ENUM:
            channel.power = _POWER_LABEL_TO_ENUM[key]
        channel.scan_add = mem.skip != "S"

    def _update_tones_from_memory(self, mem, channel: ChannelRecord) -> None:
        # Normalise any invalid DTCS entries (e.g., code 000 from CSV/UI)
        if getattr(mem, "tmode", "") == "DTCS":
            tx_code = getattr(mem, "dtcs", 0) or 0
            rx_code = getattr(mem, "rx_dtcs", tx_code) or tx_code
            if tx_code == 0 or rx_code == 0:
                mem.tmode = ""
        if mem.tmode == "":
            channel.tx_tone = channel.tx_tone.__class__.off()
            channel.rx_tone = channel.rx_tone.__class__.off()
        elif mem.tmode == "Tone":
            channel.tx_tone = channel.tx_tone.__class__.ctcss(mem.rtone)
            channel.rx_tone = channel.rx_tone.__class__.off()
        elif mem.tmode == "TSQL":
            tone = mem.ctone or mem.rtone
            channel.tx_tone = channel.tx_tone.__class__.ctcss(mem.rtone or tone)
            channel.rx_tone = channel.rx_tone.__class__.ctcss(mem.ctone or tone)
        elif mem.tmode == "DTCS":
            polarity = getattr(mem, "dtcs_polarity", "NN") or "NN"
            tx_pol = polarity[0] if len(polarity) >= 1 else "N"
            rx_pol = polarity[1] if len(polarity) >= 2 else tx_pol
            tx_code = getattr(mem, "dtcs", 0) or 0
            rx_code = getattr(mem, "rx_dtcs", tx_code) or tx_code
            if tx_code:
                channel.tx_tone = channel.tx_tone.__class__.dcs(tx_code, tx_pol)
            else:
                channel.tx_tone = channel.tx_tone.__class__.off()
            if rx_code:
                channel.rx_tone = channel.rx_tone.__class__.dcs(rx_code, rx_pol)
            else:
                channel.rx_tone = channel.rx_tone.__class__.off()

__all__ = ["RT950ProRadio"]
