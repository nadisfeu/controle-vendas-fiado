"""Banco de dados (SQLite) do controle de vendas fiado.

Regras que este módulo garante:
- Dinheiro é sempre guardado em centavos (inteiro), nunca em float.
- O saldo do cliente NUNCA é guardado nem copiado: é sempre calculado como
  saldo_inicial + compras - pagamentos. Na planilha antiga o saldo era copiado
  de mês para mês pelo nome, e quando o nome mudava a dívida sumia calada.
- Venda e pagamento não são apagados, são cancelados (ficam no histórico).
- O preço do item é copiado para a venda: mudar o preço do produto depois não
  altera vendas antigas.
"""
import json
import re
import sqlite3
from difflib import SequenceMatcher
from pathlib import Path
import unicodedata
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

SCHEMA = """
CREATE TABLE IF NOT EXISTS clientes (
    id                 INTEGER PRIMARY KEY,
    nome               TEXT NOT NULL,
    nome_busca         TEXT NOT NULL UNIQUE,   -- sem acento/maiúscula, impede cadastro repetido
    telefone           TEXT NOT NULL DEFAULT '',
    observacao         TEXT NOT NULL DEFAULT '',
    saldo_inicial      INTEGER NOT NULL DEFAULT 0,  -- centavos, trazido da planilha antiga
    saldo_inicial_data TEXT,
    ultimo_pagamento_planilha TEXT,              -- 'AAAA-MM': último mês com pagamento na planilha antiga
    ativo              INTEGER NOT NULL DEFAULT 1,
    criado_em          TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS produtos (
    id         INTEGER PRIMARY KEY,
    nome       TEXT NOT NULL,
    nome_busca TEXT NOT NULL UNIQUE,
    preco      INTEGER NOT NULL CHECK (preco >= 0),
    estoque    INTEGER NOT NULL DEFAULT 0,          -- pode ficar negativo: vendeu sem ter (encomenda)
    codigo     TEXT,                                -- opcional; se preenchido, não repete (sem diferença de maiúscula)
    marca      TEXT NOT NULL DEFAULT '',            -- opcional; escrita padronizada por formatar_nome
    tipo       TEXT NOT NULL DEFAULT '',            -- opcional (perfume, hidratante...); idem
    ativo      INTEGER NOT NULL DEFAULT 1,
    criado_em  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS vendas (
    id           INTEGER PRIMARY KEY,
    cliente_id   INTEGER NOT NULL REFERENCES clientes(id),
    data         TEXT NOT NULL,
    observacao   TEXT NOT NULL DEFAULT '',
    cancelada_em TEXT,
    criado_em    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS itens_venda (
    id         INTEGER PRIMARY KEY,
    venda_id   INTEGER NOT NULL REFERENCES vendas(id),
    produto_id INTEGER REFERENCES produtos(id),   -- NULL = item avulso
    descricao  TEXT NOT NULL,
    quantidade INTEGER NOT NULL CHECK (quantidade > 0),
    preco_unit INTEGER NOT NULL CHECK (preco_unit >= 0),   -- por quanto foi vendido (cada)
    preco_tabela INTEGER                                    -- preço normal do produto na hora; NULL = avulso
);
CREATE TABLE IF NOT EXISTS pagamentos (
    id           INTEGER PRIMARY KEY,
    cliente_id   INTEGER NOT NULL REFERENCES clientes(id),
    data         TEXT NOT NULL,
    valor        INTEGER NOT NULL CHECK (valor > 0),
    forma        TEXT NOT NULL DEFAULT '',
    observacao   TEXT NOT NULL DEFAULT '',
    cancelado_em TEXT,
    criado_em    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS meta (           -- anotações do sistema (ex.: quando foi o último backup)
    chave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_vendas_cliente ON vendas(cliente_id);
CREATE INDEX IF NOT EXISTS ix_itens_venda ON itens_venda(venda_id);
CREATE INDEX IF NOT EXISTS ix_pagamentos_cliente ON pagamentos(cliente_id);
"""

FORMAS_PAGAMENTO = ["Dinheiro", "Pix", "Cartão", "Outro"]


class ErroDeCadastro(ValueError):
    """Erro com mensagem pronta para mostrar na tela."""


def conectar(caminho):
    con = sqlite3.connect(caminho)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA)
    _atualizar_banco_antigo(con)
    return con


def _atualizar_banco_antigo(con):
    """Acrescenta colunas criadas depois da primeira versão, sem perder dados."""
    colunas = {r["name"] for r in con.execute("PRAGMA table_info(produtos)")}
    if "estoque" not in colunas:
        with con:
            con.execute("ALTER TABLE produtos ADD COLUMN estoque INTEGER NOT NULL DEFAULT 0")
    colunas = {r["name"] for r in con.execute("PRAGMA table_info(itens_venda)")}
    if "preco_tabela" not in colunas:
        with con:
            con.execute("ALTER TABLE itens_venda ADD COLUMN preco_tabela INTEGER")
    colunas = {r["name"] for r in con.execute("PRAGMA table_info(produtos)")}
    if "codigo" not in colunas:
        with con:
            con.execute("ALTER TABLE produtos ADD COLUMN codigo TEXT")
            con.execute("ALTER TABLE produtos ADD COLUMN marca TEXT NOT NULL DEFAULT ''")
    if "tipo" not in {r["name"] for r in con.execute("PRAGMA table_info(produtos)")}:
        with con:
            con.execute("ALTER TABLE produtos ADD COLUMN tipo TEXT NOT NULL DEFAULT ''")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_produtos_codigo ON produtos(upper(codigo)) WHERE codigo IS NOT NULL")
    if not ler_meta(con, "produtos_nomes_padronizados_v4"):     # v4: ordem nome/gênero/tamanho/cor; XL, 2XL, G1...
        if con.execute("SELECT 1 FROM produtos LIMIT 1").fetchone():
            _copia_antes_de_mudar(con, "organizar_nomes_produtos")
        padronizar_nomes_produtos(con)
        gravar_meta(con, "produtos_nomes_padronizados_v4", date.today().isoformat())
    colunas = {r["name"] for r in con.execute("PRAGMA table_info(clientes)")}
    if "ultimo_pagamento_planilha" not in colunas:
        with con:
            con.execute("ALTER TABLE clientes ADD COLUMN ultimo_pagamento_planilha TEXT")


def ler_meta(con, chave):
    linha = con.execute("SELECT valor FROM meta WHERE chave = ?", (chave,)).fetchone()
    return linha["valor"] if linha else None


def gravar_meta(con, chave, valor):
    with con:
        con.execute("INSERT INTO meta (chave, valor) VALUES (?, ?)"
                    " ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor", (chave, valor))


def copiar_banco(con, destino):
    """Cópia consistente do banco (API de backup do SQLite: segura mesmo com o site usando o banco)."""
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_suffix(".tmp")
    copia = sqlite3.connect(temporario)
    try:
        con.backup(copia)
    finally:
        copia.close()
    temporario.replace(destino)          # só aparece com o nome final quando está completa


# ---------------------------------------------------------------- texto e dinheiro

def limpar_nome(texto):
    return " ".join((texto or "").split())


_PALAVRAS_MINUSCULAS = {"de", "da", "do", "das", "dos", "e", "em", "no", "na", "nos", "nas", "com", "que"}


def formatar_nome(texto):
    """'MARTA  JARDIM' -> 'Marta Jardim'; 'regina filha DE ze' -> 'Regina Filha de Ze'.

    Primeira letra de cada palavra maiúscula, resto minúsculo; 'de/da/do/das/dos/e/em/no/na/com' ficam minúsculos
    (menos no começo). Só a primeira letra da palavra: 'guarda-chuva' -> 'Guarda-chuva', '(ane' -> '(Ane'.
    """
    palavras = limpar_nome(texto).lower().split(" ")
    saida = []
    for i, palavra in enumerate(palavras):
        if i > 0 and palavra in _PALAVRAS_MINUSCULAS:
            saida.append(palavra)
            continue
        letra = next((j for j, ch in enumerate(palavra) if ch.isalpha()), None)
        saida.append(palavra if letra is None else palavra[:letra] + palavra[letra].upper() + palavra[letra + 1:])
    # depois de apóstrofo também é maiúscula: "d'ávila" -> "D'Ávila", "d’essence" -> "D’Essence"
    return re.sub(r"([’'])(\w)", lambda m: m.group(1) + m.group(2).upper(), " ".join(saida))


UNIDADES = {"ml", "l", "g", "kg", "mg", "cm", "mm", "m", "un", "und", "pct", "cx"}
# tamanhos de roupa/calçado: sozinhos (sem número antes) são tamanho, não unidade ("Camiseta G" x "200 g")
TAMANHOS = {
    "ppp", "pp", "p", "m", "g", "gg", "ggg", "u",                       # brasileiros
    "xg", "xgg", "xxg", "xxxg", "eg", "egg", "exg", "exgg", "g1", "g2", "g3", "g4", "g5",   # grandes
    "xxs", "xs", "s", "l", "xl", "xxl", "xxxl", "2xl", "3xl", "4xl", "5xl",                 # em inglês
}


def e_codigo_de_modelo(palavra):
    """Código de modelo/referência: letras e depois números, 4+ caracteres ('LRMM049L36', 'XMPP0055', 'MRG4162L').
    Tamanho ('G1', '2XL') e medida ('100ml') não contam."""
    c = palavra.lower()
    return len(c) >= 4 and c not in TAMANHOS and re.fullmatch(r"[a-z]+\d[a-z0-9]*", c) is not None


def formatar_nome_produto(texto):
    """Como formatar_nome, com três ajustes (a primeira palavra sempre começa maiúscula):
    - número com unidade grudada fica minúsculo: 'KAIAK 100ML' -> 'Kaiak 100ml';
    - unidade logo depois de um número fica minúscula: 'creme 200 G' -> 'Creme 200 g';
    - tamanho sozinho fica maiúsculo: 'camiseta gg' -> 'Camiseta GG', 'calça m' -> 'Calça M'."""
    palavras = formatar_nome(texto).split(" ")
    saida = []
    for i, p in enumerate(palavras):
        anterior_e_numero = i > 0 and palavras[i - 1][:1].isdigit()
        if anterior_e_numero and p.lower() in UNIDADES:
            p = p.lower()                                  # '200 G' -> '200 g', '1 L' -> '1 l'
        elif p.lower() in TAMANHOS:
            p = p.upper()                                  # 'xl' -> 'XL', '2xl' -> '2XL', 'g1' -> 'G1'
        elif e_codigo_de_modelo(p):
            p = p.upper()                                  # 'lrmm049l36' -> 'LRMM049L36'
        elif i > 0 and p[:1].isdigit():
            p = p.lower()
        saida.append(p)
    return organizar_nome_produto(" ".join(saida))


GENEROS = {"masculino", "masculina", "feminino", "feminina", "infantil", "unissex", "menino", "menina",
           "adulto", "adulta", "juvenil", "masc", "fem", "bebe", "baby"}
CORES = {"azul", "preto", "preta", "branco", "branca", "vermelho", "vermelha", "verde", "amarelo", "amarela",
         "rosa", "roxo", "roxa", "lilas", "cinza", "marrom", "bege", "laranja", "dourado", "dourada", "prata",
         "prateado", "prateada", "nude", "vinho", "caramelo", "estampado", "estampada", "colorido", "colorida",
         "rose", "chocolate", "marsala", "grafite", "pink", "fucsia", "salmao", "coral", "off-white", "offwhite"}


def organizar_nome_produto(nome):
    """Ordem fixa para roupa: nome do produto, gênero, tamanho, depois cor e medidas.

    'Camisa GG Regata Masculina Azul' -> 'Camisa Regata Masculina GG Azul';
    'Calça Jeans 38 Feminina' -> 'Calça Jeans Feminina 38'.
    Só mexe em nome que tenha gênero ou tamanho (P, M, G...): cosmético e o resto ficam como estão
    ('Rosa Mosqueta Óleo', 'Kit 3 Sabonetes'). As palavras do nome mantêm a ordem em que foram digitadas.
    """
    palavras = nome.split(" ")
    chaves = [normalizar(p) for p in palavras]
    tem_tamanho = any(c in TAMANHOS and not (i > 0 and chaves[i - 1][:1].isdigit()) for i, c in enumerate(chaves))
    if not tem_tamanho and not any(c in GENEROS for c in chaves):   # '90 g' é medida, não tamanho
        return nome

    def e_extra(i):          # gênero, tamanho ou cor: o que pode sair do meio do nome
        return chaves[i] in GENEROS or chaves[i] in TAMANHOS or chaves[i] in CORES

    produto, genero, tamanho, outros = [], [], [], []
    for i, (p, c) in enumerate(zip(palavras, chaves)):
        seguinte_e_unidade = i + 1 < len(chaves) and chaves[i + 1] in UNIDADES
        if c in GENEROS:
            genero.append(p)
        elif c in TAMANHOS and not (i > 0 and chaves[i - 1][:1].isdigit()):
            tamanho.append(p)
        elif c in CORES or e_codigo_de_modelo(c):
            outros.append(p)                               # cor e código do modelo vão para o fim
        elif re.fullmatch(r"\d+[a-z]+", c) or (c.isdigit() and seguinte_e_unidade) or (
                c in UNIDADES and i > 0 and chaves[i - 1].isdigit()):
            outros.append(p)                               # medida: '100ml', '200 g'
        elif c.isdigit() and all(e_extra(j) for j in range(i + 1, len(chaves))):
            tamanho.append(p)                              # número no fim: tamanho ('Calça Jeans 38')
        else:
            produto.append(p)
    if not produto:                                        # só gênero/tamanho/cor: não tem o que reorganizar
        return nome
    novo = " ".join(produto + genero + tamanho + outros)
    return novo[:1].upper() + novo[1:]


def normalizar(texto):
    sem_acento = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return " ".join(sem_acento.lower().split())


def reais_para_centavos(texto):
    """'12,50' / '1.234,56' / 'R$ 10' / '12.5' -> centavos. Erro claro se não for valor."""
    if isinstance(texto, int):
        return texto * 100
    s = (texto or "").strip().replace("R$", "").replace(" ", "")
    if not s:
        raise ErroDeCadastro("Informe um valor.")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")          # '1.234' é mil duzentos e trinta e quatro
    try:
        valor = Decimal(s)
    except InvalidOperation:
        raise ErroDeCadastro(f'"{texto}" não é um valor válido. Use, por exemplo, 12,50.')
    if valor != valor.quantize(Decimal("0.01")):
        raise ErroDeCadastro(f'"{texto}" tem mais de 2 casas depois da vírgula.')
    return int(valor * 100)


def centavos_para_reais(centavos):
    sinal = "-" if centavos < 0 else ""
    inteiro, resto = divmod(abs(int(centavos)), 100)
    return f"{sinal}R$ {inteiro:,}".replace(",", ".") + f",{resto:02d}"


def centavos_para_campo(centavos):
    """Valor para preencher um campo de formulário: 1250 -> '12,50'."""
    return f"{centavos / 100:.2f}".replace(".", ",")


def ler_quantidade(texto, permitir_negativo=False):
    """'10' -> 10. Erro claro se não for número inteiro."""
    s = str(texto if texto is not None else "").strip()
    if not s:
        return 0
    try:
        n = int(s)
    except ValueError:
        raise ErroDeCadastro(f'"{texto}" não é uma quantidade válida. Use um número inteiro, por exemplo 10.')
    if n < 0 and not permitir_negativo:
        raise ErroDeCadastro("A quantidade não pode ser negativa.")
    return n


def validar_data(texto):
    try:
        return date.fromisoformat(texto).isoformat()
    except (TypeError, ValueError):
        raise ErroDeCadastro("Data inválida.")


# ---------------------------------------------------------------- clientes

def _checar_nome_livre(con, tabela, nome, ignorar_id=None):
    linha = con.execute(f"SELECT id, nome FROM {tabela} WHERE nome_busca = ?",
                        (normalizar(nome),)).fetchone()
    if linha and linha["id"] != ignorar_id:
        oque = "um cliente" if tabela == "clientes" else "um produto"
        raise ErroDeCadastro(f'Já existe {oque} chamado "{linha["nome"]}".')


def criar_cliente(con, nome, telefone="", observacao="", saldo_inicial=0, saldo_inicial_data=None):
    nome = formatar_nome(nome)
    if not nome:
        raise ErroDeCadastro("O nome do cliente é obrigatório.")
    _checar_nome_livre(con, "clientes", nome)
    with con:
        cur = con.execute(
            "INSERT INTO clientes (nome, nome_busca, telefone, observacao, saldo_inicial, saldo_inicial_data)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (nome, normalizar(nome), telefone.strip(), observacao.strip(), saldo_inicial, saldo_inicial_data))
    return cur.lastrowid


def atualizar_cliente(con, cliente_id, nome, telefone, observacao, saldo_inicial, ativo):
    nome = formatar_nome(nome)
    if not nome:
        raise ErroDeCadastro("O nome do cliente é obrigatório.")
    _checar_nome_livre(con, "clientes", nome, ignorar_id=cliente_id)
    with con:
        con.execute(
            "UPDATE clientes SET nome=?, nome_busca=?, telefone=?, observacao=?, saldo_inicial=?, ativo=?"
            " WHERE id=?",
            (nome, normalizar(nome), telefone.strip(), observacao.strip(), saldo_inicial, int(ativo), cliente_id))


_SQL_SALDOS = """
SELECT c.*,
       COALESCE(v.comprado, 0) AS comprado,
       COALESCE(p.pago, 0)     AS pago,
       c.saldo_inicial + COALESCE(v.comprado, 0) - COALESCE(p.pago, 0) AS saldo,
       v.ultima_compra, p.ultimo_pagamento
FROM clientes c
LEFT JOIN (SELECT ve.cliente_id, SUM(i.quantidade * i.preco_unit) AS comprado, MAX(ve.data) AS ultima_compra
           FROM vendas ve JOIN itens_venda i ON i.venda_id = ve.id
           WHERE ve.cancelada_em IS NULL GROUP BY ve.cliente_id) v ON v.cliente_id = c.id
LEFT JOIN (SELECT cliente_id, SUM(valor) AS pago, MAX(data) AS ultimo_pagamento
           FROM pagamentos WHERE cancelado_em IS NULL GROUP BY cliente_id) p ON p.cliente_id = c.id
"""


def padronizar_nomes_clientes(con):
    """Aplica formatar_nome a todos os clientes. Devolve [(antes, depois)] dos que mudaram."""
    mudancas = [(r["id"], r["nome"], formatar_nome(r["nome"]))
                for r in con.execute("SELECT id, nome FROM clientes ORDER BY nome_busca")]
    mudancas = [m for m in mudancas if m[1] != m[2]]
    with con:
        # nome_busca não muda (já ignora maiúscula e acento), então não há risco de nome repetido
        con.executemany("UPDATE clientes SET nome = ? WHERE id = ?", [(depois, i) for i, _, depois in mudancas])
    return [(antes, depois) for _, antes, depois in mudancas]


def clientes_sem_pagar(con, hoje, dias_minimos):
    """Quem deve e não paga há pelo menos `dias_minimos` dias, do que está há mais tempo para o de menos.

    Último pagamento = o mais recente entre o registrado no sistema e o mês da planilha antiga
    (contado como o último dia daquele mês, a favor do cliente). Sem nenhum pagamento conhecido,
    conta desde que a dívida existe no sistema (data do saldo trazido ou primeira compra).
    """
    primeira_compra = {r[0]: r[1] for r in con.execute(
        "SELECT cliente_id, MIN(data) FROM vendas WHERE cancelada_em IS NULL GROUP BY cliente_id")}
    lista = []
    for c in saldos(con, apenas_ativos=False):
        if c["saldo"] <= 0:
            continue
        no_sistema = date.fromisoformat(c["ultimo_pagamento"]) if c["ultimo_pagamento"] else None
        na_planilha = None
        if c["ultimo_pagamento_planilha"]:
            ano, mes = int(c["ultimo_pagamento_planilha"][:4]), int(c["ultimo_pagamento_planilha"][5:7])
            na_planilha = date(ano + (mes == 12), mes % 12 + 1, 1) - timedelta(days=1)
        if no_sistema or na_planilha:
            referencia = max(d for d in (no_sistema, na_planilha) if d)
            origem = "sistema" if referencia == no_sistema else "planilha"
        else:
            desde = [d for d in (c["saldo_inicial_data"] if c["saldo_inicial"] > 0 else None,
                                 primeira_compra.get(c["id"])) if d]
            referencia = date.fromisoformat(min(desde)) if desde else hoje
            origem = "nenhum"
        dias = (hoje - referencia).days
        if dias >= dias_minimos:
            lista.append(dict(id=c["id"], nome=c["nome"], ativo=c["ativo"], telefone=c["telefone"], saldo=c["saldo"],
                              referencia=referencia.isoformat(), origem=origem, dias=dias,
                              mes_planilha=c["ultimo_pagamento_planilha"]))
    lista.sort(key=lambda x: (-x["dias"], -x["saldo"]))
    return lista


def padronizar_nomes_produtos(con):
    """Aplica formatar_nome_produto a todos os produtos. Devolve [(antes, depois)] dos que mudaram.

    Reorganizar pode mudar o nome_busca: se o nome novo já for de outro produto, este fica como está
    (não cria nome repetido; o par aparece em 'possíveis produtos repetidos')."""
    linhas = con.execute("SELECT id, nome FROM produtos ORDER BY id").fetchall()
    em_uso = {normalizar(r["nome"]): r["id"] for r in linhas}
    mudancas = []
    for r in linhas:
        novo = formatar_nome_produto(r["nome"])
        if novo == r["nome"]:
            continue
        dono = em_uso.get(normalizar(novo))
        if dono is not None and dono != r["id"]:
            continue
        em_uso.pop(normalizar(r["nome"]), None)
        em_uso[normalizar(novo)] = r["id"]
        mudancas.append((r["id"], r["nome"], novo))
    with con:
        con.executemany("UPDATE produtos SET nome = ?, nome_busca = ? WHERE id = ?",
                        [(depois, normalizar(depois), i) for i, _, depois in mudancas])
    return [(antes, depois) for _, antes, depois in mudancas]


def _copia_antes_de_mudar(con, motivo):
    """Cópia do banco ao lado dele (backups/) antes de uma mudança em massa feita sozinha."""
    arquivo = con.execute("PRAGMA database_list").fetchone()["file"]
    if arquivo:
        copiar_banco(con, Path(arquivo).parent / "backups" / f"antes_{motivo}_{date.today().isoformat()}.db")


def saldos(con, apenas_ativos=True):
    where = " WHERE c.ativo = 1" if apenas_ativos else ""
    return con.execute(_SQL_SALDOS + where + " ORDER BY c.nome_busca").fetchall()


def cliente(con, cliente_id):
    linha = con.execute(_SQL_SALDOS + " WHERE c.id = ?", (cliente_id,)).fetchone()
    if linha is None:
        raise ErroDeCadastro("Cliente não encontrado.")
    return linha


def extrato(con, cliente_id):
    """Compras e pagamentos do cliente, do mais novo para o mais antigo, com o saldo após cada um."""
    c = cliente(con, cliente_id)
    linhas = []
    if c["saldo_inicial"]:
        linhas.append(dict(tipo="inicial", id=None, data=c["saldo_inicial_data"] or "", criado_em="",
                           valor=c["saldo_inicial"], cancelado=False, itens=[], observacao="",
                           descricao="Saldo trazido da planilha antiga"))
    itens = {}
    for i in con.execute("SELECT i.* FROM itens_venda i JOIN vendas v ON v.id = i.venda_id"
                         " WHERE v.cliente_id = ? ORDER BY i.id", (cliente_id,)):
        itens.setdefault(i["venda_id"], []).append(i)
    for v in con.execute("SELECT * FROM vendas WHERE cliente_id = ?", (cliente_id,)):
        its = itens.get(v["id"], [])
        linhas.append(dict(tipo="venda", id=v["id"], data=v["data"], criado_em=v["criado_em"],
                           valor=sum(i["quantidade"] * i["preco_unit"] for i in its),
                           cancelado=v["cancelada_em"] is not None, itens=its, observacao=v["observacao"],
                           descricao=", ".join(f'{i["quantidade"]}× {i["descricao"]}' for i in its)))
    for p in con.execute("SELECT * FROM pagamentos WHERE cliente_id = ?", (cliente_id,)):
        linhas.append(dict(tipo="pagamento", id=p["id"], data=p["data"], criado_em=p["criado_em"],
                           valor=p["valor"], cancelado=p["cancelado_em"] is not None, itens=[],
                           observacao=p["observacao"],
                           descricao="Pagamento" + (f' ({p["forma"]})' if p["forma"] else "")))
    ordem_tipo = {"inicial": 0, "venda": 1, "pagamento": 2}
    linhas.sort(key=lambda l: (l["data"], l["criado_em"], ordem_tipo[l["tipo"]]))
    saldo = 0
    for l in linhas:
        if not l["cancelado"]:
            saldo += -l["valor"] if l["tipo"] == "pagamento" else l["valor"]
        l["saldo_apos"] = saldo
    if saldo != c["saldo"]:
        raise RuntimeError(f"Extrato do cliente {cliente_id} não fecha: {saldo} != {c['saldo']}")
    return list(reversed(linhas))


# ---------------------------------------------------------------- produtos

def _limpar_codigo(con, codigo, ignorar_id=None):
    """'' -> None (sem código). Código repetido (sem diferença de maiúscula) é erro com o nome de quem já usa."""
    codigo = "".join((codigo or "").split()) or None
    if codigo:
        outro = con.execute("SELECT id, nome FROM produtos WHERE upper(codigo) = upper(?)", (codigo,)).fetchone()
        if outro and outro["id"] != ignorar_id:
            raise ErroDeCadastro(f'O código "{codigo}" já é do produto "{outro["nome"]}".')
    return codigo


def criar_produto(con, nome, preco, estoque=0, codigo="", marca="", tipo=""):
    nome = formatar_nome_produto(nome)
    if not nome:
        raise ErroDeCadastro("O nome do produto é obrigatório.")
    _checar_nome_livre(con, "produtos", nome)
    codigo = _limpar_codigo(con, codigo)
    with con:
        cur = con.execute("INSERT INTO produtos (nome, nome_busca, preco, estoque, codigo, marca, tipo)"
                          " VALUES (?, ?, ?, ?, ?, ?, ?)",
                          (nome, normalizar(nome), preco, estoque, codigo, formatar_nome(marca), formatar_nome(tipo)))
    return cur.lastrowid


def atualizar_produto(con, produto_id, nome, preco, ativo, estoque=None, codigo=None, marca=None, tipo=None):
    """estoque, codigo, marca e tipo = None mantêm o que está; codigo/marca/tipo = '' apagam."""
    nome = formatar_nome_produto(nome)
    if not nome:
        raise ErroDeCadastro("O nome do produto é obrigatório.")
    _checar_nome_livre(con, "produtos", nome, ignorar_id=produto_id)
    atual = produto(con, produto_id)
    codigo = atual["codigo"] if codigo is None else _limpar_codigo(con, codigo, ignorar_id=produto_id)
    marca = atual["marca"] if marca is None else formatar_nome(marca)
    tipo = atual["tipo"] if tipo is None else formatar_nome(tipo)
    with con:
        con.execute("UPDATE produtos SET nome=?, nome_busca=?, preco=?, ativo=?, estoque=COALESCE(?, estoque),"
                    " codigo=?, marca=?, tipo=? WHERE id=?",
                    (nome, normalizar(nome), preco, int(ativo), estoque, codigo, marca, tipo, produto_id))


def marcas(con):
    """Marcas já usadas (para sugerir no cadastro e filtrar)."""
    return [r[0] for r in con.execute("SELECT DISTINCT marca FROM produtos WHERE marca <> '' ORDER BY marca")]


def tipos(con):
    """Tipos já usados (perfume, hidratante...), para sugerir e filtrar."""
    return [r[0] for r in con.execute("SELECT DISTINCT tipo FROM produtos WHERE tipo <> '' ORDER BY tipo")]


# ---------------------------------------------------------------- produtos repetidos

SEMELHANCA_MINIMA = 0.86        # 0..1: quão parecidos os nomes precisam ser para perguntar


def _chave_produto(nome):
    """'Kaiak  100-ML!' -> 'kaiak 100 ml' (sem acento, maiúscula ou pontuação)."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", normalizar(nome)).split())


def nomes_parecidos(a, b):
    """True se dois nomes de produto parecem o mesmo produto digitado de jeitos diferentes.

    Números ou tamanhos diferentes nunca são o mesmo ('Batom 01' x 'Batom 02', '50 ml' x '100 ml',
    'Camiseta G' x 'Camiseta GG').
    """
    ka, kb = _chave_produto(a), _chave_produto(b)
    if not ka or not kb or re.findall(r"\d+", ka.replace(" ", "")) != re.findall(r"\d+", kb.replace(" ", "")):
        return False
    if sorted(w for w in ka.split() if w in TAMANHOS) != sorted(w for w in kb.split() if w in TAMANHOS):
        return False                                  # tamanhos diferentes: 'Camiseta G' x 'Camiseta GG'
    if sorted(w for w in ka.split() if w in CORES) != sorted(w for w in kb.split() if w in CORES):
        return False                                  # cores diferentes: 'Sutiã G Preto' x 'Sutiã G Bege' 
    if ka.replace(" ", "") == kb.replace(" ", "") or sorted(ka.split()) == sorted(kb.split()):
        return True                                   # só espaço ou ordem das palavras mudou
    if SequenceMatcher(None, ka, kb).ratio() >= SEMELHANCA_MINIMA:
        return True
    # ordem diferente + erro de digitação ('camisa regata gg' x 'gg camiza regata'): compara as palavras
    # em ordem alfabética, só com o mesmo número de palavras (uma palavra a mais, como uma cor, é outro produto)
    pa, pb = sorted(ka.split()), sorted(kb.split())
    return len(pa) == len(pb) and SequenceMatcher(None, " ".join(pa), " ".join(pb)).ratio() >= SEMELHANCA_MINIMA


def _mesma_marca_possivel(a, b):
    """Marcas diferentes = produtos diferentes; marca vazia num dos dois não impede."""
    return not a or not b or normalizar(a) == normalizar(b)


def produtos_parecidos(con, nome, marca="", ignorar_id=None):
    """Produtos já cadastrados que parecem ser este (para o aviso 'não quis dizer...?')."""
    return [p for p in produtos(con, apenas_ativos=False)
            if p["id"] != ignorar_id and _mesma_marca_possivel(p["marca"], marca) and nomes_parecidos(p["nome"], nome)]


def _pares_ignorados(con):
    return set(json.loads(ler_meta(con, "produtos_diferentes") or "[]"))


def marcar_nao_sao_iguais(con, a_id, b_id):
    """'Não são o mesmo': o par para de aparecer na lista de possíveis repetidos."""
    ignorados = _pares_ignorados(con)
    ignorados.add(f"{min(a_id, b_id)}-{max(a_id, b_id)}")
    gravar_meta(con, "produtos_diferentes", json.dumps(sorted(ignorados)))


def possiveis_repetidos(con):
    """Pares de produtos cadastrados que parecem o mesmo, menos os marcados como diferentes."""
    lista, ignorados = produtos(con, apenas_ativos=False), _pares_ignorados(con)
    pares = []
    for i, a in enumerate(lista):
        for b in lista[i + 1:]:
            if (f"{min(a['id'], b['id'])}-{max(a['id'], b['id'])}" not in ignorados
                    and _mesma_marca_possivel(a["marca"], b["marca"]) and nomes_parecidos(a["nome"], b["nome"])):
                pares.append((a, b))
    return pares


def mesclar_produtos(con, manter_id, juntar_id):
    """Junta o produto `juntar_id` dentro de `manter_id` e apaga o primeiro.

    - as vendas do produto juntado passam a contar no que fica (o histórico não se perde;
      cada venda continua mostrando o nome com que foi vendida);
    - os estoques somam;
    - tipo, marca e código vazios no que fica são preenchidos com os do outro;
    - nome e preço são os do que fica.
    """
    if manter_id == juntar_id:
        raise ErroDeCadastro("Escolha dois produtos diferentes.")
    fica, sai = produto(con, manter_id), produto(con, juntar_id)
    vendas_movidas = con.execute("SELECT COUNT(*) FROM itens_venda WHERE produto_id = ?", (juntar_id,)).fetchone()[0]
    with con:
        con.execute("UPDATE itens_venda SET produto_id = ? WHERE produto_id = ?", (manter_id, juntar_id))
        con.execute("UPDATE produtos SET codigo = NULL WHERE id = ?", (juntar_id,))     # libera o código (único)
        con.execute("UPDATE produtos SET estoque = estoque + ?, codigo = COALESCE(codigo, ?),"
                    " marca = CASE WHEN marca = '' THEN ? ELSE marca END,"
                    " tipo = CASE WHEN tipo = '' THEN ? ELSE tipo END, ativo = MAX(ativo, ?) WHERE id = ?",
                    (sai["estoque"], sai["codigo"], sai["marca"], sai["tipo"], sai["ativo"], manter_id))
        con.execute("DELETE FROM produtos WHERE id = ?", (juntar_id,))
    return dict(nome=fica["nome"], juntado=sai["nome"], vendas_movidas=vendas_movidas,
                estoque=produto(con, manter_id)["estoque"])


def adicionar_estoque(con, produto_id, quantidade):
    """Entrada de mercadoria: soma ao estoque atual."""
    produto(con, produto_id)
    if quantidade <= 0:
        raise ErroDeCadastro("Informe quantas unidades chegaram (maior que zero).")
    with con:
        con.execute("UPDATE produtos SET estoque = estoque + ? WHERE id = ?", (quantidade, produto_id))


def produto(con, produto_id):
    linha = con.execute("SELECT * FROM produtos WHERE id = ?", (produto_id,)).fetchone()
    if linha is None:
        raise ErroDeCadastro("Produto não encontrado.")
    return linha


def produtos(con, apenas_ativos=True):
    where = " WHERE ativo = 1" if apenas_ativos else ""
    return con.execute("SELECT * FROM produtos" + where + " ORDER BY ativo DESC, nome_busca").fetchall()


# ---------------------------------------------------------------- vendas e pagamentos

def registrar_venda(con, cliente_id, data, itens, observacao=""):
    """itens: lista de dicts com produto_id (ou None), descricao, quantidade, preco_unit (centavos)."""
    cliente(con, cliente_id)
    data = validar_data(data)
    if not itens:
        raise ErroDeCadastro("Adicione pelo menos um produto à venda.")
    limpos = []
    for it in itens:
        descricao, preco_tabela = limpar_nome(it.get("descricao")), None
        if it.get("produto_id") is not None:
            p = produto(con, it["produto_id"])
            descricao, preco_tabela = p["nome"], p["preco"]   # preço normal vem do cadastro, não da tela
        if not descricao:
            raise ErroDeCadastro("Todo item precisa de uma descrição.")
        qtd, preco = it.get("quantidade"), it.get("preco_unit")
        if not isinstance(qtd, int) or qtd < 1:
            raise ErroDeCadastro(f'Quantidade inválida em "{descricao}".')
        if not isinstance(preco, int) or preco < 0:
            raise ErroDeCadastro(f'Preço inválido em "{descricao}".')
        limpos.append((it.get("produto_id"), descricao, qtd, preco, preco_tabela))
    with con:
        venda_id = con.execute("INSERT INTO vendas (cliente_id, data, observacao) VALUES (?, ?, ?)",
                               (cliente_id, data, observacao.strip())).lastrowid
        con.executemany("INSERT INTO itens_venda (venda_id, produto_id, descricao, quantidade, preco_unit, preco_tabela)"
                        " VALUES (?, ?, ?, ?, ?, ?)", [(venda_id, *l) for l in limpos])
        con.executemany("UPDATE produtos SET estoque = estoque - ? WHERE id = ?",
                        [(qtd, pid) for pid, _, qtd, *_ in limpos if pid is not None])
    return venda_id


def registrar_pagamento(con, cliente_id, data, valor, forma="", observacao=""):
    cliente(con, cliente_id)
    data = validar_data(data)
    if not isinstance(valor, int):
        raise TypeError(f"valor do pagamento deve ser centavos (int), veio {valor!r}")
    if valor <= 0:
        raise ErroDeCadastro("O valor do pagamento precisa ser maior que zero.")
    with con:
        return con.execute("INSERT INTO pagamentos (cliente_id, data, valor, forma, observacao)"
                           " VALUES (?, ?, ?, ?, ?)",
                           (cliente_id, data, valor, forma, observacao.strip())).lastrowid


def cancelar_venda(con, venda_id):
    """Cancela e devolve ao estoque os produtos da venda."""
    with con:
        cur = con.execute("UPDATE vendas SET cancelada_em = datetime('now', 'localtime')"
                          " WHERE id = ? AND cancelada_em IS NULL", (venda_id,))
        if cur.rowcount != 1:
            raise ErroDeCadastro("Venda não encontrada ou já cancelada.")
        con.execute("UPDATE produtos SET estoque = estoque + (SELECT SUM(i.quantidade) FROM itens_venda i"
                    " WHERE i.venda_id = :v AND i.produto_id = produtos.id)"
                    " WHERE id IN (SELECT produto_id FROM itens_venda WHERE venda_id = :v)", {"v": venda_id})


def cancelar_pagamento(con, pagamento_id):
    with con:
        cur = con.execute("UPDATE pagamentos SET cancelado_em = datetime('now', 'localtime')"
                          " WHERE id = ? AND cancelado_em IS NULL", (pagamento_id,))
    if cur.rowcount != 1:
        raise ErroDeCadastro("Pagamento não encontrado ou já cancelado.")


def cliente_da_venda(con, venda_id):
    linha = con.execute("SELECT cliente_id FROM vendas WHERE id = ?", (venda_id,)).fetchone()
    if linha is None:
        raise ErroDeCadastro("Venda não encontrada.")
    return linha["cliente_id"]


def cliente_do_pagamento(con, pagamento_id):
    linha = con.execute("SELECT cliente_id FROM pagamentos WHERE id = ?", (pagamento_id,)).fetchone()
    if linha is None:
        raise ErroDeCadastro("Pagamento não encontrado.")
    return linha["cliente_id"]


# ---------------------------------------------------------------- relatórios

def resumo_mes(con, ano_mes):
    """ano_mes = 'AAAA-MM'. Total vendido e recebido no mês (sem cancelados)."""
    vendido = con.execute("SELECT COALESCE(SUM(i.quantidade * i.preco_unit), 0) FROM vendas v"
                          " JOIN itens_venda i ON i.venda_id = v.id"
                          " WHERE v.cancelada_em IS NULL AND substr(v.data, 1, 7) = ?", (ano_mes,)).fetchone()[0]
    recebido = con.execute("SELECT COALESCE(SUM(valor), 0) FROM pagamentos"
                           " WHERE cancelado_em IS NULL AND substr(data, 1, 7) = ?", (ano_mes,)).fetchone()[0]
    descontos = con.execute("SELECT COALESCE(SUM(i.quantidade * (i.preco_tabela - i.preco_unit)), 0) FROM vendas v"
                            " JOIN itens_venda i ON i.venda_id = v.id"
                            " WHERE v.cancelada_em IS NULL AND substr(v.data, 1, 7) = ?"
                            " AND i.preco_tabela > i.preco_unit", (ano_mes,)).fetchone()[0]
    return dict(vendido=vendido, recebido=recebido, descontos=descontos)


def movimento_clientes_mes(con, ano_mes):
    return con.execute("""
        SELECT c.id, c.nome, COALESCE(v.comprado, 0) AS comprado, COALESCE(p.pago, 0) AS pago
        FROM clientes c
        LEFT JOIN (SELECT ve.cliente_id, SUM(i.quantidade * i.preco_unit) AS comprado
                   FROM vendas ve JOIN itens_venda i ON i.venda_id = ve.id
                   WHERE ve.cancelada_em IS NULL AND substr(ve.data, 1, 7) = :m
                   GROUP BY ve.cliente_id) v ON v.cliente_id = c.id
        LEFT JOIN (SELECT cliente_id, SUM(valor) AS pago FROM pagamentos
                   WHERE cancelado_em IS NULL AND substr(data, 1, 7) = :m
                   GROUP BY cliente_id) p ON p.cliente_id = c.id
        WHERE v.comprado IS NOT NULL OR p.pago IS NOT NULL
        ORDER BY comprado DESC, pago DESC""", {"m": ano_mes}).fetchall()


MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro",
         "outubro", "novembro", "dezembro"]


def limites_mes(ano_mes):
    """'2026-11' -> ('2026-11-01', '2026-12-01'): o mês é data >= início e data < fim."""
    inicio = date.fromisoformat(ano_mes + "-01")
    fim = date(inicio.year + (inicio.month == 12), inicio.month % 12 + 1, 1)
    return inicio.isoformat(), fim.isoformat()


def nome_mes(ano_mes):
    return f"{MESES[int(ano_mes[5:7]) - 1]} de {ano_mes[:4]}"


# O saldo da planilha antiga conta como "saldo anterior" a partir do mês da importação
# (ele é o saldo de fechamento do mês anterior); antes disso o sistema não tem dados.
_SQL_RESUMO_CLIENTES = """
SELECT c.id, c.nome, c.ativo,
       (CASE WHEN c.saldo_inicial_data IS NULL OR c.saldo_inicial_data < :fim THEN c.saldo_inicial ELSE 0 END)
       + COALESCE((SELECT SUM(i.quantidade * i.preco_unit) FROM vendas v JOIN itens_venda i ON i.venda_id = v.id
                   WHERE v.cliente_id = c.id AND v.cancelada_em IS NULL AND v.data < :inicio), 0)
       - COALESCE((SELECT SUM(valor) FROM pagamentos p
                   WHERE p.cliente_id = c.id AND p.cancelado_em IS NULL AND p.data < :inicio), 0) AS anterior,
       COALESCE((SELECT SUM(i.quantidade * i.preco_unit) FROM vendas v JOIN itens_venda i ON i.venda_id = v.id
                 WHERE v.cliente_id = c.id AND v.cancelada_em IS NULL
                   AND v.data >= :inicio AND v.data < :fim), 0) AS comprou,
       COALESCE((SELECT SUM(valor) FROM pagamentos p
                 WHERE p.cliente_id = c.id AND p.cancelado_em IS NULL
                   AND p.data >= :inicio AND p.data < :fim), 0) AS pagou
FROM clientes c
"""


def resumo_clientes_mes(con, ano_mes, cliente_id=None):
    """Por cliente: saldo anterior, comprou e pagou no mês, saldo no fim do mês (as colunas da planilha antiga)."""
    inicio, fim = limites_mes(ano_mes)
    where = " WHERE c.id = :cliente" if cliente_id is not None else ""
    linhas = []
    for r in con.execute(_SQL_RESUMO_CLIENTES + where + " ORDER BY c.nome_busca",
                         {"inicio": inicio, "fim": fim, "cliente": cliente_id}):
        d = dict(r)
        d["final"] = d["anterior"] + d["comprou"] - d["pagou"]
        linhas.append(d)
    return linhas


def extrato_mes(con, cliente_id, ano_mes):
    """Extrato de um cliente num mês: saldo anterior, compras e pagamentos (sem cancelados), saldo final."""
    resumo = resumo_clientes_mes(con, ano_mes, cliente_id)
    if not resumo:
        raise ErroDeCadastro("Cliente não encontrado.")
    resumo = resumo[0]
    inicio, fim = limites_mes(ano_mes)
    linhas = [l for l in reversed(extrato(con, cliente_id))
              if l["tipo"] != "inicial" and not l["cancelado"] and inicio <= l["data"] < fim]
    saldo = resumo["anterior"]
    for l in linhas:
        saldo += -l["valor"] if l["tipo"] == "pagamento" else l["valor"]
        l["saldo_apos"] = saldo
    if saldo != resumo["final"]:
        raise RuntimeError(f"Extrato do mês {ano_mes} do cliente {cliente_id} não fecha: {saldo} != {resumo['final']}")
    return dict(resumo, linhas=linhas)


def valor_em_estoque(con):
    """Quanto o estoque rende pelo preço de venda: soma de quantidade x preço dos produtos com estoque.
    Conta também os desativados (as unidades existem); estoque negativo (vendeu sem ter) não entra."""
    r = con.execute("SELECT COALESCE(SUM(estoque * preco), 0), COALESCE(SUM(estoque), 0), COUNT(*)"
                    " FROM produtos WHERE estoque > 0").fetchone()
    return dict(valor=r[0], unidades=r[1], produtos=r[2])


def vendas_por_dia(con, inicio, fim):
    """{'AAAA-MM-DD': (total em centavos, número de vendas)} das vendas não canceladas com data em [inicio, fim)."""
    return {r["data"]: (r["total"], r["vendas"]) for r in con.execute("""
        SELECT v.data, SUM(i.quantidade * i.preco_unit) AS total, COUNT(DISTINCT v.id) AS vendas
        FROM vendas v JOIN itens_venda i ON i.venda_id = v.id
        WHERE v.cancelada_em IS NULL AND v.data >= ? AND v.data < ?
        GROUP BY v.data""", (inicio, fim))}


def vendas_por_produto_mes(con, ano_mes):
    """Produtos cadastrados agrupados pelo produto (mesmo se mudou de nome); avulsos pela descrição."""
    return con.execute("""
        SELECT i.produto_id, COALESCE(p.nome, i.descricao) AS descricao,
               SUM(i.quantidade) AS quantidade, SUM(i.quantidade * i.preco_unit) AS total
        FROM itens_venda i JOIN vendas v ON v.id = i.venda_id LEFT JOIN produtos p ON p.id = i.produto_id
        WHERE v.cancelada_em IS NULL AND substr(v.data, 1, 7) = ?
        GROUP BY COALESCE('p' || i.produto_id, 'a' || i.descricao) ORDER BY total DESC""", (ano_mes,)).fetchall()


def relatorio_produto(con, produto_id, ano):
    """Vendas de um produto no ano: por mês, quem mais comprou, descontos, e as últimas vendas (de sempre)."""
    p = produto(con, produto_id)
    base = """FROM itens_venda i JOIN vendas v ON v.id = i.venda_id JOIN clientes c ON c.id = v.cliente_id
              WHERE i.produto_id = :p AND v.cancelada_em IS NULL"""
    no_ano = base + " AND substr(v.data, 1, 4) = :ano"
    args = {"p": produto_id, "ano": str(ano)}
    por_mes = {r["mes"]: r for r in con.execute(
        "SELECT CAST(substr(v.data, 6, 2) AS INTEGER) AS mes, SUM(i.quantidade) AS quantidade,"
        " SUM(i.quantidade * i.preco_unit) AS total " + no_ano + " GROUP BY mes", args)}
    meses = [dict(numero=m, nome=MESES[m - 1], quantidade=por_mes[m]["quantidade"] if m in por_mes else 0,
                  total=por_mes[m]["total"] if m in por_mes else 0) for m in range(1, 13)]
    clientes = con.execute(
        "SELECT c.id, c.nome, SUM(i.quantidade) AS quantidade, SUM(i.quantidade * i.preco_unit) AS total "
        + no_ano + " GROUP BY c.id ORDER BY quantidade DESC, total DESC LIMIT 10", args).fetchall()
    descontos = con.execute(
        "SELECT COALESCE(SUM(i.quantidade * (i.preco_tabela - i.preco_unit)), 0) " + no_ano
        + " AND i.preco_tabela > i.preco_unit", args).fetchone()[0]
    ultimas = con.execute(
        "SELECT v.id AS venda_id, v.data, c.id AS cliente_id, c.nome, i.quantidade, i.preco_unit, i.preco_tabela "
        + base + " ORDER BY v.data DESC, v.id DESC LIMIT 15", args).fetchall()
    return dict(produto=p, ano=ano, meses=meses, clientes=clientes, ultimas=ultimas, descontos=descontos,
                quantidade=sum(m["quantidade"] for m in meses), total=sum(m["total"] for m in meses),
                maior_mes=max(m["total"] for m in meses))


def ultimos_lancamentos(con, limite=10):
    return con.execute("""
        SELECT * FROM (
            SELECT 'venda' AS tipo, v.data, v.criado_em, c.id AS cliente_id, c.nome,
                   (SELECT SUM(quantidade * preco_unit) FROM itens_venda WHERE venda_id = v.id) AS valor
            FROM vendas v JOIN clientes c ON c.id = v.cliente_id WHERE v.cancelada_em IS NULL
            UNION ALL
            SELECT 'pagamento', p.data, p.criado_em, c.id, c.nome, p.valor
            FROM pagamentos p JOIN clientes c ON c.id = p.cliente_id WHERE p.cancelado_em IS NULL
        ) ORDER BY criado_em DESC LIMIT ?""", (limite,)).fetchall()
