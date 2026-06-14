"""Tests for the bash command-detection heuristics (`patterns.py`).

This module is the safety gate behind the ``bash`` tool — it decides whether a
command needs confirmation and whether it is escalated as high-risk. Until now
it had **no tests**. These cover the high-risk detector (incl. the
``rm -r -f`` separate-flag false-negative and the destructive-command
escalation) and the cross-platform write detector.
"""

from __future__ import annotations

import pytest

from dive_mcp_host.internal_tools.tools.patterns import (
    _detect_high_risk_command,
    _detect_write_command,
)

# --- High-risk: recursive+force rm (the bug — every spelling must be caught) ---

_RM_RFORCE_HIGH_RISK = [
    "rm -rf /tmp/x",
    "rm -fr /tmp/x",
    "rm -Rf /tmp/x",
    "rm -r -f /tmp/x",            # separate flags (was a false negative)
    "rm -f -r /tmp/x",            # separate flags, other order
    "rm --recursive --force /tmp/x",  # long flags (was a false negative)
    "rm -rvf /tmp/x",             # clustered with verbose
    "rm -R -f /tmp/x",            # capital recursive + separate force
]


@pytest.mark.parametrize("cmd", _RM_RFORCE_HIGH_RISK)
def test_high_risk_recursive_force_rm_in_every_spelling(cmd: str) -> None:
    """``rm`` with recursive+force is equally dangerous as ``-rf`` regardless
    of whether the flags are clustered (``-rf``), separate (``-r -f``), capital
    (``-Rf``), or long (``--recursive --force``). All must be high-risk."""
    is_high_risk, reasons = _detect_high_risk_command(cmd)
    assert is_high_risk, f"expected high-risk for: {cmd!r} (reasons={reasons})"
    assert any("rm" in r.lower() or "recursive" in r.lower() for r in reasons)


_RM_NOT_HIGH_RISK = [
    "rm file.txt",          # plain delete — write, not high-risk
    "rm -r /tmp/x",         # recursive but NOT force — not high-risk
    "rm -f /tmp/x",         # force but NOT recursive — not high-risk
    "echo rm is just a word here",   # 'rm' inside prose, not the command
    "ls -la",
    "echo hello world",
]


@pytest.mark.parametrize("cmd", _RM_NOT_HIGH_RISK)
def test_high_risk_plain_or_non_recursive_rm_is_not_high_risk(cmd: str) -> None:
    is_high_risk, _ = _detect_high_risk_command(cmd)
    assert not is_high_risk, f"should NOT be high-risk: {cmd!r}"


# --- High-risk: destructive commands (the escalation upgrade) ---

_DESTRUCTIVE_HIGH_RISK = [
    ("dd if=/dev/zero of=/dev/sda", "Raw disk write"),
    ("dd of=/dev/sdb bs=1M", "Raw disk write"),
    ("mkfs.ext4 /dev/sda1", "Disk format"),
    ("mkfs /dev/sdc", "Disk format"),
    ("fdisk /dev/nvme0n1", "Disk format"),
    ("parted /dev/sda mklabel gpt", "Disk format"),
    ("shutdown -h now", "System power"),
    ("reboot", "System power"),
    ("halt", "System power"),
    ("poweroff", "System power"),
    ("init 0", "System power"),
    ("init 6", "System power"),
    (":(){ :|:& };:", "Fork bomb"),
    (": () { : | : & } ; :", "Fork bomb"),
]


@pytest.mark.parametrize("cmd,expected_reason", _DESTRUCTIVE_HIGH_RISK)
def test_high_risk_destructive_commands(cmd: str, expected_reason: str) -> None:
    """Destructive commands the bash tool escalates: raw disk overwrite (dd to
    a block device), filesystem format/repartition, system power control, and
    fork bombs. None of these were high-risk before."""
    is_high_risk, reasons = _detect_high_risk_command(cmd)
    assert is_high_risk, f"expected high-risk for: {cmd!r}"
    joined = " ".join(reasons).lower()
    assert expected_reason.lower().split()[0] in joined, (
        f"expected a {expected_reason!r} reason for {cmd!r}, got {reasons}"
    )


def test_high_risk_dd_to_regular_file_is_not_device_overwrite() -> None:
    """``dd of=image.img`` writes a regular file — NOT a raw disk overwrite, so
    it must not be escalated for the device reason (it's still a write op)."""
    is_high_risk, reasons = _detect_high_risk_command("dd if=/dev/zero of=disk.img")
    device_reasons = [r for r in reasons if "device" in r.lower()]
    assert device_reasons == []
    # And no spurious high-risk from dd alone:
    _ = is_high_risk  # high-risk may still be False here — that's correct


# --- High-risk: preserved existing behaviour (sudo / system paths / chmod) ---

_PRESERVED_HIGH_RISK = [
    "sudo apt install foo",          # sudo + package manager
    "sudo rm -rf /etc/thing",        # sudo + system path
    "chmod 777 /tmp/x",              # permission change
    "chown root /var/log/x",         # ownership change
    "echo modify /etc/passwd",       # system path mention
]


@pytest.mark.parametrize("cmd", _PRESERVED_HIGH_RISK)
def test_high_risk_preserved_existing_detections(cmd: str) -> None:
    is_high_risk, _ = _detect_high_risk_command(cmd)
    assert is_high_risk, f"existing detection regressed for: {cmd!r}"


# --- Write detection: cross-platform (common patterns + redirection) ---

_WRITE_TRUE_CROSS_PLATFORM = [
    "git push origin main",
    "git commit -m x",
    "npm install left-pad",
    "echo hi > out.txt",       # output redirection
    "python3 -c 'print(1)'",   # code execution
    "echo hi >> out.txt",      # append redirection
]


@pytest.mark.parametrize("cmd", _WRITE_TRUE_CROSS_PLATFORM)
def test_write_detection_cross_platform(cmd: str) -> None:
    is_write, reasons = _detect_write_command(cmd)
    assert is_write, f"expected write for: {cmd!r} (reasons={reasons})"


_WRITE_FALSE = [
    "ls -la",
    "cat /etc/hosts",
    "echo hello world",
    "node --version",
]


@pytest.mark.parametrize("cmd", _WRITE_FALSE)
def test_write_detection_readonly(cmd: str) -> None:
    is_write, _ = _detect_write_command(cmd)
    assert not is_write, f"read-only command flagged as write: {cmd!r}"


def test_write_detection_reasons_are_human_readable() -> None:
    _, reasons = _detect_write_command("git push origin main")
    assert reasons, "git push must yield a reason"
    assert all(isinstance(r, str) and r.strip() for r in reasons)
