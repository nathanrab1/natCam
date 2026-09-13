"""Parâmetros de usinagem. Unidades: mm, mm/min, RPM. Z negativo = abaixo da placa."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Config:
    # camada e orientação
    layer: str = "B_Cu"          # "B_Cu" ou "F_Cu"
    mirror: bool | None = None   # None = automático (espelha B_Cu)

    # isolação (fresa V ou topo fino)
    tool_dia: float = 0.2        # largura efetiva da ponta na profundidade de corte
    iso_passes: int = 1
    iso_overlap: float = 0.5     # fração do diâmetro sobreposta entre passes
    iso_depth: float = -0.05
    iso_feed: float = 150.0

    # furação
    drill_depth: float = -1.9
    drill_feed: float = 60.0     # avanço em Z ao furar
    drill_step: float = 0.3      # desce esse tanto por vez em todos os furos (0 = de uma vez)
    drill_single_tool: float | None = None   # usa uma broca só para tudo
    mill_holes: bool = False     # furos maiores que a broca são fresados em círculo

    # recorte do contorno
    cut_tool_dia: float = 1.0
    cut_depth: float = -1.7
    cut_step: float = 0.5
    cut_feed: float = 120.0
    tabs: int = 4
    tab_width: float = 3.0
    tab_height: float = 0.5      # espessura que fica na tab

    # geral
    safe_z: float = 2.0
    plunge_feed: float = 50.0
    spindle: int = 10000
    dwell: float = 1.0           # segundos aguardando o spindle acelerar
    origin: str = "board"        # "board" (canto inferior-esquerdo em 0,0) ou "gerber"


# Grupos de parâmetros para as interfaces: (atributo, rótulo, tipo, ajuda)
# tipo: float | int | bool | "optfloat" | "choice:a,b,c"
PARAM_GROUPS = {
    "Geral": [
        ("layer", "Camada de cobre", "choice:B_Cu,F_Cu",
         "Qual face da placa será fresada. B_Cu é o cobre de baixo (o usual em placa de face simples "
         "feita em casa); F_Cu é o de cima."),
        ("mirror", "Espelhar em X", "choice:auto,sim,não",
         "Para fresar o lado de baixo a placa é virada com o cobre para cima, então o desenho precisa "
         "ser espelhado. 'auto' espelha só quando a camada é B_Cu."),
        ("origin", "Origem X0 Y0", "choice:board,gerber",
         "'board': o canto inferior-esquerdo da placa vira X0 Y0 — zere a máquina nesse canto. "
         "'gerber': mantém as coordenadas originais do KiCad (geralmente longe da origem)."),
        ("safe_z", "Z seguro (mm)", float,
         "Altura em que a fresa se desloca entre um caminho e outro sem tocar em nada. "
         "Aumente se houver grampos ou fitas no caminho."),
        ("plunge_feed", "Avanço de mergulho (mm/min)", float,
         "Velocidade de descida da fresa em Z ao começar um caminho de isolação ou recorte. "
         "Valores baixos (30–60) protegem fresas finas."),
        ("spindle", "Spindle (RPM)", int,
         "Rotação enviada no comando M3 S. Numa 3018 o máximo costuma ser 10000; fresas finas "
         "gostam da rotação máxima."),
        ("dwell", "Espera após ligar spindle (s)", float,
         "Segundos parado depois do M3 para o motor atingir a rotação antes de tocar na placa."),
    ],
    "Isolação": [
        ("tool_dia", "Largura de corte da fresa V (mm)", float,
         "Largura do sulco que a fresa V abre na profundidade escolhida — NÃO é o diâmetro da haste. "
         "Para uma V de 30° a 0,05 mm de profundidade fica em torno de 0,1–0,2 mm. Se as trilhas "
         "saírem mais finas que o esperado, aumente este valor."),
        ("iso_passes", "Passes concêntricos", int,
         "Quantas voltas ao redor de cada trilha/pad. 1 abre só um sulco; 2 ou 3 removem mais cobre "
         "ao redor, facilitando a solda e evitando curtos por rebarba."),
        ("iso_overlap", "Sobreposição entre passes (0-1)", float,
         "Quanto cada passe adicional se sobrepõe ao anterior, em fração da largura de corte. "
         "0,5 = metade. Menor = passes mais afastados (mais rápido, pode deixar 'cristas')."),
        ("iso_depth", "Profundidade Z (mm)", float,
         "Quanto a fresa desce abaixo do cobre (negativo). O cobre tem ~0,035 mm; -0,05 a -0,1 é o "
         "usual. Placa empenada precisa de valor maior ou nivelamento."),
        ("iso_feed", "Avanço XY (mm/min)", float,
         "Velocidade de deslocamento enquanto corta a isolação. 100–200 numa 3018 com fresa V."),
    ],
    "Furação": [
        ("drill_depth", "Profundidade Z (mm)", float,
         "Z final do furo. Deve atravessar a placa e entrar um pouco na base de sacrifício: "
         "placa de 1,6 mm → -1,8 a -2,0."),
        ("drill_feed", "Avanço em Z (mm/min)", float,
         "Velocidade de descida ao furar. Brocas de 0,6–1,0 mm: 30–60 mm/min."),
        ("drill_step", "Passo Z por descida (mm, 0 = de uma vez)", float,
         "Quanto a fresa desce por vez em cada furo, até chegar na profundidade final. Vale para "
         "todos os furos: nos retos ela desce em degraus; nos fresados em círculo, desce um degrau "
         "a cada volta. 0,2–0,3 para fresas de 0,8 mm; 0 = desce de uma vez só."),
        ("drill_single_tool", "Broca única (mm, vazio = uma por Ø)", "optfloat",
         "Preencha para usar uma só broca/fresa em todos os furos e gerar um único arquivo. "
         "Vazio: gera um arquivo por diâmetro do projeto, para trocar a broca entre eles."),
        ("mill_holes", "Fresar furos maiores que a broca", bool,
         "Com broca única: furos maiores que ela são fresados em círculo, no diâmetro correto, "
         "em vez de ficarem menores. Desmarcado: todos os furos saem com o diâmetro da broca."),
    ],
    "Recorte": [
        ("cut_tool_dia", "Diâmetro da fresa (mm)", float,
         "Diâmetro da fresa de topo usada para recortar o contorno. O caminho passa por fora da "
         "borda, deslocado por metade deste valor."),
        ("cut_depth", "Profundidade final Z (mm)", float,
         "Z final do recorte. Um pouco além da espessura da placa: 1,6 mm → -1,7."),
        ("cut_step", "Profundidade por passe (mm)", float,
         "O recorte é feito em várias voltas, descendo este tanto por volta. 0,3–0,5 em FR4."),
        ("cut_feed", "Avanço XY (mm/min)", float,
         "Velocidade durante o recorte. 100–150 numa 3018 com fresa de 1 mm."),
        ("tabs", "Número de tabs (0 = sem)", int,
         "Pontes de material deixadas no contorno para a placa não soltar antes do fim. "
         "3–4 para placas pequenas. Depois quebre/lixe."),
        ("tab_width", "Largura da tab (mm)", float,
         "Comprimento de cada ponte ao longo da borda."),
        ("tab_height", "Espessura da tab (mm)", float,
         "Quanto de material fica embaixo da ponte (a fresa sobe até este Z nas tabs). "
         "0,4–0,6 segura bem e ainda quebra fácil."),
    ],
}


def config_from_dict(d: dict) -> Config:
    """Monta a Config a partir de valores em texto/JSON (formulários)."""
    cfg = Config()
    for items in PARAM_GROUPS.values():
        for attr, _label, kind, _help in items:
            if attr not in d:
                continue
            raw = d[attr]
            try:
                _apply(cfg, attr, kind, raw)
            except (TypeError, ValueError):
                raise ValueError(f"valor inválido em '{_label}': {raw!r}") from None
    return cfg


def _apply(cfg: Config, attr: str, kind, raw) -> None:
    if attr == "mirror":
        cfg.mirror = None if raw in (None, "auto", "") else (raw in (True, "sim", "true", "1"))
    elif kind == "optfloat":
        cfg.drill_single_tool = float(raw) if str(raw).strip() not in ("", "None", "null") else None
    elif kind is bool:
        setattr(cfg, attr, raw if isinstance(raw, bool) else str(raw).lower() in ("1", "true", "sim", "on"))
    elif kind is int:
        setattr(cfg, attr, int(float(raw)))
    elif kind is float:
        setattr(cfg, attr, float(raw))
    else:
        setattr(cfg, attr, str(raw))
