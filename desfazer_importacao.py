"""Desfaz a última importação de uma lista de produtos, voltando a cópia que o importar_produtos.py fez antes.

Só volta se a ÚNICA diferença entre o banco de agora e a cópia forem os produtos daquela lista.
Se depois da importação houve venda, pagamento, cliente novo, produto editado etc., recusa
(voltar a cópia apagaria esses lançamentos) e diz o que mudou.

Uso:
  python desfazer_importacao.py lista.csv              # só confere e mostra o que faria
  python desfazer_importacao.py lista.csv --aplicar    # volta a cópia (guarda o banco de agora antes)
"""
import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

import db
import importar_produtos

IGNORAR = {"meta", "sqlite_sequence"}    # anotações do sistema e contadores internos


def _tabelas(con):
    return [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")
            if r[0] not in IGNORAR]


def _linhas(con, tabela):
    return {tuple(r) for r in con.execute(f'SELECT * FROM "{tabela}"')}


def conferir(atual, copia, lista):
    """Devolve (produtos_que_vao_sumir, problemas). Sem problemas = pode voltar a cópia."""
    problemas = []
    for tabela in sorted(set(_tabelas(atual)) | set(_tabelas(copia))):
        if tabela == "produtos":
            continue
        if _linhas(atual, tabela) != _linhas(copia, tabela):
            problemas.append(f"a tabela {tabela} mudou depois da importação (venda, pagamento ou cadastro novo)")
    agora = {r["id"]: tuple(r) for r in atual.execute("SELECT * FROM produtos")}
    antes = {r["id"]: tuple(r) for r in copia.execute("SELECT * FROM produtos")}
    for pid, linha in antes.items():
        if agora.get(pid) != linha:
            problemas.append(f"o produto #{pid} ({linha[1]}) foi alterado ou apagado depois da importação")
    novos = [atual.execute("SELECT * FROM produtos WHERE id = ?", (pid,)).fetchone() for pid in agora if pid not in antes]
    codigos_lista = {l["codigo"].upper() for l in lista if l["codigo"]}
    nomes_lista = {db.normalizar(db.formatar_nome_produto(l["nome"])) for l in lista if not l["codigo"]}
    for p in novos:
        if not ((p["codigo"] or "").upper() in codigos_lista or db.normalizar(p["nome"]) in nomes_lista):
            problemas.append(f"o produto \"{p['nome']}\" não é desta lista: foi cadastrado à parte")
    if len(novos) != len(lista):
        problemas.append(f"a lista tem {len(lista)} produtos, mas apareceram {len(novos)} desde a cópia")
    return novos, problemas


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("lista", type=Path)
    ap.add_argument("--banco", default=str(Path(__file__).resolve().parent / "dados.db"))
    ap.add_argument("--copia", type=Path, help="cópia a voltar (padrão: a mais recente antes_importar_*.db)")
    ap.add_argument("--aplicar", action="store_true", help="volta a cópia de verdade (sem isso só confere)")
    a = ap.parse_args()
    if not Path(a.banco).exists():
        raise SystemExit(f"ERRO: banco não encontrado em {a.banco}")
    copias = sorted((Path(a.banco).parent / "backups").glob("antes_importar_*.db"))
    copia = a.copia or (copias[-1] if copias else None)
    if not copia or not copia.exists():
        raise SystemExit("ERRO: não achei a cópia de antes da importação (backups/antes_importar_*.db)")
    lista = importar_produtos.ler_lista(a.lista)
    atual = db.conectar(a.banco)
    antiga = sqlite3.connect(f"file:{copia}?mode=ro", uri=True)
    antiga.row_factory = sqlite3.Row
    novos, problemas = conferir(atual, antiga, lista)
    print(f"cópia: {copia.name}")
    if problemas:
        antiga.close(), atual.close()
        raise SystemExit("NÃO VOLTEI A CÓPIA, porque:\n  - " + "\n  - ".join(problemas))
    for p in novos:
        print(f"  SAI  \"{p['nome']}\"{' (cód. ' + p['codigo'] + ')' if p['codigo'] else ''}")
    print(f"{len(novos)} produtos saem; vendas, pagamentos e clientes ficam como estão")
    if a.aplicar:
        guardado = Path(a.banco).parent / "backups" / f"antes_desfazer_{datetime.now():%Y-%m-%d_%H%M%S}.db"
        db.copiar_banco(atual, guardado)
        antiga.backup(atual)                 # escreve a cópia por cima, pela API do SQLite (seguro com o site aberto)
        print(f"pronto: a lista saiu e pode ser importada de novo. banco de antes de desfazer: {guardado}")
    else:
        print("(nada mudou: rode de novo com --aplicar)")
    antiga.close()
    atual.close()


if __name__ == "__main__":
    main()
