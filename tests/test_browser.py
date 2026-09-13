"""A ponte usada no navegador (Pyodide) roda igual no Python nativo."""

import json
import zipfile
import io
from pathlib import Path

import pytest

from gcodegen import browser

FIX = Path(__file__).parent / "fixtures"


def test_browser_bridge_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(browser, "WORK", tmp_path)
    assert [g["name"] for g in json.loads(browser.params())["groups"]] == ["Geral", "Isolação", "Furação", "Recorte"]
    d = json.loads(browser.load((FIX / "teste.zip").read_bytes(), "teste.zip"))
    assert d["layers"] == ["B_Cu", "F_Cu"]
    d = json.loads(browser.preview(json.dumps({"layer": "F_Cu"})))
    assert d["board"]["mirrored"] is False
    d = json.loads(browser.generate(json.dumps({}), json.dumps(["drill"])))
    assert [f["name"] for f in d["files"]] == ["teste-2-furos-0.4mm.nc", "teste-2-furos-0.8mm.nc"]
    assert d["files"][0]["text"].startswith("(Furacao")
    d = json.loads(browser.generate(json.dumps({}), json.dumps(["cutout"])))
    assert [f["name"] for f in d["files"]] == ["teste-2-furos-0.4mm.nc", "teste-2-furos-0.8mm.nc", "teste-3-recorte.nc"]
    names = zipfile.ZipFile(io.BytesIO(browser.all_zip())).namelist()
    assert names == ["teste-2-furos-0.4mm.nc", "teste-2-furos-0.8mm.nc", "teste-3-recorte.nc"]


def test_browser_bridge_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(browser, "WORK", tmp_path)
    browser._state["loaded"] = None
    with pytest.raises(ValueError, match="abra um ZIP"):
        browser.preview("{}")
    browser.load((FIX / "teste.zip").read_bytes(), "teste.zip")
    with pytest.raises(ValueError, match="Largura de corte"):
        browser.generate(json.dumps({"tool_dia": "x"}), "null")
