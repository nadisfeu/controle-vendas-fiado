"""Cria um banco de demonstração com dados 100% fictícios (para testar o sistema ou tirar fotos das telas).

Uso:
  python demo.py                 # cria demo.db
  APP_BANCO=demo.db python app.py
"""
import random
import sys
from datetime import date, timedelta
from pathlib import Path

import db

CLIENTES = [  # nome, telefone, observação, saldo trazido da "planilha antiga" (centavos)
    ("Marta Jardim", "(31) 90000-0001", "Vizinha da rua de cima", 45000),
    ("Otávio Ribeira", "", "Trabalha na padaria", -1500),
    ("Neusa Pomar", "(31) 90000-0003", "", 12000),
    ("Heloisa Campos", "", "Indicação da Marta", 0),
    ("Lurdes Feira do Bairro", "(31) 90000-0005", "Barraca de verduras", 83000),
    ("Teresa Guarda-chuva", "", "", 21000),
    ("Jandira Escola Estadual", "(31) 90000-0007", "Merendeira", 30500),
    ("Rute Costureira", "", "", 0),
    ("Zélia da Banca", "(31) 90000-0009", "", 9900),
    ("Odete Salão", "", "Paga todo dia 10", 54000),
]
PRODUTOS = [  # nome, preço (centavos), estoque, tipo, marca, código (marcas e códigos fictícios)
    ("Perfume Floral 100 ml", 20000, 6, "Perfume", "Flor de Lis", "10021"),
    ("Hidratante Corporal", 4590, 12, "Hidratante", "Flor de Lis", "10045"),
    ("Batom Matte", 3290, 15, "Maquiagem", "Cor da Pele", "20013"),
    ("Kit Presente", 12000, 3, "Kit", "", ""),
    ("Sabonete Artesanal", 750, 40, "Sabonete", "Feito em Casa", ""),
    ("Colônia Infantil", 5990, 0, "Perfume", "Cor da Pele", "20077"),
]


def criar(caminho):
    if Path(caminho).exists():
        raise SystemExit(f"ERRO: {caminho} já existe. Apague antes para recriar.")
    con = db.conectar(caminho)
    cli = {nome: db.criar_cliente(con, nome, tel, obs, saldo, "2026-10-01") for nome, tel, obs, saldo in CLIENTES}
    pro = {nome: db.criar_produto(con, nome, preco, estoque, codigo, marca, tipo)
           for nome, preco, estoque, tipo, marca, codigo in PRODUTOS}
    item = lambda nome, qtd, preco=None: dict(produto_id=pro[nome], descricao="", quantidade=qtd,
                                              preco_unit=preco if preco is not None else db.produto(con, pro[nome])["preco"])
    db.registrar_venda(con, cli["Marta Jardim"], "2026-10-05", [item("Perfume Floral 100 ml", 1, 17000), item("Sabonete Artesanal", 4)])
    db.registrar_venda(con, cli["Neusa Pomar"], "2026-10-12", [item("Hidratante Corporal", 2), item("Batom Matte", 1)])
    db.registrar_pagamento(con, cli["Lurdes Feira do Bairro"], "2026-10-15", 30000, "Pix")
    db.registrar_pagamento(con, cli["Odete Salão"], "2026-10-10", 20000, "Dinheiro")
    db.registrar_venda(con, cli["Jandira Escola Estadual"], "2026-10-20", [item("Kit Presente", 1)])
    db.registrar_venda(con, cli["Marta Jardim"], "2026-11-02", [item("Kit Presente", 1),
                                                               dict(produto_id=None, descricao="Embrulho para presente",
                                                                    quantidade=1, preco_unit=500)])
    db.registrar_pagamento(con, cli["Marta Jardim"], "2026-11-03", 25000, "Pix")
    db.registrar_venda(con, cli["Heloisa Campos"], "2026-11-03", [item("Batom Matte", 2, 3000), item("Sabonete Artesanal", 3)])
    db.registrar_pagamento(con, cli["Zélia da Banca"], "2026-11-01", 9900, "Dinheiro")
    db.registrar_venda(con, cli["Rute Costureira"], "2026-11-02", [item("Hidratante Corporal", 1)])
    db.registrar_pagamento(con, cli["Teresa Guarda-chuva"], "2026-11-02", 5000, "Cartão")
    gerar_ano(con, cli, pro)
    con.close()


def gerar_ano(con, cli, pro, fim=date(2026, 11, 3)):
    """Vendas e pagamentos fictícios de jan/2026 até `fim`, para os gráficos terem o que mostrar.

    Semente fixa: sempre gera os mesmos dados. Vende mais perto dos dias 5 e 20 (dia de pagamento).
    """
    sorteio = random.Random(2026)
    nomes_produtos, nomes_clientes = list(pro), list(cli)
    dia = date(2026, 1, 1)
    while dia < fim:
        chance = 0.75 if dia.day in (5, 6, 20, 21) else 0.3 if dia.weekday() < 5 else 0.12
        for _ in range(3 if dia.day in (5, 20) else 1):
            if sorteio.random() > chance:
                continue
            cliente = cli[sorteio.choice(nomes_clientes)]
            itens = [dict(produto_id=pro[n], descricao="", quantidade=sorteio.randint(1, 2),
                          preco_unit=db.produto(con, pro[n])["preco"])
                     for n in sorteio.sample(nomes_produtos, sorteio.randint(1, 2))]
            db.registrar_venda(con, cliente, dia.isoformat(), itens)
            if sorteio.random() < 0.8:   # a maioria paga depois, em parte ou tudo
                total = sum(i["quantidade"] * i["preco_unit"] for i in itens)
                quando = min(fim, dia + timedelta(days=sorteio.randint(3, 25)))
                db.registrar_pagamento(con, cliente, quando.isoformat(), total // sorteio.choice([1, 1, 2]),
                                       sorteio.choice(["Pix", "Dinheiro", "Cartão"]))
        dia += timedelta(days=1)
    with con:   # devolve o estoque ao valor da demonstração (as vendas geradas o deixariam negativo)
        con.executemany("UPDATE produtos SET estoque = ? WHERE id = ?", [(e, pro[n]) for n, _, e, *_ in PRODUTOS])


if __name__ == "__main__":
    destino = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parent / "demo.db")
    criar(destino)
    print(f"banco de demonstração criado: {destino}")
