# Colocar o site na internet (PythonAnywhere)

Troque `SEU_USUARIO` pelo nome da sua conta. O endereço final fica `https://SEU_USUARIO.pythonanywhere.com`.

## 1. Baixar o código (aba **Consoles** → **Bash**)

```bash
git clone https://github.com/nadisfeu/controle-vendas-fiado.git
cd controle-vendas-fiado
python3 --version                                   # anote a versão (ex.: 3.13)
python3 -m venv ~/.venvs/vendas
~/.venvs/vendas/bin/pip install -r requirements.txt
```

## 2. Enviar os dois arquivos que NÃO estão no GitHub (aba **Files**)

Entre na pasta `controle-vendas-fiado` e use **Upload a file** para enviar:

- **`dados.db`** — o banco com os clientes;
- **`.env.producao`** — a configuração com a senha do site.

Depois, no console Bash:

```bash
cd ~/controle-vendas-fiado
mv .env.producao .env
chmod 600 .env dados.db                              # só a sua conta lê
```

## 3. Criar o site (aba **Web**)

1. **Add a new web app** → **Next** → **Manual configuration** → escolha **a mesma versão** de Python do passo 1.
2. Em **Virtualenv**, coloque: `/home/SEU_USUARIO/.venvs/vendas`
3. Em **Code**, clique no link do **WSGI configuration file**, apague tudo e deixe só:

   ```python
   import sys
   sys.path.insert(0, "/home/SEU_USUARIO/controle-vendas-fiado")
   from wsgi import application
   ```

   Salve (**Save**).
4. Em **Static files**: URL `/static/` → Directory `/home/SEU_USUARIO/controle-vendas-fiado/static`
5. Em **Security**: ligue **Force HTTPS**.
6. Clique no botão verde **Reload** no topo.

Abra `https://SEU_USUARIO.pythonanywhere.com` e entre com a senha do `.env`.

Se aparecer erro, veja o **Error log** (link na aba Web). O sistema escreve ali o motivo em português
(ex.: "banco não encontrado", "faltam no .env").

## Depois que estiver no ar

- **A partir de agora o banco que vale é o da internet.** Não lance mais vendas no computador: os dois
  bancos não se juntam sozinhos.
- **Renovar todo mês (plano grátis):** aba Web → **Run until 1 month from today**. Se esquecer, o site sai
  do ar, mas os dados continuam lá.
- **Cópias de segurança:** o sistema guarda uma cópia por dia em `backups/automaticas` (as últimas 14), mas
  elas ficam no mesmo servidor. **Baixe uma cópia para fora** (Relatórios → Baixar backup) pelo menos uma vez
  por semana; o Início avisa quando passar de 7 dias.
- **Atualizar o sistema** quando houver versão nova no GitHub:

  ```bash
  cd ~/controle-vendas-fiado && git pull && ~/.venvs/vendas/bin/pip install -r requirements.txt
  ```

  e depois **Reload** na aba Web.
