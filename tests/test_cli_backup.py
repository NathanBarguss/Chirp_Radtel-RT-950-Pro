# SPDX-License-Identifier: MIT

from contextlib import nullcontext
import logging
from pathlib import Path
from unittest.mock import Mock

from rt950pro import cli


def _clone_read(tmp_path, monkeypatch):
    output = tmp_path / "radio.bin"
    output.write_bytes(b"previous backup")
    transport = Mock()
    transport.read_clone.return_value = b"new clone"
    monkeypatch.setattr(cli.CloneSerialTransport, "open", lambda *_a, **_k: nullcontext(transport))
    return output


def test_clone_read_replaces_backup_after_saving(tmp_path, monkeypatch):
    output = _clone_read(tmp_path, monkeypatch)

    result = cli._cmd_image_clone_read(
        port="fake", baud=115200, timeout=1.0, write_timeout=1.0, output=output, logger=logging.getLogger(__name__)
    )

    assert result == 0
    assert output.read_bytes() == b"new clone"
    assert list(tmp_path.iterdir()) == [output]


def test_clone_read_keeps_previous_backup_if_replacement_fails(tmp_path, monkeypatch, caplog):
    output = _clone_read(tmp_path, monkeypatch)

    def fail_replace(source, destination):
        assert source.read_bytes() == b"new clone"
        assert destination == output
        raise OSError("simulated write error")

    monkeypatch.setattr(Path, "replace", fail_replace)
    result = cli._cmd_image_clone_read(
        port="fake", baud=115200, timeout=1.0, write_timeout=1.0, output=output, logger=logging.getLogger(__name__)
    )

    assert result != 0
    assert output.read_bytes() == b"previous backup"
    assert list(tmp_path.iterdir()) == [output]
    assert "simulated write error" in caplog.text
