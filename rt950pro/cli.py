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

"""Command line harness entry point for development."""
from __future__ import annotations

import argparse
from contextlib import suppress
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Optional, Sequence

from .channel import ChannelRecord
from .dat_loader import CPSLoaderError, load_cps_radio
from .image import CHANNEL_SECTION_BYTES, RadioImage
from .logging import configure_logging, log_mock_warning
from .regression import ComparisonError, ComparisonResult, Difference, compare_dat_to_csv
from .transport import (
    CloneSerialConfig,
    CloneSerialTransport,
    CloneTransportError,
    DEFAULT_SEGMENTS,
)


def build_parser() -> argparse.ArgumentParser:
    """Construct and return the top-level argument parser for the harness."""

    parser = argparse.ArgumentParser(description="RT-950 Pro development harness")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Increase logging verbosity (DEBUG on console)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Reduce logging verbosity (WARN on console)",
    )

    subparsers = parser.add_subparsers(dest="command")

    channel_parser = subparsers.add_parser("channel", help="Channel record utilities")
    channel_sub = channel_parser.add_subparsers(dest="channel_command")

    decode_parser = channel_sub.add_parser("decode", help="Decode a 32-byte channel blob")
    decode_parser.add_argument(
        "input",
        help="Path to binary file containing 32 bytes or a hex string when --hex is supplied",
    )
    decode_parser.add_argument(
        "--hex",
        action="store_true",
        help="Treat input argument as a hex string instead of a file path",
    )

    encode_parser = channel_sub.add_parser("encode", help="Encode a channel to bytes (placeholder)")
    encode_parser.add_argument(
        "input",
        help="Path to JSON description of a channel (not yet implemented)",
    )

    image_parser = subparsers.add_parser("image", help="Radio image utilities")
    image_sub = image_parser.add_subparsers(dest="image_command")

    clone_read_parser = image_sub.add_parser("clone-read", help="Read a clone dump over serial")
    clone_read_parser.add_argument("--port", required=True, help="Serial port presented by the radio")
    clone_read_parser.add_argument("--baud", type=int, default=115200, help="Clone baud rate (default: 115200)")
    clone_read_parser.add_argument("--timeout", type=float, default=1.0, help="Serial read timeout in seconds (default: 1.0)")
    clone_read_parser.add_argument("--write-timeout", type=float, default=1.0, help="Serial write timeout in seconds (default: 1.0)")
    clone_read_parser.add_argument("--output", type=Path, default=Path("rt950pro_clone.bin"), help="Output file for the decrypted clone image")

    clone_write_parser = image_sub.add_parser("clone-write", help="Write a clone image to the radio")
    clone_write_parser.add_argument("--port", required=True, help="Serial port presented by the radio")
    clone_write_parser.add_argument("--input", type=Path, required=True, help="Path to the decoded clone image")
    clone_write_parser.add_argument("--baud", type=int, default=115200, help="Clone baud rate (default: 115200)")
    clone_write_parser.add_argument("--timeout", type=float, default=1.0, help="Serial read timeout in seconds (default: 1.0)")
    clone_write_parser.add_argument("--write-timeout", type=float, default=1.0, help="Serial write timeout in seconds (default: 1.0)")

    summary_parser = image_sub.add_parser("summary", help="Summarise a raw clone image")
    summary_parser.add_argument("input", help="Path to a raw clone image dump")
    summary_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Number of populated channels to include in the output (default: 10)",
    )

    dat_parser = image_sub.add_parser("dat-summary", help="Summarise a vendor CPS .dat file")
    dat_parser.add_argument("input", help="Path to a CPS .dat file")
    dat_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Number of populated channels to include in the output (default: 10)",
    )
    dat_parser.add_argument(
        "--assembly",
        type=str,
        default=None,
        help="Optional explicit path to BT-RT950PRO_CPS.exe",
    )

    regression_parser = subparsers.add_parser("regression", help="Regression utilities")
    regression_sub = regression_parser.add_subparsers(dest="regression_command")

    compare_parser = regression_sub.add_parser(
        "dat-csv",
        help="Compare a CPS .dat file against a CSV export",
    )
    compare_parser.add_argument("dat", help="Path to the CPS .dat file")
    compare_parser.add_argument("csv", help="Path to the reference CSV")
    compare_parser.add_argument(
        "--assembly",
        type=str,
        default=None,
        help="Optional path to BT-RT950PRO_CPS.exe",
    )
    compare_parser.add_argument(
        "--limit",
        type=int,
        default=25,
        help="Maximum number of differences to report (default: 25)",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point used by ``python -m rt950pro``."""

    parser = build_parser()
    args = parser.parse_args(argv)

    logger = configure_logging(verbose=args.verbose, quiet=args.quiet)

    if args.command == "channel":
        if args.channel_command == "decode":
            return _cmd_channel_decode(args.input, treat_as_hex=args.hex, logger=logger)
        if args.channel_command == "encode":
            logger.warning("Channel encoding CLI not yet implemented")
            log_mock_warning(logger, "channel encode command")
            return 2
    elif args.command == "image":
        if args.image_command == "clone-read":
            return _cmd_image_clone_read(
                port=args.port,
                baud=args.baud,
                timeout=args.timeout,
                write_timeout=args.write_timeout,
                output=args.output,
                logger=logger,
            )
        if args.image_command == "clone-write":
            return _cmd_image_clone_write(
                input_path=args.input,
                port=args.port,
                baud=args.baud,
                timeout=args.timeout,
                write_timeout=args.write_timeout,
                logger=logger,
            )
        if args.image_command == "summary":
            return _cmd_image_summary(Path(args.input), limit=args.limit, logger=logger)
        if args.image_command == "dat-summary":
            assembly = Path(args.assembly) if args.assembly else None
            return _cmd_image_dat_summary(Path(args.input), limit=args.limit, assembly=assembly, logger=logger)
    elif args.command == "regression":
        if args.regression_command == "dat-csv":
            assembly = Path(args.assembly) if args.assembly else None
            return _cmd_regression_dat_csv(Path(args.dat), Path(args.csv), assembly=assembly, limit=args.limit, logger=logger)
    parser.print_help()
    return 1


def _cmd_image_clone_write(*, input_path: Path, port: str, baud: int, timeout: float, write_timeout: float, logger) -> int:
    """Write a clone image to the radio over serial."""

    expected_length = sum(segment.length for segment in DEFAULT_SEGMENTS)
    try:
        raw_data = input_path.read_bytes()
    except OSError as exc:
        logger.error("Unable to read image file %s: %s", input_path, exc)
        return 2

    if len(raw_data) != expected_length:
        logger.error("Input size %s bytes does not match expected clone length %s; refusing to write", len(raw_data), expected_length)
        logger.error("Use a raw clone dump produced by this tool or a CPS read")
        return 2
    payload = raw_data

    config = CloneSerialConfig(port=port, baudrate=baud, timeout=timeout, write_timeout=write_timeout)
    try:
        with CloneSerialTransport.open(config, logger=logger) as transport:
            transport.write_clone(payload)
    except CloneTransportError as exc:
        logger.error("Clone transport error: %s", exc)
        return 1
    except OSError as exc:
        logger.error("Unable to open serial port %s: %s", port, exc)
        return 1

    logger.info("Uploaded %d bytes to %s", len(payload), port)
    return 0


def _cmd_image_clone_read(*, port: str, baud: int, timeout: float, write_timeout: float, output: Path, logger) -> int:
    """Read a clone image over serial and write it to ``output``."""

    config = CloneSerialConfig(port=port, baudrate=baud, timeout=timeout, write_timeout=write_timeout)
    try:
        with CloneSerialTransport.open(config, logger=logger) as transport:
            data = transport.read_clone()
    except CloneTransportError as exc:
        logger.error("Clone transport error: %s", exc)
        return 1
    except OSError as exc:
        logger.error("Unable to open serial port %s: %s", port, exc)
        return 1

    temporary_path = None
    try:
        destination = output.resolve()
        with NamedTemporaryFile(dir=destination.parent, prefix=".rt950pro-", delete=False) as handle:
            temporary_path = Path(handle.name)
            handle.write(data)
        temporary_path.replace(destination)
    except OSError as exc:
        logger.error("Unable to save clone image %s: %s", output, exc)
        return 2
    finally:
        if temporary_path is not None:
            with suppress(OSError):
                temporary_path.unlink(missing_ok=True)

    logger.info("Wrote %d bytes to %s", len(data), output)
    return 0


def _compose_clone_payload(image: RadioImage) -> bytes:
    buffer = image.to_bytes()
    data = bytearray()
    data.extend(buffer[:CHANNEL_SECTION_BYTES])
    tail = buffer[CHANNEL_SECTION_BYTES:]
    cursor = 0
    for segment in DEFAULT_SEGMENTS[1:]:
        length = segment.length
        segment_bytes = tail[cursor : cursor + length]
        if len(segment_bytes) < length:
            segment_bytes = segment_bytes + b"\xFF" * (length - len(segment_bytes))
        data.extend(segment_bytes)
        cursor += length
    return bytes(data)


def _cmd_channel_decode(input_arg: str, *, treat_as_hex: bool, logger) -> int:
    """Decode a single 32-byte channel record and dump a JSON summary."""

    try:
        data = _read_channel_bytes(input_arg, treat_as_hex)
    except ValueError as exc:
        logger.error("%s", exc)
        return 2

    try:
        record = ChannelRecord.from_bytes(data, logger=logger)
    except ValueError as exc:
        logger.error("Failed to decode channel record: %s", exc)
        return 2

    payload = _channel_to_dict(record)
    print(json.dumps(payload, indent=2))
    return 0


def _cmd_image_summary(path: Path, *, limit: int, logger) -> int:
    """Produce a JSON summary for the first populated channels in ``path``."""

    try:
        image = RadioImage.from_file(path, logger=logger)
    except ValueError as exc:
        logger.error("%s", exc)
        return 2

    payload = _build_image_summary(image, limit)
    print(json.dumps(payload, indent=2))
    return 0


def _cmd_image_dat_summary(path: Path, *, limit: int, assembly: Optional[Path], logger) -> int:
    """Summarise channels stored in a vendor CPS `.dat` file."""

    try:
        image = load_cps_radio(path, assembly_path=assembly)
    except CPSLoaderError as exc:
        logger.error("%s", exc)
        return 2

    payload = _build_image_summary(image, limit)
    print(json.dumps(payload, indent=2))
    return 0


def _cmd_regression_dat_csv(
    dat_path: Path,
    csv_path: Path,
    *,
    assembly: Optional[Path],
    limit: int,
    logger,
) -> int:
    """Compare a CPS `.dat` file against a CSV export and report differences."""

    try:
        result = compare_dat_to_csv(dat_path, csv_path, assembly_path=assembly, max_differences=limit)
    except ComparisonError as exc:
        logger.error("%s", exc)
        return 2

    payload = {
        "total_channels": result.total_channels,
        "mismatched_channels": result.mismatched_channels,
        "differences": [
            {
                "zone": diff.zone,
                "slot": diff.slot,
                "field": diff.field,
                "expected": diff.expected,
                "actual": diff.actual,
            }
            for diff in result.differences
        ],
    }
    print(json.dumps(payload, indent=2))
    return 0


def _build_image_summary(image: RadioImage, limit: int) -> dict[str, object]:
    """Create a summary payload from a :class:RadioImage."""

    entries = []
    for index, channel in image.iter_populated_channels():
        entries.append(
            {
                "index": index,
                "rx_hz": channel.rx_hz,
                "tx_hz": channel.tx_hz,
                "name": channel.name,
                "power": channel.power.name,
                "bandwidth": channel.bandwidth.name,
            }
        )
        if limit and len(entries) >= limit:
            break

    summary: dict[str, object] = {
        "channels_reported": len(entries),
        "empty_slots": len(image.empty_slot_indexes()),
        "entries": entries,
    }

    if image.vfo:
        summary["vfo"] = [
            {
                "index": idx,
                "rx_hz": entry.rx_hz,
                "offset_hz": entry.offset_hz,
                "offset_direction": entry.offset_direction,
                "tx_power": entry.tx_power.name,
                "bandwidth": entry.bandwidth.name,
            }
            for idx, entry in enumerate(image.vfo)
        ]
    if image.function:
        summary["function"] = {k: v for k, v in image.function.values.items() if v is not None}
    if image.dtmf:
        summary["dtmf"] = {
            "current_id": image.dtmf.current_id,
            "ptt_id_mode": image.dtmf.ptt_id_mode,
            "last_time_send": image.dtmf.last_time_send,
            "last_time_stop": image.dtmf.last_time_stop,
            "code_groups": [code for code in image.dtmf.code_groups[:5] if code],
        }
    if image.modulation:
        summary["modulation"] = {
            "fm_current_channel": image.modulation.fm_current_channel,
            "am_current_channel": image.modulation.am_current_channel,
            "ssb_current_channel": image.modulation.ssb_current_channel,
        }
    if image.aprs:
        summary["aprs"] = {k: v for k, v in image.aprs.fields.items() if v not in (None, "")}
    return summary
def _read_channel_bytes(input_arg: str, treat_as_hex: bool) -> bytes:
    """Load raw channel bytes either from a file or from a hex string."""

    if treat_as_hex:
        cleaned = "".join(ch for ch in input_arg if ch not in {" ", "\t", "\n", "\r"})
        if len(cleaned) != 64:
            raise ValueError("Hex input must contain exactly 64 characters (32 bytes)")
        try:
            return bytes.fromhex(cleaned)
        except ValueError as exc:
            raise ValueError("Input is not valid hexadecimal data") from exc
    path = Path(input_arg)
    if not path.exists():
        raise ValueError(f"Channel input file not found: {path}")
    data = path.read_bytes()
    if len(data) != 32:
        raise ValueError(f"Channel input must be exactly 32 bytes; got {len(data)}")
    return data


def _channel_to_dict(record: ChannelRecord) -> dict[str, object]:
    """Convert a :class:`ChannelRecord` into a JSON-friendly payload."""

    return {
        "rx_hz": record.rx_hz,
        "tx_hz": record.tx_hz,
        "rx_tone": {
            "mode": record.rx_tone.mode.value,
            "display": record.rx_tone.to_display(),
            "ctcss_hz": record.rx_tone.ctcss_hz,
            "dcs_code": record.rx_tone.dcs_code,
            "dcs_polarity": record.rx_tone.dcs_polarity,
        },
        "tx_tone": {
            "mode": record.tx_tone.mode.value,
            "display": record.tx_tone.to_display(),
            "ctcss_hz": record.tx_tone.ctcss_hz,
            "dcs_code": record.tx_tone.dcs_code,
            "dcs_polarity": record.tx_tone.dcs_polarity,
        },
        "signalling_group": record.signalling_group,
        "ptt_id": record.ptt_id,
        "power": record.power.name,
        "scrambler": record.scrambler,
        "learn_fhss": record.learn_fhss,
        "bandwidth": record.bandwidth.name,
        "encryption": record.encryption,
        "busy_lockout": record.busy_lockout,
        "scan_add": record.scan_add,
        "tx_enabled": record.tx_enabled,
        "rx_modulation": record.rx_modulation.name,
        "fhss_code": record.fhss_code,
        "name": record.name,
    }

