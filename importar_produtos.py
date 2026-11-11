"""Importa uma lista de produtos (ex.: os itens de uma nota de compra) para o cadastro.

Arquivo CSV com ponto e vírgula e cabeçalho:  codigo;tipo;marca;nome;preco;quantidade
(preco como "174,90"; codigo, tipo e marca podem ficar vazios)

Para cada linha:
- código já cadastrado          -> soma a quantidade ao estoque (não mexe em nome nem preço);
- nome parecido com um existente -> NÃO faz nada e avisa para conferir (pode ser o mesmo produto);
- senão                          -> cadastra. Tipo e marca reaproveitam a grafia que já existe
                                    ("Colônia" vira "Colonia" se for assim que já está cadastrado).

Uso:
  python importar_produtos.py lista.csv              # só mostra o que faria
  python importar_produtos.py lista.csv --aplicar    # grava (com cópia do banco antes)
"""
import argparse
import csv
from datetime import datetime
from pathlib import Path

import db

COLUNAS = ["codigo", "tipo", "marca", "nome", "preco", "quantidade"]


def ler_lista(arquivo):
    with open(arquivo, encoding="utf-8-sig", newline="") as f:
        leitor = csv.DictReader(f, delimiter=";")
        if [c.strip().lower() for c in (leitor.fieldnames or [])] != COLUNAS:
            raise SystemExit(f"ERRO: o cabeçalho tem que ser exatamente: {';'.join(COLUNAS)}")
        linhas = []
        for n, l in enumerate(leitor, start=2):
            l = {k.strip().lower(): (v or "").strip() for k, v in l.items()}
            if not l["nome"]:
                raise SystemExit(f"ERRO: linha {n} sem nome")
            try:
                linhas.append(dict(l, preco=db.reais_para_centavos(l["preco"]),
                                   quantidade=db.ler_quantidade(l["quantidade"]), linha=n))
            except db.ErroDeCadastro as e:
                raise SystemExit(f"ERRO na linha {n} ({l['nome']}): {e}")
        return linhas


def _grafia_existente(valor, existentes):
    """'Colônia' -> 'Colonia' se 'Colonia' já existe (mesma palavra sem acento/maiúscula)."""
    alvo = db.normalizar(db.formatar_nome(valor))
    return next((e for e in existentes if db.normalizar(e) == alvo), valor)


def planejar(con, linhas):
    """Devolve [(acao, linha, produto_existente_ou_parecidos)] sem gravar nada."""
    plano, codigos_na_lista = [], set()
    for l in linhas:
        if l["codigo"]:
            if l["codigo"].upper() in codigos_na_lista:
                raise SystemExit(f"ERRO: código {l['codigo']} repetido na lista (linha {l['linha']})")
            codigos_na_lista.add(l["codigo"].upper())
            existente = con.execute("SELECT * FROM produtos WHERE upper(codigo) = upper(?)", (l["codigo"],)).fetchone()
            if existente:
                plano.append(("somar", l, existente))
                continue
        parecidos = db.produtos_parecidos(con, l["nome"], _grafia_existente(l["marca"], db.marcas(con)) if l["marca"] else "")
        plano.append(("conferir", l, parecidos) if parecidos else ("criar", l, None))
    return plano


def aplicar(con, plano):
    tipos, marcas = db.tipos(con), db.marcas(con)
    for acao, l, existente in plano:
        if acao == "somar" and l["quantidade"]:
            db.adicionar_estoque(con, existente["id"], l["quantidade"])
        elif acao == "criar":
            tipo = _grafia_existente(l["tipo"], tipos) if l["tipo"] else ""
            marca = _grafia_existente(l["marca"], marcas) if l["marca"] else ""
            db.criar_produto(con, l["nome"], l["preco"], l["quantidade"], l["codigo"], marca, tipo)
            tipos, marcas = db.tipos(con), db.marcas(con)


def mostrar(plano):
    for acao, l, extra in plano:
        preco = db.centavos_para_reais(l["preco"])
        if acao == "somar":
            aviso = "" if extra["preco"] == l["preco"] else f" (atenção: preço cadastrado {db.centavos_para_reais(extra['preco'])}, lista {preco})"
            print(f"  SOMAR    +{l['quantidade']} ao estoque de \"{extra['nome']}\" (cód. {l['codigo']}){aviso}")
        elif acao == "criar":
            print(f"  CRIAR    \"{db.formatar_nome_produto(l['nome'])}\" {preco}, {l['quantidade']} em estoque"
                  f"{', cód. ' + l['codigo'] if l['codigo'] else ''}")
        else:
            nomes = "; ".join(f"\"{p['nome']}\"" for p in extra)
            print(f"  CONFERIR \"{l['nome']}\" parece com {nomes} -> não foi importado; cadastre pelo site (ele pergunta)"
                  f" ou ponha o código dele na lista")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("lista", type=Path)
    ap.add_argument("--banco", default=str(Path(__file__).resolve().parent / "dados.db"))
    ap.add_argument("--aplicar", action="store_true", help="grava no banco (sem isso só mostra)")
    a = ap.parse_args()
    if not Path(a.banco).exists():
        raise SystemExit(f"ERRO: banco não encontrado em {a.banco}")
    linhas = ler_lista(a.lista)
    con = db.conectar(a.banco)
    plano = planejar(con, linhas)
    mostrar(plano)
    resumo = {k: sum(1 for p in plano if p[0] == k) for k in ("criar", "somar", "conferir")}
    print(f"{resumo['criar']} a criar, {resumo['somar']} a somar no estoque, {resumo['conferir']} para conferir")
    if a.aplicar:
        copia = Path(a.banco).parent / "backups" / f"antes_importar_{datetime.now():%Y-%m-%d_%H%M%S}.db"
        db.copiar_banco(con, copia)
        aplicar(con, plano)
        print(f"gravado. cópia de antes: {copia}")
    else:
        print("(nada gravado: rode de novo com --aplicar)")
    con.close()


if __name__ == "__main__":
    main()
