"""Tests for Patch 7 — clipboard util (SDD §8.6 fallback chain)."""

import sys

import pytest

from agentbench.tui import clipboard


class TestClipboardFallbackChain:
    def test_pyperclip_first(self, monkeypatch):
        calls = []

        class FakePyperclip:
            @staticmethod
            def copy(text):
                calls.append(text)

        monkeypatch.setitem(sys.modules, "pyperclip", FakePyperclip)
        method, extra = clipboard.copy_text("hello")
        assert method == "pyperclip"
        assert extra is None
        assert calls == ["hello"]

    def test_platform_helper_when_no_pyperclip(self, monkeypatch):
        monkeypatch.delitem(sys.modules, "pyperclip", raising=False)
        captured = {}

        def fake_run(argv, input=None, check=True, timeout=5):  # noqa: A002
            captured["argv"] = argv
            captured["input"] = input

        monkeypatch.setattr(clipboard.subprocess, "run", fake_run)
        monkeypatch.setattr(
            clipboard,
            "_backends",
            lambda: [("xclip", ["xclip", "-selection", "clipboard"], True)],
        )
        method, extra = clipboard.copy_text("data")
        assert method == "xclip"
        assert extra is None
        assert captured["argv"] == ["xclip", "-selection", "clipboard"]
        assert captured["input"] == b"data"

    def test_tempfile_last_resort(self, monkeypatch, tmp_path):
        monkeypatch.delitem(sys.modules, "pyperclip", raising=False)
        monkeypatch.setattr(clipboard, "_backends", lambda: [])
        monkeypatch.setattr(clipboard, "TEMP_CLIPBOARD", tmp_path / "cb.txt")
        method, extra = clipboard.copy_text("fallback data")
        assert method == "tempfile"
        assert extra == str(tmp_path / "cb.txt")
        assert (tmp_path / "cb.txt").read_text(encoding="utf-8") == "fallback data"

    def test_backends_exclude_missing_binaries(self, monkeypatch):
        monkeypatch.setattr(clipboard.sys, "platform", "linux")
        monkeypatch.setattr(clipboard.shutil, "which", lambda name: None)
        monkeypatch.setattr(
            clipboard.Path, "exists", lambda self: False  # noqa: ARG005
        )
        assert clipboard._backends() == []

    def test_read_clipboard_from_tempfile(self, monkeypatch, tmp_path):
        monkeypatch.delitem(sys.modules, "pyperclip", raising=False)
        monkeypatch.setattr(clipboard, "TEMP_CLIPBOARD", tmp_path / "cb.txt")
        (tmp_path / "cb.txt").write_text("stored", encoding="utf-8")
        assert clipboard.read_clipboard() == "stored"
