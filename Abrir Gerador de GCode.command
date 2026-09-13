#!/bin/bash
# Dois cliques neste arquivo: sobe o servidor local e abre a interface no Chrome.
# Feche esta janela do Terminal (ou Ctrl+C) para encerrar.
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "Primeira execução: preparando o ambiente Python (precisa de internet)…"
  python3 -m venv .venv || { echo "Python 3 não encontrado. Rode: xcode-select --install"; read -p "Enter para fechar"; exit 1; }
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -e . || { echo "Falha ao instalar dependências."; read -p "Enter para fechar"; exit 1; }
fi

exec .venv/bin/python -m gcodegen.web "$@"
