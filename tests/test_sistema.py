"""Testes com dados de brinquedo. Rodar:  .venv/bin/python -m pytest -q"""
import json
import sys
from pathlib import Path

import openpyxl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import db  # noqa: E402
from app import criar_app  # noqa: E402
from db import ErroDeCadastro  # noqa: E402
import importar_planilha  # noqa: E402


@pytest.fixture
def con(tmp_path):
    c = db.conectar(str(tmp_path / "t.db"))
    yield c
    c.close()


# ---------------------------------------------------------------- dinheiro

@pytest.mark.parametrize("texto, esperado", [
    ("12,50", 1250), ("12,5", 1250), ("1.234,56", 123456), ("R$ 10", 1000), ("12.5", 1250),
    ("1.234", 123400), ("0,01", 1), ("1.000.000,00", 100000000), ("-15,00", -1500), (" 7 ", 700),
])
def test_reais_para_centavos(texto, esperado):
    assert db.reais_para_centavos(texto) == esperado


@pytest.mark.parametrize("texto", ["", "abc", "12,345", "1,2,3", "R$"])
def test_reais_invalido_falha(texto):
    with pytest.raises(ErroDeCadastro):
        db.reais_para_centavos(texto)


@pytest.mark.parametrize("centavos, esperado", [
    (0, "R$ 0,00"), (5, "R$ 0,05"), (123456, "R$ 1.234,56"), (-1000, "-R$ 10,00"), (100000000, "R$ 1.000.000,00"),
])
def test_centavos_para_reais(centavos, esperado):
    assert db.centavos_para_reais(centavos) == esperado


# ---------------------------------------------------------------- saldo

def test_saldo_completo(con):
    ana = db.criar_cliente(con, "Ana Jardim", saldo_inicial=10000, saldo_inicial_data="2026-11-01")
    perfume = db.criar_produto(con, "Perfume", 1500)
    db.registrar_venda(con, ana, "2026-11-05", [
        dict(produto_id=perfume, descricao="", quantidade=2, preco_unit=1500),
        dict(produto_id=None, descricao="Encomenda", quantidade=1, preco_unit=500),
    ])
    c = db.cliente(con, ana)
    assert (c["saldo_inicial"], c["comprado"], c["pago"], c["saldo"]) == (10000, 3500, 0, 13500)

    pag = db.registrar_pagamento(con, ana, "2026-11-06", 5000, "Pix")
    assert db.cliente(con, ana)["saldo"] == 8500

    # cancelar o pagamento devolve a dívida; cancelar de novo é erro
    db.cancelar_pagamento(con, pag)
    assert db.cliente(con, ana)["saldo"] == 13500
    with pytest.raises(ErroDeCadastro):
        db.cancelar_pagamento(con, pag)


def test_cancelar_venda_tira_do_saldo_mas_fica_no_extrato(con):
    bia = db.criar_cliente(con, "Bia")
    v = db.registrar_venda(con, bia, "2026-11-05", [dict(produto_id=None, descricao="x", quantidade=1, preco_unit=999)])
    db.cancelar_venda(con, v)
    assert db.cliente(con, bia)["saldo"] == 0
    ext = db.extrato(con, bia)
    assert len(ext) == 1 and ext[0]["cancelado"] and ext[0]["saldo_apos"] == 0


def test_mudar_preco_nao_altera_venda_antiga(con):
    c = db.criar_cliente(con, "Carla")
    p = db.criar_produto(con, "Batom", 2000)
    db.registrar_venda(con, c, "2026-11-01", [dict(produto_id=p, descricao="", quantidade=1, preco_unit=2000)])
    db.atualizar_produto(con, p, "Batom", 9900, True)
    assert db.cliente(con, c)["comprado"] == 2000


def test_pagar_a_mais_vira_credito(con):
    c = db.criar_cliente(con, "Dora", saldo_inicial=1000)
    db.registrar_pagamento(con, c, "2026-11-02", 1500)
    assert db.cliente(con, c)["saldo"] == -500


def test_extrato_ordem_e_saldo_corrente(con):
    c = db.criar_cliente(con, "Eva", saldo_inicial=1000, saldo_inicial_data="2026-11-01")
    db.registrar_pagamento(con, c, "2026-11-10", 300)
    db.registrar_venda(con, c, "2026-11-03", [dict(produto_id=None, descricao="a", quantidade=2, preco_unit=100)])
    ext = db.extrato(con, c)  # mais novo primeiro
    assert [l["tipo"] for l in ext] == ["pagamento", "venda", "inicial"]
    assert [l["saldo_apos"] for l in ext] == [900, 1200, 1000]


def test_nome_repetido_bloqueado_mesmo_com_acento_e_maiuscula(con):
    db.criar_cliente(con, "Lurdes Jardim")
    with pytest.raises(ErroDeCadastro, match="Já existe"):
        db.criar_cliente(con, "  LURDES   jardim ")
    db.criar_produto(con, "Sabonete", 500)
    with pytest.raises(ErroDeCadastro):
        db.criar_produto(con, "sabonete", 600)


def test_renomear_para_si_mesmo_pode_e_para_outro_nao(con):
    a = db.criar_cliente(con, "Fabi")
    db.criar_cliente(con, "Gabi")
    db.atualizar_cliente(con, a, "FABI", "", "", 0, True)
    with pytest.raises(ErroDeCadastro):
        db.atualizar_cliente(con, a, "gabi", "", "", 0, True)


@pytest.mark.parametrize("item", [
    dict(produto_id=None, descricao="x", quantidade=0, preco_unit=100),
    dict(produto_id=None, descricao="x", quantidade=1, preco_unit=-1),
    dict(produto_id=None, descricao="  ", quantidade=1, preco_unit=100),
    dict(produto_id=999, descricao="x", quantidade=1, preco_unit=100),
])
def test_venda_invalida_nao_grava_nada(con, item):
    c = db.criar_cliente(con, "Hana")
    with pytest.raises(ErroDeCadastro):
        db.registrar_venda(con, c, "2026-11-01", [dict(produto_id=None, descricao="ok", quantidade=1, preco_unit=100), item])
    assert con.execute("SELECT COUNT(*) FROM vendas").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM itens_venda").fetchone()[0] == 0


def test_resumo_mes_ignora_cancelados_e_outros_meses(con):
    c = db.criar_cliente(con, "Iris")
    db.registrar_venda(con, c, "2026-11-01", [dict(produto_id=None, descricao="a", quantidade=1, preco_unit=1000)])
    v = db.registrar_venda(con, c, "2026-11-02", [dict(produto_id=None, descricao="b", quantidade=1, preco_unit=7000)])
    db.cancelar_venda(con, v)
    db.registrar_venda(con, c, "2026-10-31", [dict(produto_id=None, descricao="c", quantidade=1, preco_unit=5000)])
    db.registrar_pagamento(con, c, "2026-11-15", 400)
    assert db.resumo_mes(con, "2026-11") == dict(vendido=1000, recebido=400, descontos=0)


# ---------------------------------------------------------------- importação

def _planilha(tmp_path, linhas, cabecalho_linha=4):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "NOV"
    ws.cell(row=cabecalho_linha, column=1, value="CLIENTE")
    for col, nome in enumerate(["COMPRADO", "A RECEBER", "PAGO", "RESTANTE"], start=2):
        ws.cell(row=cabecalho_linha, column=col, value=nome)
    for i, linha in enumerate(linhas, start=cabecalho_linha + 1):
        for j, v in enumerate(linha, start=1):
            ws.cell(row=i, column=j, value=v)
    arq = tmp_path / "p.xlsx"
    wb.save(arq)
    return arq


def test_importacao_ok(tmp_path, con):
    arq = _planilha(tmp_path, [
        ["ANA  ", 0, 100, 0, 100.5], ["bia", 0, 0, 10, -10], ["Rute", 0, 0, 0, None], [None, None, None, None, None],
    ])
    clientes = importar_planilha.ler_aba(arq, "NOV")
    assert clientes == [("ANA", 10050), ("bia", -1000), ("Rute", 0)]
    assert importar_planilha.importar(con, clientes, "2026-11-01") == 9050
    with pytest.raises(SystemExit, match="já tem clientes"):
        importar_planilha.importar(con, clientes, "2026-11-01")


def test_importacao_falha_com_formula_sem_valor(tmp_path):
    # arquivo salvo pelo openpyxl não tem valor calculado da fórmula: tem que falhar, não importar zero
    arq = _planilha(tmp_path, [["ANA", 0, 100, 0, "=C5-D5"]])
    with pytest.raises(SystemExit, match="fórmula sem valor calculado"):
        importar_planilha.ler_aba(arq, "NOV")


def test_importacao_falha_saldo_sem_nome(tmp_path):
    arq = _planilha(tmp_path, [["ANA", 0, 1, 0, 1], [None, 0, 0, 0, 50]])
    with pytest.raises(SystemExit, match="sem nome"):
        importar_planilha.ler_aba(arq, "NOV")


# ---------------------------------------------------------------- site

@pytest.fixture
def cliente_http(tmp_path):
    app = criar_app(banco=str(tmp_path / "w.db"), senha="segredo", chave="chave-teste")
    app.config["TESTING"] = True
    return app.test_client(), str(tmp_path / "w.db")


def test_login_obrigatorio(cliente_http):
    http, _ = cliente_http
    r = http.get("/clientes")
    assert r.status_code == 302 and "/entrar" in r.headers["Location"]
    assert http.post("/entrar", data={"senha": "errada"}).status_code == 200
    assert http.get("/clientes").status_code == 302
    r = http.post("/entrar?proximo=/clientes", data={"senha": "segredo"})
    assert r.headers["Location"] == "/clientes"
    assert http.get("/clientes").status_code == 200


def test_login_nao_redireciona_para_fora(cliente_http):
    http, _ = cliente_http
    r = http.post("/entrar?proximo=//malicioso.com", data={"senha": "segredo"})
    assert r.headers["Location"] == "/"


def test_fluxo_completo_pelo_site(cliente_http):
    http, banco = cliente_http
    http.post("/entrar", data={"senha": "segredo"})
    r = http.post("/clientes/novo", data={"nome": "Joana Guarda-chuva", "telefone": "", "observacao": ""})
    assert r.status_code == 302
    cid = int(r.headers["Location"].rsplit("/", 1)[1])
    http.post("/produtos", data={"nome": "Creme", "preco": "45,90"})
    con = db.conectar(banco)
    pid = con.execute("SELECT id FROM produtos").fetchone()[0]

    itens = [dict(produto_id=pid, descricao="Creme", quantidade=2, preco="45,90"),
             dict(produto_id=None, descricao="Frete", quantidade=1, preco="10")]
    r = http.post(f"/clientes/{cid}/venda", data={"itens": json.dumps(itens), "data": "2026-11-03"},
                  follow_redirects=True)
    assert "R$ 101,80" in r.get_data(as_text=True)
    r = http.post(f"/clientes/{cid}/pagamento", data={"valor": "50", "data": "2026-11-04", "forma": "Pix"},
                  follow_redirects=True)
    pagina = r.get_data(as_text=True)
    assert "Saldo agora: R$ 51,80" in pagina
    assert db.cliente(con, cid)["saldo"] == 5180

    # todas as telas abrem
    for url in ["/", "/clientes", f"/clientes/{cid}", f"/clientes/{cid}/editar", f"/clientes/{cid}/venda",
                f"/clientes/{cid}/pagamento", "/produtos", f"/produtos/{pid}/editar", "/relatorios?mes=2026-11",
                "/exportar.xlsx", "/backup.db"]:
        assert http.get(url).status_code == 200, url
    con.close()


def test_venda_com_preco_invalido_mostra_erro_e_nao_grava(cliente_http):
    http, banco = cliente_http
    http.post("/entrar", data={"senha": "segredo"})
    cid = int(http.post("/clientes/novo", data={"nome": "Lia"}).headers["Location"].rsplit("/", 1)[1])
    itens = [dict(produto_id=None, descricao="x", quantidade=1, preco="abc")]
    r = http.post(f"/clientes/{cid}/venda", data={"itens": json.dumps(itens), "data": "2026-11-03"})
    assert r.status_code == 200 and "não é um valor válido" in r.get_data(as_text=True)
    con = db.conectar(banco)
    assert con.execute("SELECT COUNT(*) FROM vendas").fetchone()[0] == 0
    con.close()


def test_exportacao_excel_bate_com_saldo(cliente_http, tmp_path):
    http, banco = cliente_http
    http.post("/entrar", data={"senha": "segredo"})
    con = db.conectar(banco)
    c = db.criar_cliente(con, "Mia", saldo_inicial=12345)
    db.registrar_pagamento(con, c, "2026-11-01", 345)
    arq = tmp_path / "e.xlsx"
    arq.write_bytes(http.get("/exportar.xlsx").data)
    ws = openpyxl.load_workbook(arq)["Clientes"]
    assert ws["A2"].value == "Mia" and ws["F2"].value == pytest.approx(120.00)
    con.close()


# ---------------------------------------------------------------- estoque

def test_estoque_desconta_na_venda_e_volta_no_cancelamento(con):
    c = db.criar_cliente(con, "Nina")
    p = db.criar_produto(con, "Colônia", 3000, estoque=10)
    v = db.registrar_venda(con, c, "2026-11-05", [
        dict(produto_id=p, descricao="", quantidade=3, preco_unit=3000),
        dict(produto_id=p, descricao="", quantidade=1, preco_unit=2500),     # mesmo produto com desconto
        dict(produto_id=None, descricao="Avulso", quantidade=5, preco_unit=100),  # avulso não mexe no estoque
    ])
    assert db.produto(con, p)["estoque"] == 6
    db.cancelar_venda(con, v)
    assert db.produto(con, p)["estoque"] == 10


def test_vender_mais_que_o_estoque_fica_negativo(con):
    c = db.criar_cliente(con, "Olga")
    p = db.criar_produto(con, "Kit", 12000, estoque=1)
    db.registrar_venda(con, c, "2026-11-05", [dict(produto_id=p, descricao="", quantidade=3, preco_unit=12000)])
    assert db.produto(con, p)["estoque"] == -2


def test_entrada_e_correcao_de_estoque(con):
    p = db.criar_produto(con, "Hidratante", 2000)
    assert db.produto(con, p)["estoque"] == 0
    db.adicionar_estoque(con, p, 12)
    assert db.produto(con, p)["estoque"] == 12
    with pytest.raises(ErroDeCadastro):
        db.adicionar_estoque(con, p, 0)
    db.atualizar_produto(con, p, "Hidratante", 2000, True)          # sem estoque: mantém
    assert db.produto(con, p)["estoque"] == 12
    db.atualizar_produto(con, p, "Hidratante", 2000, True, estoque=7)
    assert db.produto(con, p)["estoque"] == 7


@pytest.mark.parametrize("texto, esperado", [("10", 10), (" 3 ", 3), ("", 0), (None, 0)])
def test_ler_quantidade(texto, esperado):
    assert db.ler_quantidade(texto) == esperado


@pytest.mark.parametrize("texto", ["1,5", "abc", "-2"])
def test_ler_quantidade_invalida(texto):
    with pytest.raises(ErroDeCadastro):
        db.ler_quantidade(texto)


def test_banco_da_primeira_versao_ganha_coluna_estoque(tmp_path):
    import sqlite3
    arq = str(tmp_path / "velho.db")
    velho = sqlite3.connect(arq)
    velho.executescript("""CREATE TABLE produtos (id INTEGER PRIMARY KEY, nome TEXT NOT NULL, nome_busca TEXT NOT NULL UNIQUE,
                           preco INTEGER NOT NULL, ativo INTEGER NOT NULL DEFAULT 1, criado_em TEXT);
                           INSERT INTO produtos (nome, nome_busca, preco) VALUES ('Antigo', 'antigo', 500);""")
    velho.commit(); velho.close()
    con = db.conectar(arq)
    p = con.execute("SELECT * FROM produtos").fetchone()
    assert (p["nome"], p["preco"], p["estoque"]) == ("Antigo", 500, 0)
    db.conectar(arq).close()   # rodar de novo não quebra
    con.close()


# ---------------------------------------------------------------- resumo do mês e extrato

def test_limites_mes_virada_de_ano():
    assert db.limites_mes("2026-12") == ("2026-12-01", "2027-01-01")
    assert db.limites_mes("2026-02") == ("2026-02-01", "2026-03-01")
    assert db.nome_mes("2026-03") == "março de 2026"


@pytest.fixture
def cliente_com_movimento(con):
    c = db.criar_cliente(con, "Paula", saldo_inicial=10000, saldo_inicial_data="2026-11-01")
    venda = lambda data, valor: db.registrar_venda(con, c, data, [dict(produto_id=None, descricao="x", quantidade=1, preco_unit=valor)])
    venda("2026-11-05", 5000)
    db.registrar_pagamento(con, c, "2026-11-20", 3000)
    db.cancelar_venda(con, venda("2026-11-21", 99900))          # cancelada: não conta
    venda("2026-12-02", 1000)
    return c


def test_resumo_clientes_mes_encadeia_os_meses(con, cliente_com_movimento):
    c = cliente_com_movimento
    out = db.resumo_clientes_mes(con, "2026-10", c)[0]
    assert (out["anterior"], out["comprou"], out["pagou"], out["final"]) == (0, 0, 0, 0)   # antes da importação
    nov = db.resumo_clientes_mes(con, "2026-11", c)[0]
    assert (nov["anterior"], nov["comprou"], nov["pagou"], nov["final"]) == (10000, 5000, 3000, 12000)
    dez = db.resumo_clientes_mes(con, "2026-12", c)[0]
    assert dez["anterior"] == nov["final"]
    assert dez["final"] == 13000 == db.cliente(con, c)["saldo"]


def test_extrato_mes(con, cliente_com_movimento):
    e = db.extrato_mes(con, cliente_com_movimento, "2026-11")
    assert e["anterior"] == 10000 and e["final"] == 12000
    assert [(l["tipo"], l["valor"], l["saldo_apos"]) for l in e["linhas"]] == [("venda", 5000, 15000), ("pagamento", 3000, 12000)]
    assert db.extrato_mes(con, cliente_com_movimento, "2027-01")["linhas"] == []


def test_paginas_de_impressao(cliente_http):
    http, banco = cliente_http
    http.post("/entrar", data={"senha": "segredo"})
    con = db.conectar(banco)
    a = db.criar_cliente(con, "Quitéria", saldo_inicial=5000, saldo_inicial_data="2026-11-01")
    db.criar_cliente(con, "Rita", saldo_inicial=2000, saldo_inicial_data="2026-11-01")
    db.registrar_pagamento(con, a, "2026-11-10", 1500)
    con.close()

    so_movimento = http.get("/relatorios/imprimir?mes=2026-11").get_data(as_text=True)
    assert "Resumo de novembro de 2026" in so_movimento
    assert "Quitéria" in so_movimento and "Rita" not in so_movimento     # Rita não mexeu no mês
    assert "R$ 55,00" in so_movimento                                     # a receber: 35 + 20

    todos = http.get("/relatorios/imprimir?mes=2026-11&mostrar=todos").get_data(as_text=True)
    assert "Rita" in todos and "Total (2 clientes)" in todos

    extrato = http.get(f"/clientes/{a}/imprimir?mes=2026-11").get_data(as_text=True)
    assert "Extrato de novembro de 2026" in extrato and "R$ 35,00" in extrato
    assert http.get(f"/clientes/{a}/imprimir?mes=lixo").status_code == 200   # mês inválido cai no mês atual
    assert http.get("/clientes/999/imprimir").status_code == 404


# ---------------------------------------------------------------- padrão dos nomes

@pytest.mark.parametrize("antes, depois", [
    ("MARTA JARDIM", "Marta Jardim"), ("heloisa campos", "Heloisa Campos"), ("  OTÁVIO   ribeira ", "Otávio Ribeira"),
    ("regina filha DE ze", "Regina Filha de Ze"), ("NEUSA FEIRA DO BAIRRO", "Neusa Feira do Bairro"),
    ("TERESA GUARDA-CHUVA", "Teresa Guarda-chuva"), ("lia (tia lia)", "Lia (Tia Lia)"), ("De souza", "De Souza"),
    ("ana e maria", "Ana e Maria"), ("SALA 2", "Sala 2"), ("Élida", "Élida"),
])
def test_formatar_nome(antes, depois):
    assert db.formatar_nome(antes) == depois


def test_cliente_novo_e_editado_ficam_no_padrao(con):
    c = db.criar_cliente(con, "JOSEFA DA SILVA")
    assert db.cliente(con, c)["nome"] == "Josefa da Silva"
    db.atualizar_cliente(con, c, "josefa DA silva PEREIRA", "", "", 0, True)
    assert db.cliente(con, c)["nome"] == "Josefa da Silva Pereira"


def test_padronizar_nomes_existentes(con):
    with con:   # simula clientes importados antes do padrão
        con.execute("INSERT INTO clientes (nome, nome_busca, saldo_inicial) VALUES ('MARIA BDI', 'maria bdi', 500)")
        con.execute("INSERT INTO clientes (nome, nome_busca) VALUES ('Joana Silva', 'joana silva')")
    assert db.padronizar_nomes_clientes(con) == [("MARIA BDI", "Maria Bdi")]
    assert [c["nome"] for c in db.saldos(con)] == ["Joana Silva", "Maria Bdi"]
    assert db.saldos(con)[1]["saldo"] == 500
    assert db.padronizar_nomes_clientes(con) == []   # rodar de novo não muda nada


# ---------------------------------------------------------------- desconto

def test_venda_com_desconto_guarda_preco_normal(con):
    c = db.criar_cliente(con, "Sara")
    perfume = db.criar_produto(con, "Perfume", 20000, estoque=5)
    db.registrar_venda(con, c, "2026-11-10", [
        dict(produto_id=perfume, descricao="", quantidade=2, preco_unit=17000),   # 200 -> 170, dois
        dict(produto_id=None, descricao="Embrulho", quantidade=1, preco_unit=500),
    ])
    assert db.cliente(con, c)["saldo"] == 34500                           # cobra o valor com desconto
    itens = {i["descricao"]: i for i in db.extrato(con, c)[0]["itens"]}
    assert (itens["Perfume"]["preco_tabela"], itens["Perfume"]["preco_unit"]) == (20000, 17000)
    assert itens["Embrulho"]["preco_tabela"] is None
    assert db.resumo_mes(con, "2026-11")["descontos"] == 6000             # 2 x 30


def test_preco_normal_vem_do_cadastro_nao_da_tela(con):
    c = db.criar_cliente(con, "Tina")
    p = db.criar_produto(con, "Batom", 3000)
    db.registrar_venda(con, c, "2026-11-10", [dict(produto_id=p, descricao="", quantidade=1, preco_unit=3500,
                                                   preco_tabela=1)])  # a tela não decide o preço normal
    item = db.extrato(con, c)[0]["itens"][0]
    assert item["preco_tabela"] == 3000
    assert db.resumo_mes(con, "2026-11")["descontos"] == 0                # vender acima não conta como desconto


def test_banco_antigo_ganha_coluna_preco_tabela(tmp_path):
    import sqlite3
    arq = str(tmp_path / "v.db")
    velho = sqlite3.connect(arq)
    velho.executescript("""CREATE TABLE itens_venda (id INTEGER PRIMARY KEY, venda_id INTEGER NOT NULL, produto_id INTEGER,
                           descricao TEXT NOT NULL, quantidade INTEGER NOT NULL, preco_unit INTEGER NOT NULL);
                           INSERT INTO itens_venda (venda_id, descricao, quantidade, preco_unit) VALUES (1, 'x', 1, 100);""")
    velho.commit(); velho.close()
    con = db.conectar(arq)
    assert con.execute("SELECT preco_tabela FROM itens_venda").fetchone()[0] is None
    con.close()


def test_desconto_aparece_na_tela_impressao_e_excel(cliente_http, tmp_path):
    http, banco = cliente_http
    http.post("/entrar", data={"senha": "segredo"})
    con = db.conectar(banco)
    c = db.criar_cliente(con, "Ursula")
    p = db.criar_produto(con, "Perfume", 20000)
    con.close()
    r = http.post(f"/clientes/{c}/venda", data={"itens": json.dumps([dict(produto_id=p, descricao="Perfume", quantidade=1, preco="170")]),
                                                "data": "2026-11-10"}, follow_redirects=True)
    pagina = r.get_data(as_text=True)
    assert "Venda de R$ 170,00 registrada para Ursula (com desconto de R$ 30,00)" in pagina
    assert "de R$ 200,00, desconto de R$ 30,00" in pagina
    assert "de R$ 200,00, desconto de R$ 30,00" in http.get(f"/clientes/{c}/imprimir?mes=2026-11").get_data(as_text=True)
    assert "R$ 30,00" in http.get("/relatorios?mes=2026-11").get_data(as_text=True)
    arq = tmp_path / "e.xlsx"
    arq.write_bytes(http.get("/exportar.xlsx").data)
    ws = openpyxl.load_workbook(arq)["Vendas"]
    assert [c.value for c in ws[2]][5:9] == [200, 170, 30, 170]
