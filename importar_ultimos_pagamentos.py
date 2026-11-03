"""Lê na planilha antiga o último mês em que cada cliente pagou e guarda no cadastro.

Serve para a lista "há muito tempo sem pagar" já funcionar com o histórico de antes do sistema.
A planilha teve dois formatos: até set/2025 (PAGO na coluna G, cabeçalho na linha 2) e depois
(PAGO na coluna D, cabeçalho na linha 3 ou 4). O cliente é achado pelo nome sem acento/maiúscula,
o mesmo critério do cadastro.

Uso:
  python importar_ultimos_pagamentos.py "PLANILHA.xlsx"              # só mostra o que faria
  python importar_ultimos_pagamentos.py "PLANILHA.xlsx" --aplicar    # grava no banco
"""
import argparse
import re
from pathlib import Path

import openpyxl

import db

MESES = {"JANEIRO": 1, "FEVEREIRO": 2, "MARCO": 3, "ABRIL": 4, "MAIO": 5, "JUNHO": 6, "JULHO": 7,
         "AGOSTO": 8, "SETEMBRO": 9, "OUTUBRO": 10, "NOVEMBRO": 11, "DEZEMBRO": 12}


def mes_da_aba(titulo, primeira_celula):
    """'OUTUBRO25' -> (2025, 10); 'NOVEMBRO2026' -> (2026, 11); 'MARÇO' + 'Planilha MARÇO 2023' -> (2023, 3)."""
    texto = db.normalizar(titulo).upper()
    nome = next((n for n in MESES if texto.startswith(n)), None)
    if nome is None:
        return None
    resto = re.match(r"(\d{2,4})?", texto[len(nome):]).group(1)
    if resto:
        ano = int(resto) + (2000 if len(resto) == 2 else 0)
    else:
        achado = re.search(r"(20\d{2})", str(primeira_celula or ""))
        if not achado:
            raise SystemExit(f"ERRO: aba {titulo!r} não diz o ano (nem no título, nem em A1).")
        ano = int(achado.group(1))
    return ano, MESES[nome]


def colunas_da_aba(linhas, titulo):
    """(linha do cabeçalho, coluna do nome, coluna do PAGO), procurando nas 6 primeiras linhas."""
    for n, linha in enumerate(linhas[:6]):
        rotulos = [db.normalizar(str(v or "")).upper() for v in linha]
        if "COMPRADO" in rotulos and "PAGO" in rotulos:
            return n, 0, rotulos.index("PAGO")
    raise SystemExit(f"ERRO: aba {titulo!r} sem cabeçalho com COMPRADO e PAGO nas 6 primeiras linhas.")


def ultimos_pagamentos(arquivo):
    """{nome normalizado: 'AAAA-MM' do último mês com PAGO > 0}, e a lista de abas ignoradas."""
    wb = openpyxl.load_workbook(arquivo, data_only=True, read_only=True)
    ultimo, ignoradas = {}, []
    for ws in wb.worksheets:
        linhas = list(ws.iter_rows(values_only=True))
        if not linhas or not any(any(v is not None for v in l) for l in linhas):
            ignoradas.append(ws.title)
            continue
        quando = mes_da_aba(ws.title, linhas[0][0] if linhas[0] else None)
        if quando is None:
            raise SystemExit(f"ERRO: não sei de que mês é a aba {ws.title!r}.")
        cab, col_nome, col_pago = colunas_da_aba(linhas, ws.title)
        mes = f"{quando[0]}-{quando[1]:02d}"
        for linha in linhas[cab + 1:]:
            if len(linha) <= col_pago or linha[col_nome] is None:
                continue
            nome, pago = db.normalizar(str(linha[col_nome])), linha[col_pago]
            if nome and isinstance(pago, (int, float)) and pago > 0.005:
                ultimo[nome] = max(ultimo.get(nome, mes), mes)
    return ultimo, ignoradas


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("planilha", type=Path)
    ap.add_argument("--banco", default=str(Path(__file__).resolve().parent / "dados.db"))
    ap.add_argument("--aplicar", action="store_true", help="grava no banco (sem isso só mostra)")
    a = ap.parse_args()
    ultimo, ignoradas = ultimos_pagamentos(a.planilha)
    con = db.conectar(a.banco)
    clientes = db.saldos(con, apenas_ativos=False)
    achados = {c["id"]: ultimo[c["nome_busca"]] for c in clientes if c["nome_busca"] in ultimo}
    devendo = [c for c in clientes if c["saldo"] > 0]
    sem_historico = [c["nome"] for c in devendo if c["id"] not in achados]
    print(f"abas ignoradas (vazias): {ignoradas}")
    print(f"{len(achados)} de {len(clientes)} clientes têm pagamento na planilha")
    print(f"{len(devendo) - len(sem_historico)} de {len(devendo)} que devem têm último pagamento conhecido;"
          f" {len(sem_historico)} sem nenhum pagamento achado pelo nome")
    if a.aplicar:
        with con:
            con.executemany("UPDATE clientes SET ultimo_pagamento_planilha = ? WHERE id = ?",
                            [(mes, cid) for cid, mes in achados.items()])
        print("gravado.")
    else:
        print("(nada gravado: rode com --aplicar para gravar)")
    con.close()


if __name__ == "__main__":
    main()
