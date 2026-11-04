"""Entrada do site na hospedagem (PythonAnywhere ou qualquer servidor WSGI).

O arquivo de configuração WSGI do PythonAnywhere só precisa de:

    import sys
    sys.path.insert(0, "/home/SEU_USUARIO/controle-vendas-fiado")
    from wsgi import application

Lê o .env desta pasta. Falha alto (o erro aparece no "error log" da hospedagem) se faltar
configuração, se o banco não existir ou se a senha for fraca — melhor o site não abrir do que
abrir com um banco vazio ou desprotegido.
"""
import os
from pathlib import Path

from app import PASTA, ajustar_fuso, carregar_env, criar_app

SENHA_MINIMA = 12


def montar():
    carregar_env(PASTA / ".env")
    faltando = [n for n in ("APP_SENHA", "APP_CHAVE") if not os.environ.get(n)]
    if faltando:
        raise RuntimeError(f"faltam no .env: {', '.join(faltando)} (veja .env.exemplo)")
    if len(os.environ["APP_SENHA"]) < SENHA_MINIMA:
        raise RuntimeError(f"APP_SENHA muito curta para a internet: use pelo menos {SENHA_MINIMA} caracteres")
    if len(os.environ["APP_CHAVE"]) < 32:
        raise RuntimeError("APP_CHAVE muito curta: gere com  python -c \"import secrets; print(secrets.token_hex(32))\"")
    banco = Path(os.environ.get("APP_BANCO") or PASTA / "dados.db")
    if not banco.exists():
        raise RuntimeError(f"banco não encontrado em {banco}: envie o dados.db para esta pasta "
                           "(o site não cria um banco vazio sozinho na hospedagem)")
    ajustar_fuso()
    return criar_app(banco=str(banco), senha=os.environ["APP_SENHA"], chave=os.environ["APP_CHAVE"],
                     cookie_seguro=True,
                     minutos_inativo=int(os.environ.get("APP_MINUTOS_INATIVO") or 60),
                     pasta_copias=os.environ.get("APP_PASTA_COPIAS") or str(PASTA / "backups" / "automaticas"))


application = montar()
