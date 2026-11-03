"""Lista clientes que tinham saldo num mês da planilha antiga e não aparecem no mês seguinte.

Na planilha antiga o saldo era puxado pelo nome (PROCV). Se o nome sumiu ou mudou, a dívida
saiu da conta sem pagamento registrado. Esta lista é para revisão manual: parte pode ter sido
limpeza proposital (dívida perdoada, pagou por fora), parte pode ser nome que mudou.

Uso:
  .venv/bin/python relatorio_saldos_sumidos.py "PLANILHA.xlsx" saida.csv
"""
import csv
import difflib
import sys

import openpyxl

import db

# Abas no formato novo (B = COMPRADO, E = RESTANTE), em ordem cronológica.
ABAS = ["SETEMBRO25.2", "OUTUBRO25", "NOVEMBRO25", "DEZEMBRO25", "JANEIRO26", "FEVEREIRO26", "MARCO26",
        "ABRIL26", "MAIO26", "JUNHO26", "JULHO26", "AGOSTO26", "SETEMBRO26", "OUTUBRO26", "NOVEMBRO2026"]


def ler(ws):
    linhas = list(ws.iter_rows(max_col=5, values_only=True))
    cab = next(i for i, l in enumerate(linhas[:10]) if l[1] == "COMPRADO")
    return {db.limpar_nome(str(l[0])): l[4] for l in linhas[cab + 1:]
            if l[0] is not None and str(l[0]).strip()}


def main(arquivo, saida):
    wb = openpyxl.load_workbook(arquivo, data_only=True, read_only=True)
    faltando = [a for a in ABAS if a not in wb.sheetnames]
    if faltando:
        raise SystemExit(f"ERRO: abas não encontradas: {faltando}")
    meses = [(a, ler(wb[a])) for a in ABAS]
    final = meses[-1][1]
    nomes_finais = {db.normalizar(n): n for n in final}
    linhas = []
    for (aba_ant, ant), (aba, atual) in zip(meses, meses[1:]):
        for nome, saldo in ant.items():
            if nome in atual or not isinstance(saldo, (int, float)) or abs(saldo) < 0.005:
                continue
            parecido = difflib.get_close_matches(db.normalizar(nome), nomes_finais, n=1, cutoff=0.8)
            linhas.append([aba_ant, aba, nome, f"{saldo:.2f}".replace(".", ","),
                           nomes_finais[parecido[0]] if parecido else ""])
    with open(saida, "w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig: Excel abre com acento certo
        w = csv.writer(f, delimiter=";")
        w.writerow(["Estava em", "Sumiu em", "Cliente", "Saldo que sumiu (R$)",
                    "Nome parecido no cadastro atual (conferir)"])
        w.writerows(linhas)
    total = sum(float(l[3].replace(",", ".")) for l in linhas)
    print(f"{len(linhas)} saldos sumidos, total R$ {total:.2f}, "
          f"{sum(1 for l in linhas if l[4])} com nome parecido no cadastro atual -> {saida}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    main(sys.argv[1], sys.argv[2])
