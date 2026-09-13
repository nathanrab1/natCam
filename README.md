# gcodegen — Gerber + Excellon (KiCad) → G-code GRBL

Gera os programas de usinagem para fresar uma placa de circuito impresso de
face simples em uma CNC com GRBL (3018 e similares), a partir do ZIP que o
KiCad exporta:

| Arquivo gerado          | Operação                                                   |
|-------------------------|------------------------------------------------------------|
| `<nome>-1-isolacao.nc`  | Isolação das trilhas com fresa V (contorno do cobre)       |
| `<nome>-2-furos-*.nc`   | Furação, um arquivo por diâmetro de broca                  |
| `<nome>-3-recorte.nc`   | Recorte do contorno (Edge.Cuts) em passes, com tabs        |
| `<nome>-preview.svg`    | Pré-visualização do cobre, furos e caminhos gerados        |

Só usa `G0`/`G1`/`M3`/`M5`/`G4` — sem ciclos fixos nem troca automática de
ferramenta, que o GRBL não tem.

## Usar online (GitHub Pages)

A página funciona sem servidor: o Python roda dentro do navegador (Pyodide).
Publicada em **https://nathanrab1.github.io/natCam/** — abra, arraste o ZIP e
gere; os arquivos saem como download e o ZIP nunca sai do seu computador.
O primeiro acesso baixa ~15 MB de runtime (depois fica em cache).

Para publicar/atualizar: no GitHub, *Settings → Pages → Source: Deploy from a
branch*, branch `main`, pasta `/ (root)`. Cada push em `main` atualiza o site.

## Usar localmente (servidor Python)

Dê dois cliques em **`Abrir Gerador de GCode.command`** dentro desta pasta.
Uma janela do Terminal abre (deixe-a aberta — é o servidor) e o Google Chrome
abre em `http://127.0.0.1:8765`. Na primeira vez o script cria a pasta `.venv`
e instala as dependências sozinho (precisa de internet nessa execução).

> Se o macOS disser que o arquivo "não pode ser aberto porque é de um
> desenvolvedor não identificado": clique com o botão direito → **Abrir**
> → **Abrir**. Só na primeira vez.

Na página: arraste o ZIP do KiCad para a janela (ou **Abrir ZIP…**) → ajuste
os parâmetros na aba e clique no botão **Gerar** no fim dela. Cada aba gera só
a sua operação (isolação, furação ou recorte); a aba *Geral* tem **Gerar tudo**.
Os arquivos gerados vão se acumulando na lista à esquerda. A placa aparece à
direita (arraste para mover, roda do mouse para zoom; cobre em laranja,
isolação em vermelho, furos em azul, recorte em verde). Os `.nc` ficam
listados à esquerda para download e são gravados na **Pasta de destino**
(botão **Escolher…** abre o seletor do macOS, ou digite o caminho; vazio =
`output/<nome>/` dentro desta pasta). A última pasta escolhida fica
lembrada. **Mostrar no Finder** abre a pasta.

Pelo Terminal, o equivalente é `.venv/bin/gcodegen-web` (ou
`.venv/bin/gcodegen-web placa.zip` para já abrir uma placa). Para encerrar,
Ctrl+C na janela do Terminal.

A mesma `index.html` serve os dois modos: se encontra o servidor local usa
ele (com pasta de destino e "Mostrar no Finder"); se não, carrega o Python
no navegador.

## Instalação (linha de comando)

```sh
python3 -m venv .venv
.venv/bin/pip install -e .
```

## Exportando no KiCad

1. **Arquivo → Plotar** (ou `Plot`): formato *Gerber*, marque as camadas
   `B.Cu` (e `F.Cu` se quiser), `Edge.Cuts`. Deixe *Usar extensões Protel* ou
   não — os dois casos são reconhecidos.
2. No mesmo diálogo, **Gerar arquivos de furação**: formato *Excellon*,
   unidades mm. Furos PTH e NPTH separados ou juntos, tanto faz.
3. Compacte a pasta num ZIP.

Pela linha de comando é equivalente a:

```sh
kicad-cli pcb export gerbers --check-zones --layers B.Cu,Edge.Cuts -o saida/ placa.kicad_pcb
kicad-cli pcb export drill --format excellon --excellon-units mm -o saida/ placa.kicad_pcb
zip placa.zip saida/*
```

## Uso pela linha de comando

```sh
.venv/bin/gcodegen placa.zip
```

Cria a pasta `placa_gcode/` ao lado do ZIP. O padrão é fresar `B_Cu`
**espelhado** (a placa é virada com o cobre para cima), com a origem X0 Y0 no
canto inferior-esquerdo da placa e Z0 na superfície do cobre.

Parâmetros mais usados (todos em mm, mm/min):

```
--tool-dia 0.2        largura de corte da fresa V na profundidade (não o diâmetro do haste)
--iso-passes 2        passes concêntricos de isolação (mais passes = mais folga p/ soldar)
--iso-depth -0.05     profundidade da isolação
--drill-single-tool 0.8 --mill-holes
                      uma broca só; furos maiores são fresados em círculo com ela
--drill-step 0.3      quanto desce por vez em cada furo, reto ou em círculo (0 = de uma vez)
--cut-tool-dia 1.0 --cut-depth -1.7 --cut-step 0.5
--tabs 4 --tab-width 3 --tab-height 0.5   (--tabs 0 desliga)
--layer F_Cu          fresa a face superior (não espelha)
--origin gerber       mantém as coordenadas originais do gerber
--skip-isolation / --skip-drill / --skip-cutout
```

`gcodegen --help` lista todos.

Abra o `preview.svg` antes de fresar: cobre em laranja, isolação em vermelho,
furos em azul, recorte em verde tracejado; os eixos vermelho/verde marcam a
origem.

## Ordem na máquina

1. Fixe a placa com o cobre para cima, zere X/Y no canto inferior-esquerdo e Z
   tocando o cobre.
2. `1-isolacao.nc` com a fresa V.
3. `2-furos-*.nc` — troque a broca entre os arquivos e re-zere o Z.
4. `3-recorte.nc` com a fresa de topo, por último (a placa fica presa pelas tabs).

## O que é suportado

- Gerber RS-274X / X2: apertures `C R O P`, macros `%AM` com variáveis e
  expressões (o `RoundRect` e afins do KiCad), traços `G01`, arcos `G02/G03`
  (`G74`/`G75`), regiões `G36/G37`, polaridade `LPD/LPC`, mm e polegadas.
- Excellon: formato decimal e com zeros suprimidos (`METRIC,TZ` / `INCH,LZ`),
  rasgos `G85` (viram fileira de furos).
- Edge.Cuts com recortes internos (viram um caminho de recorte interno, sem tabs).

Não suportado: `%SR` (step-and-repeat), `%AB` (block apertures) — o KiCad não
emite nenhum dos dois para cobre.

## Testes

```sh
.venv/bin/pytest
```

`tests/fixtures/` contém dois ZIPs exportados pelo KiCad 10 (template Arduino
Nano e uma placa de teste com trilha, arco, via, zona e pad roundrect).
