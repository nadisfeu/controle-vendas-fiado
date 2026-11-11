"""Testes no navegador de verdade (Chromium via Playwright): cliques, JavaScript e layout no celular.

Rodar:  python -m pytest -q tests/test_navegador.py
Fotos das telas (opcional):  FOTOS=pasta python -m pytest -q tests/test_navegador.py
"""
import os
import sys
import threading
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
from werkzeug.serving import make_server  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import db  # noqa: E402
from app import criar_app  # noqa: E402

SENHA = "senha-teste"
CELULAR = dict(width=375, height=740)
FOTOS = os.environ.get("FOTOS")


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    banco = str(tmp_path_factory.mktemp("nav") / "nav.db")
    con = db.conectar(banco)
    db.criar_cliente(con, "Marta Jardim", telefone="(31) 99999-0000", saldo_inicial=67200, saldo_inicial_data="2026-11-01")
    db.criar_cliente(con, "Otávio Ribeira", saldo_inicial=-1000, saldo_inicial_data="2026-11-01")
    db.criar_cliente(con, "Neusa Pomar")
    con.close()
    servidor = make_server("127.0.0.1", 0, criar_app(banco=banco, senha=SENHA, chave="k" * 32))
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{servidor.server_port}", banco
    servidor.shutdown()


@pytest.fixture(scope="module")
def navegador():
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def pagina(navegador, site):
    url, _ = site
    contexto = navegador.new_context(viewport=CELULAR, locale="pt-BR")
    pg = contexto.new_page()
    erros = []
    pg.on("pageerror", lambda e: erros.append(str(e)))
    pg.on("console", lambda m: m.type == "error" and erros.append(m.text))
    pg.goto(url + "/entrar")
    pg.fill("input[name=senha]", SENHA)
    pg.click("button[type=submit]")
    pg.wait_for_url(url + "/")
    yield pg
    contexto.close()
    assert not erros, f"erros de JavaScript no navegador: {erros}"


def foto(pg, nome):
    if FOTOS:
        Path(FOTOS).mkdir(parents=True, exist_ok=True)
        pg.screenshot(path=f"{FOTOS}/{nome}.png", full_page=True)


def sem_rolagem_lateral(pg):
    largura = pg.evaluate("document.documentElement.scrollWidth")
    assert largura <= CELULAR["width"], f"{pg.url}: página mais larga que o celular ({largura}px)"


def abrir_cliente(pg, url, busca):
    pg.goto(url + "/clientes")
    pg.fill("#filtro", busca)
    visiveis = pg.locator("#lista li:visible a")
    assert visiveis.count() == 1, f"busca {busca!r} deveria achar 1 cliente"
    visiveis.click()


def test_fluxo_venda_pagamento_cancelamento(pagina, site):
    url, banco = site
    pg = pagina

    # cadastra dois produtos pela tela
    pg.goto(url + "/produtos")
    for nome, preco in [("Perfume 100 ml", "89,90"), ("Sabonete", "7,50")]:
        pg.fill("input[name=nome]", nome)
        pg.fill("input[name=preco]", preco)
        pg.click("form button[type=submit]")
        assert "cadastrado" in pg.inner_text(".aviso")
    foto(pg, "04_produtos")

    # nova venda: toca 2x no perfume, 1x no sabonete, + no sabonete, item avulso, muda preço
    abrir_cliente(pg, url, "jardim")
    assert "R$ 672,00" in pg.inner_text(".saldo-grande")
    foto(pg, "02_cliente")
    pg.click("text=Nova venda")
    assert pg.is_disabled("#salvar")
    pg.click("button.produto:has-text('Perfume')")
    pg.click("button.produto:has-text('Perfume')")
    assert pg.inner_text("button.produto:has-text('Perfume') .qtd") == "2"
    pg.click("button.produto:has-text('Sabonete')")
    pg.click("#carrinho li:has-text('Sabonete') .mais")
    pg.fill("#avulso-descricao", "Frete")
    pg.fill("#avulso-preco", "5")
    pg.click("#avulso-adicionar")
    assert pg.inner_text("#total") == "R$ 199,80"                       # 2×89,90 + 2×7,50 + 5
    assert pg.inner_text("#saldo-depois") == "R$ 871,80"                # 672 + 199,80
    campo = pg.locator("#carrinho li:has-text('Sabonete') .preco-item input")
    campo.fill("6,00")
    campo.dispatch_event("change")
    assert pg.inner_text("#total") == "R$ 196,80"
    sem_rolagem_lateral(pg)
    foto(pg, "03_venda")
    pg.click("#salvar")
    assert "Venda de R$ 196,80 registrada" in pg.inner_text(".aviso")
    assert "R$ 868,80" in pg.inner_text(".saldo-grande")
    assert "2× Perfume 100 ml" in pg.inner_text(".extrato")

    # pagamento com o botão "Pagou tudo"
    pg.click("text=Registrar pagamento")
    pg.click("text=Pagou tudo")
    assert pg.input_value("#valor") == "868,80"
    foto(pg, "05_pagamento")
    pg.click("button:has-text('Salvar pagamento')")
    assert "Saldo agora: R$ 0,00" in pg.inner_text(".aviso")
    assert "Não deve nada" in pg.inner_text(".saldo-grande")

    # cancelar o pagamento (confirmando a pergunta) devolve a dívida
    pg.once("dialog", lambda d: d.accept())
    pg.click(".extrato li.tipo-pagamento button:has-text('cancelar')")
    assert "Pagamento cancelado" in pg.inner_text(".aviso")
    assert "R$ 868,80" in pg.inner_text(".saldo-grande")
    assert pg.locator(".extrato li.cancelado").count() == 1
    foto(pg, "06_cliente_historico")

    # recusar a pergunta não cancela nada
    pg.once("dialog", lambda d: d.dismiss())
    pg.click(".extrato li.tipo-venda button:has-text('cancelar')")
    assert "R$ 868,80" in pg.inner_text(".saldo-grande")

    con = db.conectar(banco)
    assert db.cliente(con, 1)["saldo"] == 86880
    con.close()


def test_venda_sem_produto_cadastrado_e_valor_errado(pagina, site):
    url, _ = site
    pg = pagina
    abrir_cliente(pg, url, "Neusa")
    pg.click("text=Nova venda")
    alertas = []
    pg.on("dialog", lambda d: (alertas.append(d.message), d.accept()))
    pg.fill("#avulso-descricao", "Encomenda")
    pg.fill("#avulso-preco", "abc")
    pg.click("#avulso-adicionar")
    assert alertas and "Valor inválido" in alertas[0]
    assert pg.locator("#carrinho li").count() == 0


def test_lista_de_clientes_filtra_sem_acento_e_mostra_credito(pagina, site):
    url, _ = site
    pg = pagina
    pg.goto(url + "/clientes")
    assert pg.locator("#lista li:visible").count() == 3
    pg.fill("#filtro", "JARDIM")
    assert pg.locator("#lista li:visible").count() == 1
    pg.fill("#filtro", "")
    pg.check("#so-devendo")
    nomes = pg.locator("#lista li:visible .nome").all_inner_texts()
    assert not any("Otávio" in n for n in nomes)                      # Otávio tem crédito, não deve
    pg.uncheck("#so-devendo")
    assert "crédito R$ 10,00" in pg.inner_text("#lista li:has-text('Otávio')")
    pg.fill("#filtro", "ninguem com esse nome")
    assert pg.is_visible("#nada")


def test_cadastro_repetido_mostra_erro(pagina, site):
    url, _ = site
    pg = pagina
    pg.goto(url + "/clientes/novo")
    pg.fill("input[name=nome]", "neusa  POMAR")
    pg.click("button[type=submit]")
    assert "Já existe um cliente" in pg.inner_text(".aviso-erro")
    assert pg.input_value("input[name=nome]") == "neusa  POMAR"   # não perde o que foi digitado


@pytest.mark.parametrize("caminho", ["/", "/clientes", "/clientes/1", "/clientes/1/venda", "/clientes/1/pagamento",
                                     "/clientes/1/editar", "/clientes/novo", "/produtos", "/relatorios"])
def test_telas_cabem_no_celular(pagina, site, caminho):
    url, _ = site
    pagina.goto(url + caminho)
    sem_rolagem_lateral(pagina)
    if caminho in ("/", "/relatorios", "/clientes"):
        foto(pagina, {"/": "01_inicio", "/relatorios": "07_relatorios", "/clientes": "08_clientes"}[caminho])


def test_estoque_aparece_e_avisa_na_venda(pagina, site):
    url, _ = site
    pg = pagina
    pg.goto(url + "/produtos")
    pg.fill("input[name=nome]", "Kit Presente")
    pg.fill("input[name=preco]", "120")
    pg.fill("input[name=estoque]", "2")
    pg.click("form button[type=submit]")
    assert "2 em estoque" in pg.inner_text(".lista li:has-text('Kit Presente')")

    abrir_cliente(pg, url, "Otávio")
    pg.click("text=Nova venda")
    botao = pg.locator("button.produto:has-text('Kit Presente')")
    assert "restam 2" in botao.inner_text()
    for _ in range(3):
        botao.click()
    assert "faltam 1" in botao.inner_text()
    assert "só tem 2 em estoque" in pg.inner_text("#carrinho li:has-text('Kit Presente')")
    pg.click("#salvar")
    assert "Kit Presente ficou com -1" in pg.inner_text(".aviso-erro")

    pg.goto(url + "/produtos")
    assert "faltam 1" in pg.inner_text(".lista li:has-text('Kit Presente')")
    pg.click("text=Kit Presente")                       # abre o relatório do produto
    pg.click("text=Editar produto / estoque")
    pg.fill("input[name=entrada]", "5")
    pg.click("button[type=submit]")
    assert "Estoque de Kit Presente: 4" in pg.inner_text(".aviso")


def test_impressao_resumo_e_extrato(pagina, site):
    url, _ = site
    pg = pagina
    pg.goto(url + "/relatorios")
    pg.click("a.botao:has-text('Imprimir resumo do mês')")
    assert "Resumo de" in pg.inner_text("h1")
    sem_rolagem_lateral(pg)
    foto(pg, "09_imprimir_mes_tela")
    pg.select_option("select[name=mostrar]", "todos")
    pg.wait_for_url("**mostrar=todos**")
    assert "Total (" in pg.inner_text("tfoot")
    pg.emulate_media(media="print")
    assert not pg.is_visible(".barra") and not pg.is_visible("button.imprimir")
    if FOTOS:
        pg.pdf(path=f"{FOTOS}/resumo_mes.pdf", format="A4", print_background=True)
    pg.emulate_media(media="screen")

    abrir_cliente(pg, url, "jardim")
    pg.click("a.botao:has-text('Imprimir extrato')")
    assert "Extrato de" in pg.inner_text(".cabecalho-folha")
    sem_rolagem_lateral(pg)
    if FOTOS:
        pg.emulate_media(media="print")
        pg.pdf(path=f"{FOTOS}/extrato_cliente.pdf", format="A4", print_background=True)


def test_formulario_no_computador_e_observacao_cresce(navegador, site):
    url, _ = site
    ctx = navegador.new_context(viewport=dict(width=1665, height=900), color_scheme="dark")
    pg = ctx.new_page()
    pg.goto(url + "/entrar")
    pg.fill("input[name=senha]", SENHA)
    pg.click("button[type=submit]")
    pg.goto(url + "/clientes/novo")
    cartao = pg.locator(".cartao").bounding_box()
    for campo in ["input[name=nome]", "input[name=telefone]", "textarea", "button[type=submit]"]:
        caixa = pg.locator(campo).bounding_box()
        assert caixa["x"] + caixa["width"] <= cartao["x"] + cartao["width"] + 0.5, f"{campo} passa do quadro"
    obs = pg.locator("textarea")
    altura_vazia = obs.bounding_box()["height"]
    obs.fill("linha 1\nlinha 2\nlinha 3\nlinha 4\nlinha 5")
    obs.dispatch_event("input")
    assert obs.bounding_box()["height"] > altura_vazia + 40, "observação não cresceu"
    assert pg.evaluate("(t => t.scrollHeight <= t.clientHeight + 1)(document.querySelector('textarea'))"), "observação com rolagem"
    foto(pg, "10_form_computador")
    ctx.close()


def test_desconto_no_item_da_venda(pagina, site):
    """O exemplo que motivou a função: perfume de 200,00 que saiu por 170,00."""
    url, banco = site
    pg = pagina
    pg.goto(url + "/produtos")
    pg.fill("input[name=nome]", "Perfume Importado")
    pg.fill("input[name=preco]", "200")
    pg.fill("input[name=estoque]", "5")
    pg.click("form button[type=submit]")

    abrir_cliente(pg, url, "Neusa")
    pg.click("text=Nova venda")
    pg.click("button.produto:has-text('Perfume Importado')")
    linha = pg.locator("#carrinho li:has-text('Perfume Importado')")
    assert not linha.locator(".desconto").is_visible()
    assert "Vendido por" in linha.inner_text()

    linha.locator(".preco-item input").click()                 # tocar seleciona o valor: digitar substitui
    pg.keyboard.type("170")
    assert linha.locator(".preco-item input").input_value() == "170"
    assert pg.inner_text("#total") == "R$ 170,00"                              # atualiza enquanto digita
    assert linha.locator(".desconto").inner_text() == "Preço normal R$ 200,00 · desconto de R$ 30,00 (15%)"
    assert pg.inner_text("#descontos") == "Descontos nesta venda: R$ 30,00"
    linha.locator(".mais").click()
    assert pg.inner_text("#total") == "R$ 340,00"
    assert "desconto de R$ 60,00" in pg.locator("#carrinho li:has-text('Perfume Importado') .desconto").inner_text()
    sem_rolagem_lateral(pg)
    foto(pg, "11_venda_desconto")

    pg.click("#salvar")
    assert "Venda de R$ 340,00 registrada para Neusa Pomar (com desconto de R$ 60,00)" in pg.inner_text(".aviso")
    assert "de R$ 200,00, desconto de R$ 60,00" in pg.inner_text(".extrato")


def test_ordenar_por_maior_e_menor_saldo(pagina, site):
    url, _ = site
    pg = pagina
    pg.goto(url + "/clientes")
    saldos = lambda: [int(s) for s in pg.locator("#lista li:visible").evaluate_all("els => els.map(e => e.dataset.saldo)")]
    pg.select_option("#ordem", "saldo")
    assert saldos() == sorted(saldos(), reverse=True)
    pg.select_option("#ordem", "menor")
    assert saldos() == sorted(saldos())
    assert saldos()[0] == min(saldos()) and len(set(saldos())) > 1   # de fato reordenou valores diferentes


@pytest.mark.parametrize("tema_do_aparelho", ["light", "dark"])
def test_botao_de_tema(navegador, site, tema_do_aparelho):
    url, _ = site
    ctx = navegador.new_context(viewport=CELULAR, color_scheme=tema_do_aparelho)
    pg = ctx.new_page()
    pg.goto(url + "/entrar")
    pg.fill("input[name=senha]", SENHA)
    pg.click("button[type=submit]")
    fundo = lambda: pg.evaluate("getComputedStyle(document.body).backgroundColor")
    CLARO, ESCURO = "rgb(236, 226, 214)", "rgb(23, 19, 15)"
    botao = pg.locator("#botao-tema")
    assert botao.inner_text() == "🌓 Automático"
    assert fundo() == (CLARO if tema_do_aparelho == "light" else ESCURO)       # automático segue o aparelho
    botao.click()
    assert botao.inner_text() == "☀️ Claro" and fundo() == CLARO
    botao.click()
    assert botao.inner_text() == "🌙 Escuro" and fundo() == ESCURO
    pg.goto(url + "/clientes")                                                    # lembra a escolha
    assert pg.locator("#botao-tema").inner_text() == "🌙 Escuro" and fundo() == ESCURO
    sem_rolagem_lateral(pg)
    pg.locator("#botao-tema").click()
    assert pg.locator("#botao-tema").inner_text() == "🌓 Automático"
    assert fundo() == (CLARO if tema_do_aparelho == "light" else ESCURO)
    ctx.close()


def test_graficos_de_relatorios(pagina, site):
    url, banco = site
    con = db.conectar(banco)
    c = db.criar_cliente(con, "Beatriz Gráfico")
    for data, valor in [("2026-11-01", 15000), ("2026-11-02", 52000), ("2026-03-05", 8000)]:
        db.registrar_venda(con, c, data, [dict(produto_id=None, descricao="x", quantidade=1, preco_unit=valor)])
    con.close()
    pg = pagina
    pg.goto(url + "/relatorios?mes=2026-11")
    dias = pg.evaluate("JSON.parse(document.getElementById('dados-grafico-mes').textContent)")
    com_venda = [d for d in dias if d["total"] > 0]
    assert pg.locator("#grafico-mes path.barra").count() == len(com_venda)
    assert pg.locator("#grafico-mes .rotulo-valor").count() == 1               # só o maior ganha número
    sem_rolagem_lateral(pg)
    foto(pg, "12_relatorios_graficos")

    # mouse sobre a coluna do dia 2 mostra o valor
    melhor = max(com_venda, key=lambda d: d["total"])
    coluna = pg.locator("#grafico-mes g.dia").nth(melhor["dia"] - 1)
    coluna.hover()
    assert pg.locator(".dica-grafico").is_visible()
    assert pg.inner_text(".dica-grafico").startswith(f"Dia {melhor['dia']} (")

    # toque num quadradinho do mapa (março, dia 5) mostra o valor e continua aparecendo
    celula = pg.locator(".mapa .celula[data-dica^='5 de março']")
    celula.scroll_into_view_if_needed()
    pg.mouse.move(1, 1)          # tira o mouse de cima: parado, ele "acenderia" o quadradinho que rolar para baixo dele
    celula.dispatch_event("pointerover", {"pointerType": "touch"})
    celula.dispatch_event("pointerout", {"pointerType": "touch"})
    assert pg.inner_text(".dica-grafico") == "5 de março (qui): R$ 80,00 · 1 venda"
    pg.locator("h1").click()                                                   # tocar fora esconde
    assert not pg.locator(".dica-grafico").is_visible()
    # o nome do mês leva aos relatórios daquele mês
    pg.click(".mapa-mes:has-text('mar')")
    assert "mes=2026-03" in pg.url and "Vendas por dia · março de 2026" in pg.inner_text("main")


def test_telas_novas_no_celular(pagina, site):
    url, banco = site
    con = db.conectar(banco)
    c = db.criar_cliente(con, "Marisa Atrasada", telefone="(31) 98888-7777", saldo_inicial=31000, saldo_inicial_data="2025-05-01")
    db.atualizar_cliente(con, c, "Marisa Atrasada", "(31) 98888-7777", "", 31000, False)
    con.close()
    pg = pagina
    pg.goto(url + "/clientes")
    pg.select_option("#mostrar", "desativados")
    pg.wait_for_url("**mostrar=desativados**")
    assert "Marisa Atrasada" in pg.inner_text("#lista") and "desativados" in pg.inner_text(".aviso-info")
    sem_rolagem_lateral(pg)

    pg.goto(url + "/")
    pg.click(".alerta-atraso:not(.alerta-backup)")      # o aviso de backup também é um cartão
    assert "/clientes/sem-pagar" in pg.url
    linha = pg.locator(".lista-atraso li:has-text('Marisa Atrasada')")
    assert "nenhum pagamento registrado" in linha.inner_text() and "(desativado)" in linha.inner_text()
    numeros = linha.locator(".atraso-numeros").inner_text()
    assert "R$ 310,00" in numeros and "há " in numeros and "deve desde 01/05/2025" in numeros   # dias e data juntos
    assert linha.locator("a[href^='tel:']").count() == 1
    pg.select_option("select[name=dias]", "180")
    pg.wait_for_url("**dias=180**")
    sem_rolagem_lateral(pg)
    foto(pg, "13_sem_pagar")

    pg.goto(url + "/produtos")
    pg.click(".lista a:has-text('Perfume Importado')")
    assert "Vendas por mês em" in pg.inner_text("main")
    assert pg.locator(".barras-barra").count() >= 1
    sem_rolagem_lateral(pg)
    foto(pg, "14_produto")


def test_filtros_de_produto_e_busca_por_codigo(pagina, site):
    url, banco = site
    con = db.conectar(banco)
    db.criar_produto(con, "Essencial Clássico", 25000, estoque=4, codigo="73455", marca="Natura", tipo="Perfume")
    db.criar_produto(con, "Malbec", 19000, estoque=0, codigo="88001", marca="O Boticário", tipo="Perfume")
    db.criar_produto(con, "Tododia Ameixa", 5500, estoque=9, marca="Natura", tipo="Hidratante")
    parado = db.criar_produto(con, "Linha Antiga", 1000, tipo="Hidratante")
    db.atualizar_produto(con, parado, "Linha Antiga", 1000, False)
    con.close()
    pg = pagina
    pg.goto(url + "/produtos")
    visiveis = lambda: pg.locator("#lista li:visible .produto-nome a").all_inner_texts()
    pg.fill("#filtro", "73455")
    assert visiveis() == ["Essencial Clássico"]                                  # pelo código
    pg.fill("#filtro", "boticario")
    assert visiveis() == ["Malbec"]                                              # pela marca, sem acento
    pg.fill("#filtro", "")
    pg.select_option("#filtro-tipo", "perfume")
    pg.select_option("#filtro-marca", "natura")
    assert visiveis() == ["Essencial Clássico"]                                  # tipo + marca juntos
    pg.select_option("#filtro-tipo", "")
    pg.select_option("#filtro-marca", "")
    pg.select_option("#filtro-estoque", "sem")
    assert "Malbec" in visiveis() and "Tododia Ameixa" not in visiveis()
    pg.select_option("#filtro-estoque", "")
    assert "Linha Antiga" not in visiveis()
    pg.select_option("#filtro-situacao", "desativados")
    assert visiveis() == ["Linha Antiga"]
    sem_rolagem_lateral(pg)

    abrir_cliente(pg, url, "Neusa")
    pg.click("text=Nova venda")
    pg.fill("#busca-produto", "88001")
    assert pg.locator("button.produto").all_inner_texts()[0].startswith("Malbec")
    assert pg.locator("button.produto").count() == 1
    pg.fill("#busca-produto", "")
    pg.select_option("#filtro-tipo", "Hidratante")
    nomes = [t.split("\n")[0] for t in pg.locator("button.produto").all_inner_texts()]
    assert nomes == ["Tododia Ameixa"]                                           # desativado não aparece na venda
    assert "Hidratante · Natura" in pg.locator("button.produto").first.inner_text()


def test_produto_repetido_aviso_e_juntar(pagina, site):
    url, banco = site
    pg = pagina
    pg.goto(url + "/produtos")
    for campo, valor in [("nome", "Egeo Dolce 90ml"), ("preco", "120"), ("estoque", "2")]:
        pg.fill(f".form-produto input[name={campo}]", valor)
    pg.click(".form-produto button[type=submit]")
    for campo, valor in [("nome", "EGEO DOLCE 90 ML"), ("preco", "120"), ("estoque", "3")]:   # repetido por engano
        pg.fill(f".form-produto input[name={campo}]", valor)
    pg.click(".form-produto button[type=submit]")
    assert "Não quis dizer este produto?" in pg.inner_text("#aviso-parecido")
    sem_rolagem_lateral(pg)
    foto(pg, "15_nao_quis_dizer")
    pg.click("#aviso-parecido button:has-text('Sim, é esse')")
    assert "Somei 3 ao estoque" in pg.inner_text(".aviso")
    con = db.conectar(banco)
    egeo = con.execute("SELECT id, estoque FROM produtos WHERE nome = 'Egeo Dolce 90ml'").fetchall()
    assert len(egeo) == 1 and egeo[0]["estoque"] == 5
    # um par que já estava repetido aparece na lista e é juntado
    a = db.criar_produto(con, "Quasar Classic", 9000, estoque=1)
    b = db.criar_produto(con, "Quasar Clasic", 9000, estoque=2, codigo="QC1")
    con.close()
    pg.goto(url + "/produtos")
    par = pg.locator(".par-repetido:has-text('Quasar')")
    assert par.count() == 1
    par.locator("a:has-text('Juntar')").click()
    sem_rolagem_lateral(pg)
    foto(pg, "16_juntar")
    pg.check(f"input[name=fica][value='{a}']")
    pg.once("dialog", lambda d: d.accept())
    pg.click("button:has-text('Juntar os dois')")
    assert "foi juntado em" in pg.inner_text(".aviso")
    assert "cód. QC1" in pg.inner_text("main")                     # código do outro veio junto
    con = db.conectar(banco)
    assert db.produto(con, a)["estoque"] == 3
    con.close()


def test_desconto_em_porcentagem(pagina, site):
    url, banco = site
    con = db.conectar(banco)
    db.criar_produto(con, "Perfume Teste Porcento", 20000, estoque=5)
    con.close()
    pg = pagina
    abrir_cliente(pg, url, "Neusa")
    pg.click("text=Nova venda")
    pg.fill("#busca-produto", "porcento")
    pg.click("button.produto:has-text('Perfume Teste Porcento')")
    linha = pg.locator("#carrinho li:has-text('Perfume Teste Porcento')")
    linha.locator(".pct-item input").click()
    pg.keyboard.type("10")
    assert linha.locator(".preco-item input").input_value() == "180,00"           # 200 - 10%
    assert "desconto de R$ 20,00 (10%)" in linha.locator(".desconto").inner_text()
    linha.locator(".preco-item input").fill("150")                                 # o contrário: preço -> %
    assert linha.locator(".pct-item input").input_value() == "25"
    linha.locator(".pct-item input").fill("12,5")
    assert linha.locator(".preco-item input").input_value() == "175,00"
    # item avulso: a % vale sobre o valor digitado
    pg.fill("#avulso-descricao", "Embrulho")
    pg.fill("#avulso-preco", "50")
    pg.click("#avulso-adicionar")
    avulso = pg.locator("#carrinho li:has-text('Embrulho')")
    avulso.locator(".pct-item input").fill("20")
    assert avulso.locator(".preco-item input").input_value() == "40,00"
    assert pg.inner_text("#total") == "R$ 215,00"                                  # 175 + 40
    sem_rolagem_lateral(pg)
    foto(pg, "17_desconto_porcentagem")
    pg.click("#salvar")
    assert "Venda de R$ 215,00 registrada" in pg.inner_text(".aviso") and "desconto de R$ 25,00" in pg.inner_text(".aviso")
