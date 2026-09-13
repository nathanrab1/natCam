"""Testes da API HTTP usando o servidor em uma thread."""

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from gcodegen import web

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def base_url(tmp_path_factory):
    web.OUTPUT_ROOT = tmp_path_factory.mktemp("out")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def call(url, data=None, headers=None, method=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(req) as r:
        body = r.read()
        return r.status, r.headers, body


def test_index_and_params(base_url):
    status, headers, body = call(base_url + "/")
    assert status == 200 and b"Gerador de G-code" in body
    _, _, body = call(base_url + "/api/params")
    groups = json.loads(body)["groups"]
    assert [g["name"] for g in groups] == ["Geral", "Isolação", "Furação", "Recorte"]


def test_load_preview_generate_download(base_url):
    _, _, body = call(base_url + "/api/load", (FIX / "teste.zip").read_bytes(), {"X-Filename": "teste.zip"})
    d = json.loads(body)
    token = d["token"]
    assert d["layers"] == ["B_Cu", "F_Cu"] and d["has_outline"]

    req = json.dumps({"token": token, "config": {"layer": "F_Cu", "mirror": "auto"}}).encode()
    _, _, body = call(base_url + "/api/preview", req, {"Content-Type": "application/json"})
    board = json.loads(body)["board"]
    assert board["mirrored"] is False and len(board["holes"]) == 3

    req = json.dumps({"token": token, "config": {"drill_single_tool": "0.6", "mill_holes": True}}).encode()
    _, _, body = call(base_url + "/api/generate", req, {"Content-Type": "application/json"})
    d = json.loads(body)
    names = [f["name"] for f in d["files"]]
    assert names == ["teste-1-isolacao.nc", "teste-2-furos-0.6mm.nc", "teste-2-furos-fresados.nc",
                     "teste-3-recorte.nc", "teste-preview.svg"]
    assert d["board"]["isolation"] and d["board"]["cutout"] and len(d["board"]["milled"]) == 2

    status, headers, body = call(f"{base_url}/api/files/{token}/teste-1-isolacao.nc")
    assert body.startswith(b"(Isolacao") and "attachment" in headers["Content-Disposition"]
    status, headers, body = call(f"{base_url}/api/files/{token}/all.zip")
    assert headers["Content-Type"] == "application/zip" and body[:2] == b"PK"


def test_invalid_param_is_reported(base_url):
    _, _, body = call(base_url + "/api/load", (FIX / "teste.zip").read_bytes(), {"X-Filename": "teste.zip"})
    token = json.loads(body)["token"]
    req = json.dumps({"token": token, "config": {"tool_dia": "abc"}}).encode()
    with pytest.raises(urllib.error.HTTPError) as e:
        call(base_url + "/api/generate", req, {"Content-Type": "application/json"})
    assert e.value.code == 500
    assert "Largura de corte" in json.loads(e.value.read())["error"]


def test_custom_output_dir(base_url, tmp_path):
    _, _, body = call(base_url + "/api/load", (FIX / "teste.zip").read_bytes(), {"X-Filename": "teste.zip"})
    token = json.loads(body)["token"]
    dest = tmp_path / "minha pasta" / "gcode"
    req = json.dumps({"token": token, "config": {}, "out_dir": str(dest)}).encode()
    _, _, body = call(base_url + "/api/generate", req, {"Content-Type": "application/json"})
    d = json.loads(body)
    assert d["out"] == str(dest)
    assert (dest / "teste-1-isolacao.nc").exists()
    assert d["log"][0] == f"Saída: {dest}"


def test_relative_output_dir_rejected(base_url):
    _, _, body = call(base_url + "/api/load", (FIX / "teste.zip").read_bytes(), {"X-Filename": "teste.zip"})
    token = json.loads(body)["token"]
    req = json.dumps({"token": token, "config": {}, "out_dir": "pasta/relativa"}).encode()
    with pytest.raises(urllib.error.HTTPError) as e:
        call(base_url + "/api/generate", req, {"Content-Type": "application/json"})
    assert "caminho absoluto" in json.loads(e.value.read())["error"]


def test_generate_single_operation_accumulates(base_url):
    _, _, body = call(base_url + "/api/load", (FIX / "teste.zip").read_bytes(), {"X-Filename": "teste.zip"})
    token = json.loads(body)["token"]
    hdr = {"Content-Type": "application/json"}
    _, _, body = call(base_url + "/api/generate", json.dumps({"token": token, "config": {}, "ops": ["drill"]}).encode(), hdr)
    d = json.loads(body)
    assert [f["name"] for f in d["files"]] == ["teste-2-furos-0.4mm.nc", "teste-2-furos-0.8mm.nc"]
    assert d["board"]["isolation"] == [] and d["board"]["cutout"] == []
    _, _, body = call(base_url + "/api/generate", json.dumps({"token": token, "config": {}, "ops": ["cutout"]}).encode(), hdr)
    d = json.loads(body)
    assert [f["name"] for f in d["files"]] == ["teste-2-furos-0.4mm.nc", "teste-2-furos-0.8mm.nc", "teste-3-recorte.nc"]
    assert d["board"]["cutout"] and d["board"]["isolation"] == []
