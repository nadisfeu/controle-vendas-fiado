"""Site de controle de vendas fiado: clientes, produtos, vendas, pagamentos e saldos.

Rodar:  .venv/bin/python app.py      (lê APP_SENHA e APP_CHAVE do arquivo .env)
"""
import hmac
import io
import json
import os
import secrets
import sys
import time
from datetime import date, datetime
from functools import wraps
from pathlib import Path

from flask import (Flask, abort, flash, g, redirect, render_template, request, send_file, session,
                   url_for)

import db
import graficos
from db import ErroDeCadastro

PASTA = Path(__file__).resolve().parent
OPCOES_DIAS_SEM_PAGAR = (30, 60, 90, 180)
DIAS_SEM_PAGAR_PADRAO = 60
MAX_TENTATIVAS_SENHA = 5            # erros seguidos de senha antes de bloquear...
BLOQUEIO_SEGUNDOS = 15 * 60         # ...por 15 minutos (por endereço de internet)
DIAS_AVISO_BACKUP = 7               # aviso no Início se ninguém baixou cópia há 7 dias ou mais
COPIAS_AUTOMATICAS = 14             # cópias diárias guardadas no servidor
MINUTOS_INATIVO_PADRAO = 60         # sem usar por esse tempo, pede a senha de novo


def ajustar_fuso():
    """Servidores costumam rodar em UTC: sem isso, venda lançada às 22h sai com a data de amanhã."""
    os.environ["TZ"] = os.environ.get("APP_FUSO", "America/Sao_Paulo")
    time.tzset()


def carregar_env(caminho):
    """Lê um .env simples (CHAVE=valor por linha) sem sobrescrever o que já está no ambiente."""
    if not caminho.exists():
        return
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if linha and not linha.startswith("#") and "=" in linha:
            chave, valor = linha.split("=", 1)
            os.environ.setdefault(chave.strip(), valor.strip())


def exigir_env(nome):
    valor = os.environ.get(nome)
    if not valor:
        sys.exit(f"ERRO: a variável {nome} não está definida. Copie .env.exemplo para .env e preencha.")
    return valor


def criar_app(banco, senha, chave, cookie_seguro=False, csrf=True, pasta_copias=None,
              minutos_inativo=MINUTOS_INATIVO_PADRAO):
    """cookie_seguro: só manda o login por HTTPS (ligar na internet).
    csrf: exige o código do formulário em todo POST (só os testes de regra desligam).
    pasta_copias: onde guardar a cópia automática diária do banco (None = não faz).
    minutos_inativo: tempo sem uso até pedir a senha de novo. O login também não é guardado:
    fechar o navegador desconecta (e o limite de tempo cobre o navegador que "restaura" a sessão)."""
    app = Flask(__name__)
    app.config.update(SECRET_KEY=chave, BANCO=banco, SENHA=senha, CSRF=csrf,
                      SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SECURE=cookie_seguro)
    tentativas_erradas = {}          # endereço -> horários dos erros de senha recentes

    app.jinja_env.filters["reais"] = db.centavos_para_reais
    app.jinja_env.filters["campo"] = db.centavos_para_campo
    app.jinja_env.filters["mes_por_extenso"] = lambda am: db.nome_mes(am) if am else ""
    app.jinja_env.filters["data_br"] = lambda s: f"{s[8:10]}/{s[5:7]}/{s[0:4]}" if s and len(s) >= 10 else ""

    def con():
        if "con" not in g:
            g.con = db.conectar(app.config["BANCO"])
        return g.con

    @app.teardown_appcontext
    def fechar(_erro):
        c = g.pop("con", None)
        if c is not None:
            c.close()

    # ------------------------------------------------------------ segurança

    def csrf_token():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return session["csrf"]
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def checar_csrf():
        """Todo formulário leva um código secreto da sessão: outro site não consegue enviar por ela."""
        if request.method == "POST" and app.config["CSRF"]:
            enviado, esperado = request.form.get("csrf", ""), session.get("csrf", "")
            if not esperado or not hmac.compare_digest(enviado, esperado):
                abort(400)

    @app.errorhandler(400)
    def formulario_expirado(_erro):
        return render_template("erro.html", titulo="Formulário expirado",
                               mensagem="Esta página ficou aberta por muito tempo ou veio de outro lugar. "
                                        "Volte, recarregue a página e tente de novo."), 400

    @app.after_request
    def cabecalhos_de_seguranca(resposta):
        resposta.headers["X-Content-Type-Options"] = "nosniff"
        resposta.headers["X-Frame-Options"] = "DENY"
        resposta.headers["Referrer-Policy"] = "same-origin"
        if not request.path.startswith("/static/"):
            resposta.headers["Cache-Control"] = "no-store"     # dados de clientes não ficam no cache
        if app.config["SESSION_COOKIE_SECURE"]:
            resposta.headers["Strict-Transport-Security"] = "max-age=31536000"
        return resposta

    def endereco():
        # no PythonAnywhere o endereço real de quem acessa vem neste cabeçalho
        return request.headers.get("X-Real-IP") or request.remote_addr or "?"

    # ------------------------------------------------------------ cópia automática

    @app.before_request
    def copia_do_dia():
        """Na primeira visita do dia, guarda uma cópia do banco no servidor (fica as últimas 14).

        Não substitui baixar a cópia para fora do servidor: se a conta sumir, estas somem junto.
        Se falhar, o site continua funcionando e o Início mostra o aviso.
        """
        if not pasta_copias or request.path.startswith("/static/"):
            return
        destino = Path(pasta_copias) / f"copia_{date.today().isoformat()}.db"
        if destino.exists():
            return
        try:
            db.copiar_banco(con(), destino)
            antigas = sorted(Path(pasta_copias).glob("copia_*.db"))[:-COPIAS_AUTOMATICAS]
            for velha in antigas:
                velha.unlink()
            db.gravar_meta(con(), "erro_copia_automatica", "")
        except Exception as e:      # registra e avisa, mas não derruba a venda que ela está lançando
            app.logger.exception("cópia automática falhou")
            db.gravar_meta(con(), "erro_copia_automatica", f"{date.today().isoformat()}: {e}")

    def marca_da_senha():
        """Muda quando a senha muda: trocar APP_SENHA derruba todo login feito com a senha antiga."""
        return hmac.new(app.config["SECRET_KEY"].encode(), app.config["SENHA"].encode(), "sha256").hexdigest()[:32]

    def login_obrigatorio(f):
        @wraps(f)
        def envolvido(*a, **kw):
            agora_s = time.time()
            logado = (session.get("logado")
                      and hmac.compare_digest(session.get("senha_marca", ""), marca_da_senha())
                      and agora_s - session.get("ultimo_uso", 0) < minutos_inativo * 60)
            if not logado:
                expirou = bool(session.get("logado"))
                session.clear()
                if expirou:
                    flash("Por segurança, entre de novo (ficou muito tempo sem uso).", "erro")
                return redirect(url_for("entrar", proximo=request.path))
            session["ultimo_uso"] = agora_s
            return f(*a, **kw)
        return envolvido

    def hoje():
        return date.today().isoformat()

    def agora():
        return datetime.now().strftime("%d/%m/%Y às %H:%M")

    # ------------------------------------------------------------ login

    @app.route("/entrar", methods=["GET", "POST"])
    def entrar():
        erro = None
        if request.method == "POST":
            agora_s, quem = time.monotonic(), endereco()
            erros = [t for t in tentativas_erradas.get(quem, []) if agora_s - t < BLOQUEIO_SEGUNDOS]
            tentativas_erradas[quem] = erros
            if len(erros) >= MAX_TENTATIVAS_SENHA:
                espera = int((BLOQUEIO_SEGUNDOS - (agora_s - erros[0])) // 60) + 1
                return render_template("entrar.html", erro=f"Muitas tentativas com senha errada. "
                                                           f"Espere {espera} minuto(s) e tente de novo."), 429
            if hmac.compare_digest(request.form.get("senha", "").encode(), app.config["SENHA"].encode()):
                tentativas_erradas.pop(quem, None)
                session.clear()
                session["logado"] = True
                session["senha_marca"] = marca_da_senha()
                session["ultimo_uso"] = time.time()
                session.permanent = False        # cookie some quando o navegador fecha
                proximo = request.args.get("proximo", "")
                return redirect(proximo if proximo.startswith("/") and not proximo.startswith("//") else "/")
            erros.append(agora_s)
            erro = "Senha incorreta."
        return render_template("entrar.html", erro=erro)

    @app.route("/sair")
    def sair():
        session.clear()
        return redirect(url_for("entrar"))

    # ------------------------------------------------------------ início

    @app.route("/")
    @login_obrigatorio
    def inicio():
        # inclui inativos: desativar o cliente não pode fazer a dívida dele sumir do total
        todos = db.saldos(con(), apenas_ativos=False)
        devendo = sorted((c for c in todos if c["saldo"] > 0), key=lambda c: -c["saldo"])
        mes = hoje()[:7]
        atrasados = db.clientes_sem_pagar(con(), date.today(), DIAS_SEM_PAGAR_PADRAO)
        ultimo_backup = db.ler_meta(con(), "ultimo_backup_baixado")
        dias_sem_backup = (date.today() - date.fromisoformat(ultimo_backup[:10])).days if ultimo_backup else None
        return render_template("inicio.html",
                               dias_sem_backup=dias_sem_backup, aviso_backup=dias_sem_backup is None
                               or dias_sem_backup >= DIAS_AVISO_BACKUP,
                               erro_copia=db.ler_meta(con(), "erro_copia_automatica"),
                               total_receber=sum(c["saldo"] for c in devendo),
                               qtd_devendo=len(devendo), qtd_clientes=sum(1 for c in todos if c["ativo"]),
                               atrasados=atrasados, atrasados_total=sum(c["saldo"] for c in atrasados),
                               dias_atraso=DIAS_SEM_PAGAR_PADRAO,
                               maiores=devendo[:10], resumo=db.resumo_mes(con(), mes), mes=mes,
                               ultimos=db.ultimos_lancamentos(con(), 10))

    # ------------------------------------------------------------ clientes

    @app.route("/clientes")
    @login_obrigatorio
    def clientes():
        mostrar = request.args.get("mostrar") or ("todos" if request.args.get("inativos") == "1" else "ativos")
        if mostrar not in ("ativos", "desativados", "todos"):
            mostrar = "ativos"
        lista = db.saldos(con(), apenas_ativos=False)
        if mostrar != "todos":
            lista = [c for c in lista if bool(c["ativo"]) == (mostrar == "ativos")]
        return render_template("clientes.html", clientes=lista, mostrar=mostrar)

    @app.route("/clientes/sem-pagar")
    @login_obrigatorio
    def clientes_sem_pagar():
        try:
            dias = int(request.args.get("dias", DIAS_SEM_PAGAR_PADRAO))
        except ValueError:
            dias = DIAS_SEM_PAGAR_PADRAO
        if dias not in OPCOES_DIAS_SEM_PAGAR:
            dias = DIAS_SEM_PAGAR_PADRAO
        lista = db.clientes_sem_pagar(con(), date.today(), dias)
        return render_template("sem_pagar.html", lista=lista, dias=dias, opcoes=OPCOES_DIAS_SEM_PAGAR,
                               total=sum(c["saldo"] for c in lista))

    @app.route("/clientes/novo", methods=["GET", "POST"])
    @login_obrigatorio
    def cliente_novo():
        f = request.form
        if request.method == "POST":
            try:
                novo_id = db.criar_cliente(con(), f.get("nome", ""), f.get("telefone", ""), f.get("observacao", ""))
                flash(f'Cliente "{db.limpar_nome(f["nome"])}" cadastrado.')
                return redirect(url_for("cliente_ver", cliente_id=novo_id))
            except ErroDeCadastro as e:
                flash(str(e), "erro")
        return render_template("cliente_form.html", c=None, form=f)

    @app.route("/clientes/<int:cliente_id>")
    @login_obrigatorio
    def cliente_ver(cliente_id):
        try:
            c = db.cliente(con(), cliente_id)
        except ErroDeCadastro:
            abort(404)
        return render_template("cliente.html", c=c, extrato=db.extrato(con(), cliente_id))

    @app.route("/clientes/<int:cliente_id>/editar", methods=["GET", "POST"])
    @login_obrigatorio
    def cliente_editar(cliente_id):
        try:
            c = db.cliente(con(), cliente_id)
        except ErroDeCadastro:
            abort(404)
        f = request.form
        if request.method == "POST":
            try:
                db.atualizar_cliente(con(), cliente_id, f.get("nome", ""), f.get("telefone", ""),
                                     f.get("observacao", ""),
                                     db.reais_para_centavos(f.get("saldo_inicial") or "0"),
                                     f.get("ativo") == "1")
                flash("Cadastro atualizado.")
                return redirect(url_for("cliente_ver", cliente_id=cliente_id))
            except ErroDeCadastro as e:
                flash(str(e), "erro")
        return render_template("cliente_form.html", c=c, form=f)

    @app.route("/clientes/<int:cliente_id>/venda", methods=["GET", "POST"])
    @login_obrigatorio
    def venda_nova(cliente_id):
        try:
            c = db.cliente(con(), cliente_id)
        except ErroDeCadastro:
            abort(404)
        carrinho = []
        if request.method == "POST":
            try:
                carrinho = json.loads(request.form.get("itens") or "[]")
                itens = [dict(produto_id=it.get("produto_id"), descricao=it.get("descricao", ""),
                              quantidade=int(it.get("quantidade", 0)),
                              preco_unit=db.reais_para_centavos(str(it.get("preco", ""))))
                         for it in carrinho]
                db.registrar_venda(con(), cliente_id, request.form.get("data", ""), itens,
                                   request.form.get("observacao", ""))
                total = sum(i["quantidade"] * i["preco_unit"] for i in itens)
                desconto = sum(i["quantidade"] * max(0, db.produto(con(), i["produto_id"])["preco"] - i["preco_unit"])
                               for i in itens if i["produto_id"] is not None)
                novo = db.cliente(con(), cliente_id)
                flash(f'Venda de {db.centavos_para_reais(total)} registrada para {c["nome"]}'
                      + (f' (com desconto de {db.centavos_para_reais(desconto)})' if desconto else '')
                      + f'. Saldo agora: {db.centavos_para_reais(novo["saldo"])}.')
                vendidos = {i["produto_id"] for i in itens if i["produto_id"] is not None}
                acabando = [db.produto(con(), pid) for pid in sorted(vendidos)]
                acabando = [p for p in acabando if p["estoque"] <= 0]
                if acabando:
                    flash("Atenção, estoque: " + "; ".join(
                        f'{p["nome"]} acabou' if p["estoque"] == 0 else f'{p["nome"]} ficou com {p["estoque"]} (vendeu sem ter)'
                        for p in acabando) + ".", "erro")
                return redirect(url_for("cliente_ver", cliente_id=cliente_id))
            except (ErroDeCadastro, ValueError, TypeError, AttributeError) as e:
                flash(str(e) if isinstance(e, ErroDeCadastro) else "Não consegui ler os itens da venda.", "erro")
        lista = [dict(id=p["id"], nome=p["nome"], preco=db.centavos_para_campo(p["preco"]), estoque=p["estoque"],
                      codigo=p["codigo"] or "", marca=p["marca"], tipo=p["tipo"])
                 for p in db.produtos(con())]
        return render_template("venda.html", c=c, produtos=lista, carrinho=carrinho,
                               marcas=sorted({p["marca"] for p in lista if p["marca"]}),
                               tipos=sorted({p["tipo"] for p in lista if p["tipo"]}),
                               data=request.form.get("data") or hoje(),
                               observacao=request.form.get("observacao", ""))

    @app.route("/clientes/<int:cliente_id>/pagamento", methods=["GET", "POST"])
    @login_obrigatorio
    def pagamento_novo(cliente_id):
        try:
            c = db.cliente(con(), cliente_id)
        except ErroDeCadastro:
            abort(404)
        f = request.form
        if request.method == "POST":
            try:
                valor = db.reais_para_centavos(f.get("valor", ""))
                db.registrar_pagamento(con(), cliente_id, f.get("data", ""), valor,
                                       f.get("forma", ""), f.get("observacao", ""))
                novo = db.cliente(con(), cliente_id)
                flash(f'Pagamento de {db.centavos_para_reais(valor)} registrado para {c["nome"]}. '
                      f'Saldo agora: {db.centavos_para_reais(novo["saldo"])}.')
                return redirect(url_for("cliente_ver", cliente_id=cliente_id))
            except ErroDeCadastro as e:
                flash(str(e), "erro")
        return render_template("pagamento.html", c=c, form=f, data=f.get("data") or hoje(),
                               formas=db.FORMAS_PAGAMENTO)

    @app.route("/vendas/<int:venda_id>/cancelar", methods=["POST"])
    @login_obrigatorio
    def venda_cancelar(venda_id):
        try:
            cliente_id = db.cliente_da_venda(con(), venda_id)
            db.cancelar_venda(con(), venda_id)
            flash("Venda cancelada. Ela continua no histórico, riscada, e não conta mais no saldo.")
        except ErroDeCadastro as e:
            flash(str(e), "erro")
            return redirect(url_for("clientes"))
        return redirect(url_for("cliente_ver", cliente_id=cliente_id))

    @app.route("/pagamentos/<int:pagamento_id>/cancelar", methods=["POST"])
    @login_obrigatorio
    def pagamento_cancelar(pagamento_id):
        try:
            cliente_id = db.cliente_do_pagamento(con(), pagamento_id)
            db.cancelar_pagamento(con(), pagamento_id)
            flash("Pagamento cancelado. Ele continua no histórico, riscado, e não conta mais no saldo.")
        except ErroDeCadastro as e:
            flash(str(e), "erro")
            return redirect(url_for("clientes"))
        return redirect(url_for("cliente_ver", cliente_id=cliente_id))

    # ------------------------------------------------------------ produtos

    @app.route("/produtos", methods=["GET", "POST"])
    @login_obrigatorio
    def produtos():
        f = request.form
        sugestoes = []
        if request.method == "POST":
            try:
                preco, estoque = db.reais_para_centavos(f.get("preco", "")), db.ler_quantidade(f.get("estoque"))
                # parece com um que já existe? pergunta antes de criar outro ("não quis dizer...?")
                if f.get("confirmar_novo") != "1":
                    sugestoes = db.produtos_parecidos(con(), f.get("nome", ""), f.get("marca", ""))
                if not sugestoes:
                    db.criar_produto(con(), f.get("nome", ""), preco, estoque, f.get("codigo", ""), f.get("marca", ""),
                                     f.get("tipo", ""))
                    flash(f'Produto "{db.formatar_nome_produto(f["nome"])}" cadastrado.')
                    return redirect(url_for("produtos"))
            except ErroDeCadastro as e:
                flash(str(e), "erro")
        return render_template("produtos.html", produtos=db.produtos(con(), apenas_ativos=False), form=f,
                               marcas=db.marcas(con()), tipos=db.tipos(con()), sugestoes=sugestoes,
                               repetidos=db.possiveis_repetidos(con()))

    @app.route("/produtos/<int:produto_id>/somar", methods=["POST"])
    @login_obrigatorio
    def produto_somar(produto_id):
        """'Sim, é esse': em vez de cadastrar de novo, soma a quantidade digitada ao produto que já existe."""
        try:
            quantidade = db.ler_quantidade(request.form.get("estoque"))
            p = db.produto(con(), produto_id)
            if quantidade:
                db.adicionar_estoque(con(), produto_id, quantidade)
                flash(f'Não cadastrei de novo: "{p["nome"]}" já existia. Somei {quantidade} ao estoque dele.')
            else:
                flash(f'Não cadastrei de novo: "{p["nome"]}" já existia.')
        except ErroDeCadastro as e:
            flash(str(e), "erro")
            return redirect(url_for("produtos"))
        return redirect(url_for("produto_ver", produto_id=produto_id))

    @app.route("/produtos/juntar", methods=["GET", "POST"])
    @login_obrigatorio
    def produtos_juntar():
        try:
            a, b = int(request.values.get("a", "")), int(request.values.get("b", ""))
            pa, pb = db.produto(con(), a), db.produto(con(), b)
        except (ValueError, ErroDeCadastro):
            abort(404)
        if request.method == "POST":
            fica = int(request.form.get("fica", 0))
            if fica not in (a, b):
                flash("Escolha qual dos dois produtos fica.", "erro")
            else:
                try:
                    r = db.mesclar_produtos(con(), fica, b if fica == a else a)
                    flash(f'Pronto: "{r["juntado"]}" foi juntado em "{r["nome"]}". '
                          f'Estoque agora: {r["estoque"]}. Vendas passadas para ele: {r["vendas_movidas"]}.')
                    return redirect(url_for("produto_ver", produto_id=fica))
                except ErroDeCadastro as e:
                    flash(str(e), "erro")
        vendidos = {pid: con().execute("SELECT COALESCE(SUM(quantidade), 0) FROM itens_venda i JOIN vendas v"
                                       " ON v.id = i.venda_id WHERE i.produto_id = ? AND v.cancelada_em IS NULL",
                                       (pid,)).fetchone()[0] for pid in (a, b)}
        # sugere manter o mais vendido; no empate, o cadastrado primeiro (costuma ser o original)
        sugerido = max((pa, pb), key=lambda p: (vendidos[p["id"]], -p["id"]))["id"]
        return render_template("produtos_juntar.html", a=pa, b=pb, vendidos=vendidos, sugerido=sugerido)

    @app.route("/produtos/diferentes", methods=["POST"])
    @login_obrigatorio
    def produtos_diferentes():
        try:
            db.marcar_nao_sao_iguais(con(), int(request.form["a"]), int(request.form["b"]))
            flash("Certo, esses dois não vão mais aparecer como repetidos.")
        except (KeyError, ValueError):
            abort(400)
        return redirect(url_for("produtos"))

    @app.route("/produtos/<int:produto_id>")
    @login_obrigatorio
    def produto_ver(produto_id):
        try:
            ano = int(request.args.get("ano") or hoje()[:4])
            r = db.relatorio_produto(con(), produto_id, ano)
        except (ErroDeCadastro, ValueError):
            abort(404)
        return render_template("produto.html", r=r, p=r["produto"], ano=ano)

    @app.route("/produtos/<int:produto_id>/editar", methods=["GET", "POST"])
    @login_obrigatorio
    def produto_editar(produto_id):
        try:
            p = db.produto(con(), produto_id)
        except ErroDeCadastro:
            abort(404)
        f = request.form
        if request.method == "POST":
            try:
                entrada = db.ler_quantidade(f.get("entrada"))
                db.atualizar_produto(con(), produto_id, f.get("nome", ""),
                                     db.reais_para_centavos(f.get("preco", "")), f.get("ativo") == "1",
                                     db.ler_quantidade(f.get("estoque"), permitir_negativo=True),
                                     codigo=f.get("codigo", ""), marca=f.get("marca", ""), tipo=f.get("tipo", ""))
                if entrada:
                    db.adicionar_estoque(con(), produto_id, entrada)
                p = db.produto(con(), produto_id)
                flash(f'Produto atualizado. Estoque de {p["nome"]}: {p["estoque"]}.')
                return redirect(url_for("produto_ver", produto_id=produto_id))
            except ErroDeCadastro as e:
                flash(str(e), "erro")
        return render_template("produto_form.html", p=p, form=f, marcas=db.marcas(con()), tipos=db.tipos(con()))

    # ------------------------------------------------------------ relatórios e backup

    def mes_pedido():
        mes = request.args.get("mes") or hoje()[:7]
        try:
            db.validar_data(mes + "-01")
        except ErroDeCadastro:
            mes = hoje()[:7]
        return mes

    @app.route("/relatorios/imprimir")
    @login_obrigatorio
    def imprimir_mes():
        mes = mes_pedido()
        mostrar = "todos" if request.args.get("mostrar") == "todos" else "movimento"
        todos = db.resumo_clientes_mes(con(), mes)
        if mostrar == "todos":
            linhas = [c for c in todos if c["comprou"] or c["pagou"] or c["final"]]
        else:
            linhas = [c for c in todos if c["comprou"] or c["pagou"]]
        totais = {k: sum(c[k] for c in todos) for k in ("anterior", "comprou", "pagou", "final")}
        return render_template("imprimir_mes.html", mes=mes, nome_mes=db.nome_mes(mes), linhas=linhas,
                               totais=totais, mostrar=mostrar,
                               a_receber=sum(c["final"] for c in todos if c["final"] > 0),
                               creditos=sum(-c["final"] for c in todos if c["final"] < 0),
                               qtd_devendo=sum(1 for c in todos if c["final"] > 0),
                               produtos=db.vendas_por_produto_mes(con(), mes), agora=agora())

    @app.route("/clientes/<int:cliente_id>/imprimir")
    @login_obrigatorio
    def imprimir_cliente(cliente_id):
        mes = mes_pedido()
        try:
            c = db.cliente(con(), cliente_id)
        except ErroDeCadastro:
            abort(404)
        return render_template("imprimir_cliente.html", c=c, mes=mes, nome_mes=db.nome_mes(mes),
                               e=db.extrato_mes(con(), cliente_id, mes), agora=agora())

    @app.route("/relatorios")
    @login_obrigatorio
    def relatorios():
        mes = mes_pedido()
        dia_de_hoje = date.today()
        return render_template("relatorios.html", mes=mes, nome_mes=db.nome_mes(mes), resumo=db.resumo_mes(con(), mes),
                               grafico=graficos.grafico_mes(con(), mes, dia_de_hoje),
                               mapa=graficos.mapa_ano(con(), int(mes[:4]), dia_de_hoje),
                               clientes=db.movimento_clientes_mes(con(), mes),
                               produtos=db.vendas_por_produto_mes(con(), mes))

    @app.route("/exportar.xlsx")
    @login_obrigatorio
    def exportar():
        from exportar import gerar_planilha
        arquivo = gerar_planilha(con())
        db.gravar_meta(con(), "ultimo_backup_baixado", datetime.now().isoformat(timespec="seconds"))
        return send_file(arquivo, as_attachment=True, download_name=f"vendas_{hoje()}.xlsx",
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    @app.route("/backup.db")
    @login_obrigatorio
    def backup():
        db.gravar_meta(con(), "ultimo_backup_baixado", datetime.now().isoformat(timespec="seconds"))
        return send_file(io.BytesIO(con().serialize()), as_attachment=True,
                         download_name=f"backup_vendas_{hoje()}.db", mimetype="application/octet-stream")

    return app


if __name__ == "__main__":
    carregar_env(PASTA / ".env")
    ajustar_fuso()
    app = criar_app(banco=os.environ.get("APP_BANCO") or str(PASTA / "dados.db"),
                    senha=exigir_env("APP_SENHA"), chave=exigir_env("APP_CHAVE"),
                    pasta_copias=os.environ.get("APP_PASTA_COPIAS") or str(PASTA / "backups" / "automaticas"))
    app.jinja_env.auto_reload = True   # mudança nas telas aparece sem reiniciar
    app.run(host="127.0.0.1", port=int(os.environ.get("APP_PORTA", "5000")))
