"""Importa os clientes e o saldo atual de uma aba da planilha antiga.

Cada cliente vira um cadastro com "saldo trazido da planilha antiga" = coluna RESTANTE da aba.
Só funciona em banco vazio (para não duplicar) e confere no fim se a soma bate com a planilha.

Uso:
  .venv/bin/python importar_planilha.py "PLANILHA.xlsx" --aba NOVEMBRO2026 --data 2026-11-01
"""
import argparse
import sys
from pathlib import Path

import openpyxl

import db


def ler_aba(arquivo, aba):
    """Devolve [(nome, saldo_em_centavos)]. Falha alto em qualquer coisa estranha."""
    wb = openpyxl.load_workbook(arquivo, data_only=True, read_only=True)
    if aba not in wb.sheetnames:
        raise SystemExit(f"ERRO: aba {aba!r} não existe. Abas: {wb.sheetnames}")
    linhas = list(wb[aba].iter_rows(values_only=True))
    # Fórmula sem valor calculado aparece como None em data_only, igual a célula vazia.
    # Precisa das fórmulas para não importar zero calado.
    formulas = list(openpyxl.load_workbook(arquivo, read_only=True)[aba].iter_rows(values_only=True))
    cab = next((i for i, l in enumerate(linhas[:10]) if len(l) > 4 and l[1] == "COMPRADO"), None)
    if cab is None:
        raise SystemExit("ERRO: não achei o cabeçalho (coluna B = 'COMPRADO') nas 10 primeiras linhas. "
                         "Este importador só entende o formato usado a partir de SETEMBRO25.2.")
    if not (str(linhas[cab][4] or "").strip().upper() in ("RESTANTE", "")):
        print(f"aviso: cabeçalho da coluna E é {linhas[cab][4]!r}, esperava RESTANTE", file=sys.stderr)
    saida, problemas = [], []
    for n, linha in enumerate(linhas[cab + 1:], start=cab + 2):
        nome, restante = (linha[0] if len(linha) > 0 else None), (linha[4] if len(linha) > 4 else None)
        nome = db.limpar_nome(str(nome)) if nome is not None else ""
        if not nome:
            if isinstance(restante, (int, float)) and abs(restante) >= 0.005:
                problemas.append(f"linha {n}: saldo {restante} sem nome de cliente")
            continue
        if restante is None:
            formula = formulas[n - 1][4] if len(formulas[n - 1]) > 4 else None
            if isinstance(formula, str) and formula.startswith("="):
                problemas.append(f"linha {n} ({nome}): RESTANTE é fórmula sem valor calculado "
                                 "(abra a planilha no Excel e salve de novo)")
                continue
            restante = 0  # célula realmente vazia: não deve nada
        if not isinstance(restante, (int, float)):
            problemas.append(f"linha {n} ({nome}): RESTANTE não é número: {restante!r}")
            continue
        saida.append((nome, round(restante * 100)))
    if problemas:
        raise SystemExit("ERRO: a planilha tem linhas que não sei importar:\n  " + "\n  ".join(problemas))
    if not saida:
        raise SystemExit("ERRO: nenhum cliente encontrado (a planilha foi salva pelo Excel? "
                         "Sem isso as fórmulas não têm valor calculado).")
    return saida


def importar(con, clientes, data):
    if con.execute("SELECT COUNT(*) FROM clientes").fetchone()[0]:
        raise SystemExit("ERRO: o banco já tem clientes. A importação só roda em banco vazio.")
    with con:
        for nome, saldo in clientes:
            con.execute("INSERT INTO clientes (nome, nome_busca, saldo_inicial, saldo_inicial_data)"
                        " VALUES (?, ?, ?, ?)", (db.formatar_nome(nome), db.normalizar(nome), saldo, data))
    soma_banco = sum(c["saldo"] for c in db.saldos(con))
    soma_planilha = sum(s for _, s in clientes)
    if soma_banco != soma_planilha or len(db.saldos(con)) != len(clientes):
        raise SystemExit(f"ERRO: conferência falhou: banco {soma_banco} x planilha {soma_planilha}")
    return soma_banco


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("planilha", type=Path)
    ap.add_argument("--aba", required=True)
    ap.add_argument("--data", required=True, help="data do saldo inicial, AAAA-MM-DD")
    ap.add_argument("--banco", default=str(Path(__file__).resolve().parent / "dados.db"))
    a = ap.parse_args()
    data = db.validar_data(a.data)
    clientes = ler_aba(a.planilha, a.aba)
    con = db.conectar(a.banco)
    try:
        total = importar(con, clientes, data)
    except Exception:
        con.close()
        raise
    devendo = sum(1 for _, s in clientes if s > 0)
    credito = sum(1 for _, s in clientes if s < 0)
    print(f"{len(clientes)} clientes importados de {a.aba!r} para {a.banco}")
    print(f"  devendo: {devendo}  com crédito: {credito}  zerados: {len(clientes) - devendo - credito}")
    print(f"  saldo total (soma de tudo): {db.centavos_para_reais(total)}")
    print(f"  total a receber (só quem deve): {db.centavos_para_reais(sum(s for _, s in clientes if s > 0))}")


if __name__ == "__main__":
    main()
