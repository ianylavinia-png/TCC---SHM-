"""
preparar_historico.py — converte o CSV de backup do monitoramento em arquivos binários compactos
que o dashboard carrega em ~1 s (sem parsear texto no navegador).

Uso (na pasta do projeto):
    py preparar_historico.py
    py preparar_historico.py "outro_arquivo.csv" --saida dados

Entrada: CSV com as colunas  Data/Hora, ID da Placa, Aceleração X, Aceleração Y, Aceleração Z [, dt]
         (separador , ou ; ; vírgula decimal com ou sem aspas; data dd/mm/aaaa ou mm/dd/aaaa)
Saída (pasta --saida):
    historico.json            → manifesto lido pelo dashboard (placas, nº de leituras, período, limpeza)
    historico_<placa>.bin     → por placa, em colunas little-endian:
                                  uint32[n]  data/hora "de parede" em segundos desde 1970 (sem fuso)
                                  float32[n] X | float32[n] Y | float32[n] Z
Só usa a biblioteca padrão do Python (não precisa de pandas/numpy).
"""
import argparse
import calendar
import csv
import json
import sys
from array import array
from datetime import datetime, timezone
from pathlib import Path

CSV_PADRAO = "BACKUP DADOS - IMPORTANTE - Sketch_sep1a - Página1_v1.csv"


def detectar_separador(primeira_linha: str) -> str:
    return max([",", ";", "\t"], key=primeira_linha.count)


def partes_data(texto: str):
    """'09/10/2026 12:32:27' -> (09, 10, 2026, 12, 32, 27) sem assumir a ordem dia/mês."""
    data, _, hora = texto.strip().partition(" ")
    a, b, ano = (int(p) for p in data.replace("-", "/").split("/"))
    h = m = s = 0
    if hora:
        hp = hora.split(":")
        h, m = int(hp[0]), int(hp[1])
        s = int(float(hp[2])) if len(hp) > 2 else 0
    if a > 31:                       # ISO aaaa-mm-dd → devolve como (mês, dia, ano)
        a, b, ano = b, ano, a
        return b, a, ano, h, m, s, "iso"
    if ano < 100:
        ano += 2000
    return a, b, ano, h, m, s, None


def numero(txt: str) -> float:
    return float(txt.strip().replace(",", "."))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", nargs="?", default=CSV_PADRAO, help="arquivo CSV de entrada")
    ap.add_argument("--saida", default="dados", help="pasta de saída (padrão: dados)")
    ap.add_argument("--formato-data", choices=["auto", "dmy", "mdy"], default="auto",
                    help="ordem da data: dia/mês (dmy) ou mês/dia (mdy); 'auto' detecta")
    args = ap.parse_args()

    entrada = Path(args.csv)
    if not entrada.exists():
        sys.exit(f"Arquivo não encontrado: {entrada}")
    saida = Path(args.saida)
    saida.mkdir(parents=True, exist_ok=True)

    # ---------- 1ª passada: lê tudo guardando a data "crua" ----------
    with entrada.open(encoding="utf-8-sig", newline="") as f:
        sep = detectar_separador(f.readline())
    brutos = {}                      # placa -> lista de (a, b, ano, h, m, s, x, y, z)
    voto_dmy = voto_mdy = 0
    ruins, n_ruins = [], 0
    with entrada.open(encoding="utf-8-sig", newline="") as f:
        leitor = csv.reader(f, delimiter=sep)
        cabecalho = next(leitor)
        for nlinha, row in enumerate(leitor, start=2):
            if not row or not row[0].strip():
                continue
            try:
                a, b, ano, h, mi, s, iso = partes_data(row[0])
                x, y, z = numero(row[2]), numero(row[3]), numero(row[4])
            except (ValueError, IndexError):
                n_ruins += 1
                if len(ruins) < 10:
                    ruins.append((nlinha, row))
                continue
            if iso:
                voto_mdy += 1
            else:
                voto_dmy += a > 12
                voto_mdy += b > 12
            brutos.setdefault(row[1].strip(), []).append((a, b, ano, h, mi, s, x, y, z))

    if args.formato_data != "auto":
        ordem = args.formato_data
    elif voto_dmy and not voto_mdy:
        ordem = "dmy"
    elif voto_mdy and not voto_dmy:
        ordem = "mdy"
    else:
        sys.exit("Não deu para saber se a data é dia/mês ou mês/dia. Rode de novo com --formato-data dmy ou mdy.")

    # ---------- 2ª etapa: converte, ordena, limpa e grava ----------
    manifesto = {
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
        "fonte": entrada.name,
        "formato_data": ordem,
        "formato_bin": "uint32[n] segundos (horário de parede, sem fuso) | float32[n] X | float32[n] Y | float32[n] Z — little-endian",
        "placas": {},
    }
    total = 0
    for placa, linhas in sorted(brutos.items()):
        regs = []
        for a, b, ano, h, mi, s, x, y, z in linhas:
            mes, dia = (b, a) if ordem == "dmy" else (a, b)
            regs.append((calendar.timegm((ano, mes, dia, h, mi, s, 0, 0, 0)), x, y, z))
        fora_ordem = sum(1 for p, q in zip(regs, regs[1:]) if q[0] < p[0])
        regs.sort(key=lambda r: r[0])                         # sort estável: mantém a ordem de chegada no empate
        limpos = [r for i, r in enumerate(regs) if i == 0 or r != regs[i - 1]]   # remove duplicatas exatas consecutivas
        duplicadas = len(regs) - len(limpos)

        t = array("I", (r[0] for r in limpos))
        cx = array("f", (r[1] for r in limpos))
        cy = array("f", (r[2] for r in limpos))
        cz = array("f", (r[3] for r in limpos))
        if sys.byteorder != "little":
            for col in (t, cx, cy, cz):
                col.byteswap()
        nome_bin = f"historico_{placa}.bin"
        with (saida / nome_bin).open("wb") as fb:
            for col in (t, cx, cy, cz):
                col.tofile(fb)

        fmt = lambda seg: datetime.fromtimestamp(seg, timezone.utc).strftime("%d/%m/%Y %H:%M:%S")
        manifesto["placas"][placa] = {
            "arquivo": nome_bin,
            "n": len(limpos),
            "inicio": fmt(limpos[0][0]),
            "fim": fmt(limpos[-1][0]),
            "linhas_lidas": len(linhas),
            "fora_de_ordem_corrigidas": fora_ordem,
            "duplicadas_removidas": duplicadas,
        }
        total += len(limpos)
        print(f"{placa}: {len(limpos):>9,} leituras  {fmt(limpos[0][0])} → {fmt(limpos[-1][0])}  "
              f"(ordenadas {fora_ordem}, duplicadas removidas {duplicadas})  → {nome_bin}")

    manifesto["total"] = total
    manifesto["linhas_ignoradas"] = n_ruins
    with (saida / "historico.json").open("w", encoding="utf-8") as fj:
        json.dump(manifesto, fj, ensure_ascii=False, indent=2)

    print(f"\nData lida como {'dia/mês' if ordem == 'dmy' else 'mês/dia'}/ano · separador {sep!r} · "
          f"{total:,} leituras gravadas em '{saida}/'")
    if n_ruins:
        print(f"⚠ {n_ruins} linha(s) ignoradas por erro de formato, ex.: {ruins[:3]}")


if __name__ == "__main__":
    main()
