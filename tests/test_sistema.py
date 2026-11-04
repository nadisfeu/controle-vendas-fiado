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
    # CSRF desligado só aqui: estes testes enviam formulário direto; os do navegador usam o código de verdade
    app = criar_app(banco=str(tmp_path / "w.db"), senha="segredo", chave="chave-teste", csrf=False)
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
    ("ana e maria", "Ana e Maria"), ("FEITO EM CASA", "Feito em Casa"), ("Zé NA roça", "Zé na Roça"), ("SALA 2", "Sala 2"), ("Élida", "Élida"),
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


# ---------------------------------------------------------------- gráficos

import graficos  # noqa: E402
from datetime import date  # noqa: E402


def _venda(con, cliente, data, valor):
    return db.registrar_venda(con, cliente, data, [dict(produto_id=None, descricao="x", quantidade=1, preco_unit=valor)])


def test_grafico_mes_melhor_pior_e_futuro(con):
    c = db.criar_cliente(con, "Vera")
    _venda(con, c, "2026-11-02", 1000)
    _venda(con, c, "2026-11-02", 500)          # duas vendas no mesmo dia somam
    _venda(con, c, "2026-11-03", 9000)
    db.cancelar_venda(con, _venda(con, c, "2026-11-01", 99999))   # cancelada não conta
    _venda(con, c, "2026-10-31", 77777)        # outro mês não conta
    g = graficos.grafico_mes(con, "2026-11", hoje=date(2026, 11, 3))
    assert len(g["dias"]) == 30
    assert g["dias"][1] == dict(dia=2, semana="seg", total=1500, vendas=2, futuro=False)
    assert (g["melhor"]["dia"], g["melhor"]["total"]) == (3, 9000)
    assert (g["pior"]["dia"], g["pior"]["total"]) == (2, 1500)
    assert g["sem_venda"] == 1                 # só o dia 1; do dia 4 em diante é futuro, não "sem venda"
    assert g["dias"][3]["futuro"] and not g["dias"][2]["futuro"]


def test_grafico_mes_vazio_e_um_dia_so(con):
    assert graficos.grafico_mes(con, "2026-02", hoje=date(2026, 11, 3))["melhor"] is None
    c = db.criar_cliente(con, "Wanda")
    _venda(con, c, "2026-02-14", 100)
    g = graficos.grafico_mes(con, "2026-02", hoje=date(2026, 11, 3))
    assert len(g["dias"]) == 28 and g["melhor"]["dia"] == 14 and g["pior"] is None   # um dia só: sem "pior"


@pytest.mark.parametrize("total, esperado", [(0, 0), (1, 1), (2000, 1), (2001, 2), (9999, 5), (10000, 5)])
def test_nivel_do_mapa(total, esperado):
    assert graficos.nivel(total, 10000) == esperado


def test_mapa_ano_melhor_dia_de_cada_mes(con):
    c = db.criar_cliente(con, "Yara")
    _venda(con, c, "2026-12-12", 50000)        # "em dezembro vendeu mais no dia 12"
    _venda(con, c, "2026-12-03", 1000)
    _venda(con, c, "2026-02-28", 3000)
    _venda(con, c, "2027-01-01", 99999)        # outro ano não conta
    m = graficos.mapa_ano(con, 2026, hoje=date(2026, 12, 31))
    dez, fev = m["meses"][11], m["meses"][1]
    assert (dez["melhor"]["dia"], dez["total"]) == (12, 51000)
    assert dez["celulas"][11]["nivel"] == 5 and dez["celulas"][2]["nivel"] == 1 and dez["celulas"][0]["nivel"] == 0
    assert fev["celulas"][28:] == [None, None, None]      # 29, 30 e 31 de fevereiro não existem
    assert m["meses"][0]["melhor"] is None and m["maximo"] == 50000
    # as faixas da legenda batem com a regra de nível
    for k, (de, ate) in enumerate(m["faixas"], start=1):
        assert graficos.nivel(de, m["maximo"]) == k and graficos.nivel(ate, m["maximo"]) == k
    assert m["faixas"][-1][1] == m["maximo"]


def test_pagamento_recusa_valor_que_nao_e_centavos(con):
    c = db.criar_cliente(con, "Zilda")
    with pytest.raises(TypeError):
        db.registrar_pagamento(con, c, "2026-11-01", 12.5)


def test_relatorios_mostra_graficos(cliente_http):
    http, banco = cliente_http
    http.post("/entrar", data={"senha": "segredo"})
    con = db.conectar(banco)
    c = db.criar_cliente(con, "Alice")
    _venda(con, c, "2026-11-02", 45000)
    con.close()
    pagina = http.get("/relatorios?mes=2026-11").get_data(as_text=True)
    assert "Dia que mais vendeu: <b>dia 2 (seg), R$ 450,00</b>" in pagina
    assert 'id="dados-grafico-mes"' in pagina and "Melhor dia de cada mês · 2026" in pagina
    assert 'data-dica="2 de novembro (seg): R$ 450,00 · 1 venda"' in pagina
    vazio = http.get("/relatorios?mes=2025-03").get_data(as_text=True)
    assert "Nenhuma venda em março de 2025" in vazio and "Nenhuma venda em 2025" in vazio


# ---------------------------------------------------------------- sem pagar, desativados, relatório do produto

def test_clientes_sem_pagar(con):
    hoje = date(2026, 11, 3)
    pagou_no_sistema = db.criar_cliente(con, "Ana Sistema", saldo_inicial=5000, saldo_inicial_data="2026-01-01")
    db.registrar_pagamento(con, pagou_no_sistema, "2026-08-01", 1000)          # 94 dias
    so_planilha = db.criar_cliente(con, "Bia Planilha", saldo_inicial=3000, saldo_inicial_data="2026-11-01")
    with con:
        con.execute("UPDATE clientes SET ultimo_pagamento_planilha = '2026-07' WHERE id = ?", (so_planilha,))  # 31/07: 95 dias
    nunca = db.criar_cliente(con, "Cris Nunca", saldo_inicial=2000, saldo_inicial_data="2026-11-01")             # 2 dias
    planilha_e_sistema = db.criar_cliente(con, "Dani Ambos", saldo_inicial=9000, saldo_inicial_data="2026-01-01")
    with con:
        con.execute("UPDATE clientes SET ultimo_pagamento_planilha = '2025-12' WHERE id = ?", (planilha_e_sistema,))
    db.registrar_pagamento(con, planilha_e_sistema, "2026-10-20", 100)         # o mais recente vale: 14 dias
    credito = db.criar_cliente(con, "Eva Credito", saldo_inicial=-500, saldo_inicial_data="2025-01-01")
    desativada = db.criar_cliente(con, "Fia Desativada", saldo_inicial=700, saldo_inicial_data="2025-11-03")
    db.atualizar_cliente(con, desativada, "Fia Desativada", "", "", 700, False)

    lista = db.clientes_sem_pagar(con, hoje, 60)
    assert [(c["nome"], c["dias"], c["origem"]) for c in lista] == [
        ("Fia Desativada", 365, "nenhum"), ("Bia Planilha", 95, "planilha"), ("Ana Sistema", 94, "sistema")]
    assert not lista[0]["ativo"]                                              # desativado continua sendo cobrado
    nomes_0 = {c["nome"] for c in db.clientes_sem_pagar(con, hoje, 0)}
    assert "Eva Credito" not in nomes_0 and {"Cris Nunca", "Dani Ambos"} <= nomes_0   # crédito não é dívida


def _planilha_historico(tmp_path):
    wb = openpyxl.Workbook()
    antiga = wb.active
    antiga.title = "MARÇO"                                   # formato antigo: ano em A1, PAGO na coluna G
    antiga["A1"] = "Planilha  MARÇO 2023  "
    for col, rot in zip("ACEGI", ["CLIENTE ", "COMPRADO", " A RECEBER ", "PAGO", "RESTANTE"]):
        antiga[f"{col}2"] = rot
    antiga.append(["ANA SILVA", None, 100, None, None, None, 50])
    antiga.append(["Bia Souza", None, 80, None, None, None, None])
    nova = wb.create_sheet("OUTUBRO25")                      # formato novo: PAGO na coluna D, cabeçalho na linha 3
    for col, rot in zip("ABCDE", ["CLIENTE", "COMPRADO", "A RECEBER", "PAGO", "RESTANTE"]):
        nova[f"{col}3"] = rot
    nova.append(["ana  silva", 0, 50, 0, 50])                # não pagou em out/25
    nova.append(["BIA SOUZA", 0, 80, 30, 50])
    wb.create_sheet("Página9")                                # vazia: ignorada
    arq = tmp_path / "hist.xlsx"
    wb.save(arq)
    return arq


def test_ler_ultimos_pagamentos_dos_dois_formatos(tmp_path):
    import importar_ultimos_pagamentos as iup
    ultimo, ignoradas = iup.ultimos_pagamentos(_planilha_historico(tmp_path))
    assert ultimo == {"ana silva": "2023-03", "bia souza": "2025-10"} and ignoradas == ["Página9"]
    assert iup.mes_da_aba("NOVEMBRO2026", None) == (2026, 11) and iup.mes_da_aba("SETEMBRO25.2", None) == (2025, 9)
    assert iup.mes_da_aba("MARCO26", None) == (2026, 3) and iup.mes_da_aba("FEVEREIRO24", None) == (2024, 2)


def test_ler_ultimos_pagamentos_falha_alto(tmp_path):
    import importar_ultimos_pagamentos as iup
    wb = openpyxl.Workbook()
    wb.active.title = "RESUMO"
    wb.active["A1"] = "qualquer coisa"
    arq = tmp_path / "ruim.xlsx"
    wb.save(arq)
    with pytest.raises(SystemExit, match="não sei de que mês"):
        iup.ultimos_pagamentos(arq)
    wb.active.title = "ABRIL"                                  # mês sem ano em lugar nenhum
    wb.save(arq)
    with pytest.raises(SystemExit, match="não diz o ano"):
        iup.ultimos_pagamentos(arq)


def test_relatorio_produto(con):
    a, b = db.criar_cliente(con, "Gil"), db.criar_cliente(con, "Hugo")
    p = db.criar_produto(con, "Perfume", 20000, estoque=10)
    item = lambda q, preco: [dict(produto_id=p, descricao="", quantidade=q, preco_unit=preco)]
    db.registrar_venda(con, a, "2026-03-10", item(2, 17000))
    db.registrar_venda(con, b, "2026-03-20", item(1, 20000))
    db.registrar_venda(con, b, "2026-07-01", item(1, 20000))
    db.cancelar_venda(con, db.registrar_venda(con, a, "2026-07-02", item(5, 20000)))
    db.registrar_venda(con, a, "2025-12-31", item(1, 20000))                  # outro ano
    r = db.relatorio_produto(con, p, 2026)
    assert (r["meses"][2]["quantidade"], r["meses"][2]["total"]) == (3, 54000)
    assert (r["meses"][6]["quantidade"], r["quantidade"], r["total"], r["maior_mes"]) == (1, 4, 74000, 54000)
    assert [(c["nome"], c["quantidade"]) for c in r["clientes"]] == [("Hugo", 2), ("Gil", 2)]   # empate: quem gastou mais
    assert r["descontos"] == 6000
    assert len(r["ultimas"]) == 4 and r["ultimas"][0]["data"] == "2026-07-01"   # de sempre, sem a cancelada


def test_relatorio_mensal_agrupa_produto_pelo_cadastro(con):
    c = db.criar_cliente(con, "Ivo")
    p = db.criar_produto(con, "Batom", 3000)
    db.registrar_venda(con, c, "2026-11-01", [dict(produto_id=p, descricao="", quantidade=1, preco_unit=3000)])
    db.atualizar_produto(con, p, "Batom Matte", 3000, True)                     # mudou de nome depois
    db.registrar_venda(con, c, "2026-11-02", [dict(produto_id=p, descricao="", quantidade=2, preco_unit=3000),
                                              dict(produto_id=None, descricao="Embrulho", quantidade=1, preco_unit=500)])
    linhas = [(l["produto_id"], l["descricao"], l["quantidade"]) for l in db.vendas_por_produto_mes(con, "2026-11")]
    assert linhas == [(p, "Batom Matte", 3), (None, "Embrulho", 1)]


def test_telas_novas_pelo_site(cliente_http):
    http, banco = cliente_http
    http.post("/entrar", data={"senha": "segredo"})
    con = db.conectar(banco)
    ativa = db.criar_cliente(con, "Joana Ativa", saldo_inicial=1000, saldo_inicial_data="2026-01-01")
    parada = db.criar_cliente(con, "Lara Parada", saldo_inicial=4000, saldo_inicial_data="2025-01-01")
    db.atualizar_cliente(con, parada, "Lara Parada", "", "", 4000, False)
    p = db.criar_produto(con, "Colônia", 5000)
    con.close()
    inicio = http.get("/").get_data(as_text=True)
    assert "R$ 50,00" in inicio                       # total a receber inclui a desativada (10 + 40)
    assert "sem pagar há mais de 60 dias" in inicio
    so_desativados = http.get("/clientes?mostrar=desativados").get_data(as_text=True)
    assert "Lara Parada" in so_desativados and "Joana Ativa" not in so_desativados
    ativos = http.get("/clientes").get_data(as_text=True)
    assert "Joana Ativa" in ativos and "Lara Parada" not in ativos
    assert "Lara Parada" in http.get("/clientes?mostrar=todos").get_data(as_text=True)
    sem_pagar = http.get("/clientes/sem-pagar?dias=180").get_data(as_text=True)
    assert "Lara Parada" in sem_pagar and "nenhum pagamento registrado" in sem_pagar
    assert http.get("/clientes/sem-pagar?dias=7").status_code == 200           # opção inválida cai no padrão
    produto = http.get(f"/produtos/{p}").get_data(as_text=True)
    assert "Colônia" in produto and "Este produto ainda não foi vendido" in produto
    assert http.get("/produtos/999").status_code == 404


def test_sem_pagar_mostra_data_do_ultimo_dia_do_mes_da_planilha(cliente_http):
    http, banco = cliente_http
    http.post("/entrar", data={"senha": "segredo"})
    con = db.conectar(banco)
    for nome, mes in [("Nair Fevereiro", "2024-02"), ("Olga Setembro", "2025-09"), ("Pia Dezembro", "2025-12")]:
        c = db.criar_cliente(con, nome, saldo_inicial=1000, saldo_inicial_data="2026-11-01")
        with con:
            con.execute("UPDATE clientes SET ultimo_pagamento_planilha = ? WHERE id = ?", (mes, c))
    con.close()
    pagina = http.get("/clientes/sem-pagar?dias=30").get_data(as_text=True)
    for data in ("29/02/2024", "30/09/2025", "31/12/2025"):          # ano bissexto, mês de 30 e virada de ano
        assert f'último pagamento em {data} <span class="origem">(planilha)</span>' in pagina
    assert "último pagamento na planilha:" not in pagina


# ---------------------------------------------------------------- código, tipo e marca do produto

def test_codigo_tipo_e_marca(con):
    p = db.criar_produto(con, "Essencial 100 ml", 20000, codigo=" 73 455 ", marca="NATURA", tipo="perfume")
    x = db.produto(con, p)
    assert (x["codigo"], x["marca"], x["tipo"]) == ("73455", "Natura", "Perfume")       # padronizado
    db.criar_produto(con, "Sem código 1", 100)
    db.criar_produto(con, "Sem código 2", 100, codigo="  ")                           # vários sem código: ok
    with pytest.raises(ErroDeCadastro, match='já é do produto "Essencial 100 ml"'):
        db.criar_produto(con, "Outro", 100, codigo="73455")
    q = db.criar_produto(con, "Batom", 100, codigo="ab-1", marca="o boticário")
    with pytest.raises(ErroDeCadastro):
        db.criar_produto(con, "Batom 2", 100, codigo="AB-1")                          # sem diferença de maiúscula
    assert db.marcas(con) == ["Natura", "O Boticário"] and db.tipos(con) == ["Perfume"]


def test_editar_mantem_ou_apaga_codigo_tipo_marca(con):
    p = db.criar_produto(con, "Creme", 100, codigo="C1", marca="Natura", tipo="Hidratante")
    db.atualizar_produto(con, p, "Creme", 150, True)                                # sem passar: mantém
    x = db.produto(con, p)
    assert (x["codigo"], x["marca"], x["tipo"], x["preco"]) == ("C1", "Natura", "Hidratante", 150)
    db.atualizar_produto(con, p, "Creme", 150, True, codigo="c1", marca="", tipo="")   # o próprio código: ok
    x = db.produto(con, p)
    assert (x["codigo"], x["marca"], x["tipo"]) == ("c1", "", "")
    outro = db.criar_produto(con, "Loção", 100, codigo="L1")
    with pytest.raises(ErroDeCadastro):
        db.atualizar_produto(con, outro, "Loção", 100, True, codigo="C1")


def test_banco_antigo_ganha_codigo_tipo_marca(tmp_path):
    import sqlite3
    arq = str(tmp_path / "v.db")
    velho = sqlite3.connect(arq)
    velho.executescript("""CREATE TABLE produtos (id INTEGER PRIMARY KEY, nome TEXT NOT NULL, nome_busca TEXT NOT NULL UNIQUE,
                           preco INTEGER NOT NULL, estoque INTEGER NOT NULL DEFAULT 0, ativo INTEGER NOT NULL DEFAULT 1, criado_em TEXT);
                           INSERT INTO produtos (nome, nome_busca, preco) VALUES ('Antigo', 'antigo', 500);""")
    velho.commit(); velho.close()
    con = db.conectar(arq)
    p = con.execute("SELECT * FROM produtos").fetchone()
    assert (p["codigo"], p["marca"], p["tipo"]) == (None, "", "")
    con.close()


def test_cadastro_de_produto_pelo_site_e_excel(cliente_http, tmp_path):
    http, banco = cliente_http
    http.post("/entrar", data={"senha": "segredo"})
    http.post("/produtos", data={"tipo": "perfume", "marca": "natura", "nome": "Kaiak", "codigo": "K9", "preco": "150", "estoque": "2"})
    r = http.post("/produtos", data={"nome": "Kaiak 2", "codigo": "k9", "preco": "1"}, follow_redirects=True)
    assert 'O código &#34;k9&#34; já é do produto &#34;Kaiak&#34;' in r.get_data(as_text=True) or \
           'O código "k9" já é do produto "Kaiak"' in r.get_data(as_text=True)
    lista = http.get("/produtos").get_data(as_text=True)
    assert "Perfume · Natura · cód. K9" in lista
    arq = tmp_path / "e.xlsx"
    arq.write_bytes(http.get("/exportar.xlsx").data)
    ws = openpyxl.load_workbook(arq)["Produtos"]
    assert [c.value for c in ws[1]][:4] == ["Produto", "Tipo", "Marca", "Código"]
    assert [c.value for c in ws[2]][:4] == ["Kaiak", "Perfume", "Natura", "K9"]



# ---------------------------------------------------------------- segurança e backup (preparação para a internet)

def _app(tmp_path, **kw):
    banco = str(tmp_path / "s.db")
    db.conectar(banco).close()
    app = criar_app(banco=banco, senha="senha-certa-123", chave="k" * 32, **kw)
    app.config["TESTING"] = True
    return app, banco


def _csrf_da_pagina(http, url):
    import re
    return re.search(r'name="csrf" value="([^"]+)"', http.get(url).get_data(as_text=True)).group(1)


def test_csrf_recusa_formulario_sem_codigo(tmp_path):
    app, banco = _app(tmp_path)
    http = app.test_client()
    assert http.post("/entrar", data={"senha": "senha-certa-123"}).status_code == 400        # sem código
    token = _csrf_da_pagina(http, "/entrar")
    assert http.post("/entrar", data={"senha": "senha-certa-123", "csrf": token}).status_code == 302
    r = http.post("/clientes/novo", data={"nome": "Intrusa", "csrf": "codigo-falso"})
    assert r.status_code == 400 and "Formulário expirado" in r.get_data(as_text=True)
    token = _csrf_da_pagina(http, "/clientes/novo")
    assert http.post("/clientes/novo", data={"nome": "Legítima", "csrf": token}).status_code == 302
    con = db.conectar(banco)
    assert [c["nome"] for c in db.saldos(con)] == ["Legítima"]
    con.close()


def test_bloqueio_depois_de_varias_senhas_erradas(tmp_path):
    app, _ = _app(tmp_path, csrf=False)
    http = app.test_client()
    for _ in range(5):
        assert "Senha incorreta" in http.post("/entrar", data={"senha": "chute"}).get_data(as_text=True)
    r = http.post("/entrar", data={"senha": "senha-certa-123"})            # até a certa fica bloqueada
    assert r.status_code == 429 and "Muitas tentativas" in r.get_data(as_text=True)
    outro = app.test_client()                                               # outro endereço não é afetado
    r = outro.post("/entrar", data={"senha": "senha-certa-123"}, environ_base={"REMOTE_ADDR": "10.0.0.9"})
    assert r.status_code == 302


def test_acertar_a_senha_zera_os_erros(tmp_path):
    app, _ = _app(tmp_path, csrf=False)
    http = app.test_client()
    for _ in range(4):
        http.post("/entrar", data={"senha": "chute"})
    assert http.post("/entrar", data={"senha": "senha-certa-123"}).status_code == 302
    for _ in range(4):
        http.post("/entrar", data={"senha": "chute"})
    assert http.post("/entrar", data={"senha": "senha-certa-123"}).status_code == 302


def test_cabecalhos_de_seguranca_e_cookie_seguro(tmp_path):
    app, _ = _app(tmp_path, csrf=False, cookie_seguro=True)
    http = app.test_client()
    r = http.post("/entrar", data={"senha": "senha-certa-123"}, base_url="https://localhost")
    assert "Secure" in r.headers["Set-Cookie"] and "HttpOnly" in r.headers["Set-Cookie"]
    for h, v in [("X-Frame-Options", "DENY"), ("X-Content-Type-Options", "nosniff"), ("Cache-Control", "no-store")]:
        assert r.headers[h] == v
    assert "max-age" in r.headers["Strict-Transport-Security"]


def test_aviso_de_backup_no_inicio(tmp_path):
    app, banco = _app(tmp_path, csrf=False)
    http = app.test_client()
    http.post("/entrar", data={"senha": "senha-certa-123"})
    assert "Nenhuma cópia de segurança baixada ainda" in http.get("/").get_data(as_text=True)
    assert http.get("/backup.db").status_code == 200
    assert "cópia de segurança" not in http.get("/").get_data(as_text=True)
    con = db.conectar(banco)
    db.gravar_meta(con, "ultimo_backup_baixado", "2020-01-01T10:00:00")
    con.close()
    assert "Última cópia de segurança há" in http.get("/").get_data(as_text=True)
    http.get("/exportar.xlsx")                                              # baixar o Excel também conta
    assert "cópia de segurança" not in http.get("/").get_data(as_text=True)


def test_copia_automatica_diaria_guarda_as_ultimas_14(tmp_path):
    import sqlite3
    pasta = tmp_path / "copias"
    pasta.mkdir()
    for i in range(1, 17):                                                   # 16 cópias velhas
        (pasta / f"copia_2026-01-{i:02d}.db").write_bytes(b"velha")
    app, banco = _app(tmp_path, csrf=False, pasta_copias=str(pasta))
    con = db.conectar(banco)
    db.criar_cliente(con, "Cliente da Cópia")
    con.close()
    app.test_client().get("/entrar")
    copias = sorted(p.name for p in pasta.glob("copia_*.db"))
    assert len(copias) == 14 and copias[-1] == f"copia_{date.today().isoformat()}.db"
    assert "copia_2026-01-01.db" not in copias                               # as mais velhas saíram
    hoje = sqlite3.connect(pasta / copias[-1])
    assert hoje.execute("SELECT nome FROM clientes").fetchone()[0] == "Cliente da Cópia"   # cópia real e completa
    hoje.close()
    app.test_client().get("/entrar")                                         # segunda visita do dia: não repete
    assert len(list(pasta.glob("copia_*.db"))) == 14


def test_copia_automatica_que_falha_avisa_mas_nao_derruba(tmp_path):
    arquivo_no_lugar_da_pasta = tmp_path / "nao_e_pasta"
    arquivo_no_lugar_da_pasta.write_text("x")
    app, _ = _app(tmp_path, csrf=False, pasta_copias=str(arquivo_no_lugar_da_pasta))
    http = app.test_client()
    assert http.post("/entrar", data={"senha": "senha-certa-123"}).status_code == 302
    assert "A cópia automática do dia falhou" in http.get("/").get_data(as_text=True)


def _importar_wsgi_com_env_valido(tmp_path, monkeypatch):
    """wsgi.py monta o site ao ser importado (é o que a hospedagem faz): precisa de um .env válido antes."""
    import importlib, sys as _sys
    monkeypatch.setenv("APP_SENHA", "abacate-bolo-janela-vento-42")
    monkeypatch.setenv("APP_CHAVE", "c" * 64)
    db.conectar(str(tmp_path / "ok.db")).close()
    monkeypatch.setenv("APP_BANCO", str(tmp_path / "ok.db"))
    monkeypatch.setenv("APP_PASTA_COPIAS", str(tmp_path / "copias"))
    _sys.modules.pop("wsgi", None)
    return importlib.import_module("wsgi")


def test_wsgi_falha_alto_sem_banco_ou_com_senha_fraca(tmp_path, monkeypatch):
    wsgi = _importar_wsgi_com_env_valido(tmp_path, monkeypatch)
    monkeypatch.setattr(wsgi, "PASTA", tmp_path)                             # sem .env nesta pasta
    for k in ("APP_SENHA", "APP_CHAVE", "APP_BANCO"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(RuntimeError, match="faltam no .env"):
        wsgi.montar()
    monkeypatch.setenv("APP_SENHA", "curta")
    monkeypatch.setenv("APP_CHAVE", "c" * 64)
    with pytest.raises(RuntimeError, match="muito curta"):
        wsgi.montar()
    monkeypatch.setenv("APP_SENHA", "abacate-bolo-janela-vento-42")
    monkeypatch.setenv("APP_BANCO", str(tmp_path / "nao_existe.db"))
    with pytest.raises(RuntimeError, match="banco não encontrado"):
        wsgi.montar()
    assert not (tmp_path / "nao_existe.db").exists()                         # não criou banco vazio
    db.conectar(str(tmp_path / "ok.db")).close()
    monkeypatch.setenv("APP_BANCO", str(tmp_path / "ok.db"))
    monkeypatch.setenv("APP_PASTA_COPIAS", str(tmp_path / "copias"))
    app = wsgi.montar()
    assert app.config["SESSION_COOKIE_SECURE"] and app.config["CSRF"]
    import time as _t
    assert _t.strftime("%Z") in ("-03", "BRT", "-02", "BRST")                 # fuso de São Paulo


def test_wsgi_importa_com_env_valido(tmp_path, monkeypatch):
    """O PythonAnywhere faz 'from wsgi import application': isso tem que funcionar com o .env certo."""
    import importlib, sys as _sys
    monkeypatch.setenv("APP_SENHA", "abacate-bolo-janela-vento-42")
    monkeypatch.setenv("APP_CHAVE", "c" * 64)
    db.conectar(str(tmp_path / "ok.db")).close()
    monkeypatch.setenv("APP_BANCO", str(tmp_path / "ok.db"))
    monkeypatch.setenv("APP_PASTA_COPIAS", str(tmp_path / "copias"))
    _sys.modules.pop("wsgi", None)
    modulo = importlib.import_module("wsgi")
    http = modulo.application.test_client()
    assert http.get("/entrar").status_code == 200 and http.get("/").status_code == 302



def test_trocar_a_senha_derruba_logins_antigos(tmp_path):
    """Se a senha vazar e for trocada, quem entrou com a antiga tem que sair na hora."""
    banco = str(tmp_path / "t.db")
    db.conectar(banco).close()
    antes = criar_app(banco=banco, senha="senha-antiga-123", chave="k" * 32, csrf=False)
    http = antes.test_client()
    assert http.post("/entrar", data={"senha": "senha-antiga-123"}).status_code == 302
    assert http.get("/clientes").status_code == 200
    cookie = http.get_cookie("session").value
    depois = criar_app(banco=banco, senha="senha-nova-4567", chave="k" * 32, csrf=False)   # mesma chave, senha nova
    http2 = depois.test_client()
    http2.set_cookie("session", cookie)
    r = http2.get("/clientes")
    assert r.status_code == 302 and "/entrar" in r.headers["Location"]
    assert http2.post("/entrar", data={"senha": "senha-antiga-123"}).status_code == 200      # a antiga não entra
    assert http2.post("/entrar", data={"senha": "senha-nova-4567"}).status_code == 302


def test_login_antigo_sem_marca_da_senha_e_recusado(tmp_path):
    """Cookies de antes desta proteção (só com logado=True) não valem mais."""
    banco = str(tmp_path / "t.db")
    db.conectar(banco).close()
    app = criar_app(banco=banco, senha="senha-qualquer-123", chave="k" * 32, csrf=False)
    http = app.test_client()
    with http.session_transaction() as s:
        s["logado"] = True
    assert http.get("/clientes").status_code == 302



def test_login_nao_fica_guardado_ao_fechar_o_navegador(tmp_path):
    app, _ = _app(tmp_path, csrf=False, cookie_seguro=True)
    r = app.test_client().post("/entrar", data={"senha": "senha-certa-123"}, base_url="https://localhost")
    cookie = r.headers["Set-Cookie"]
    assert "Expires" not in cookie and "Max-Age" not in cookie      # cookie de sessão: some ao fechar


def test_sai_sozinho_depois_de_muito_tempo_sem_uso(tmp_path):
    app, _ = _app(tmp_path, csrf=False, minutos_inativo=60)
    http = app.test_client()
    http.post("/entrar", data={"senha": "senha-certa-123"})
    assert http.get("/clientes").status_code == 200
    with http.session_transaction() as s:
        s["ultimo_uso"] -= 59 * 60                                     # 59 min parado: ainda vale
    assert http.get("/clientes").status_code == 200                   # e o uso renova o prazo
    with http.session_transaction() as s:
        s["ultimo_uso"] -= 61 * 60                                     # 61 min parado: sai
    r = http.get("/clientes", follow_redirects=True)
    assert "entre de novo" in r.get_data(as_text=True)
    assert http.get("/clientes").status_code == 302
