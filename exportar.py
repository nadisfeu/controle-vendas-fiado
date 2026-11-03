"""Exporta tudo para uma planilha Excel (serve de backup legível e para abrir no Excel)."""
import io

from openpyxl import Workbook
from openpyxl.styles import Font

import db

FORMATO_REAIS = 'R$ #,##0.00'


def _aba(wb, titulo, cabecalho, linhas, colunas_reais):
    ws = wb.create_sheet(titulo)
    ws.append(cabecalho)
    for celula in ws[1]:
        celula.font = Font(bold=True)
    for linha in linhas:
        ws.append(linha)
    for col in colunas_reais:
        for celula in ws.iter_cols(min_col=col, max_col=col, min_row=2).__next__():
            celula.number_format = FORMATO_REAIS
    for i, _ in enumerate(cabecalho, start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = 18
    ws.column_dimensions["A"].width = 32
    ws.freeze_panes = "A2"


def gerar_planilha(con):
    wb = Workbook()
    wb.remove(wb.active)
    r = lambda centavos: centavos / 100

    _aba(wb, "Clientes",
         ["Cliente", "Telefone", "Saldo da planilha antiga", "Comprado (no sistema)", "Pago (no sistema)",
          "Saldo atual", "Última compra", "Último pagamento", "Ativo", "Observação"],
         [[c["nome"], c["telefone"], r(c["saldo_inicial"]), r(c["comprado"]), r(c["pago"]), r(c["saldo"]),
           c["ultima_compra"] or "", c["ultimo_pagamento"] or "", "sim" if c["ativo"] else "não",
           c["observacao"]] for c in db.saldos(con, apenas_ativos=False)],
         colunas_reais=[3, 4, 5, 6])

    _aba(wb, "Vendas",
         ["Cliente", "Data", "Venda nº", "Item", "Quantidade", "Preço normal", "Vendido por (cada)",
          "Desconto", "Total do item", "Cancelada"],
         [[l["nome"], l["data"], l["venda_id"], l["descricao"], l["quantidade"],
           r(l["preco_tabela"]) if l["preco_tabela"] is not None else "", r(l["preco_unit"]),
           r(l["quantidade"] * max(0, l["preco_tabela"] - l["preco_unit"])) if l["preco_tabela"] is not None else "",
           r(l["quantidade"] * l["preco_unit"]), "sim" if l["cancelada_em"] else ""]
          for l in con.execute("SELECT c.nome, v.data, v.id AS venda_id, v.cancelada_em, i.descricao,"
                               " i.quantidade, i.preco_unit, i.preco_tabela FROM itens_venda i"
                               " JOIN vendas v ON v.id = i.venda_id JOIN clientes c ON c.id = v.cliente_id"
                               " ORDER BY v.data, v.id, i.id")],
         colunas_reais=[6, 7, 8, 9])

    _aba(wb, "Pagamentos",
         ["Cliente", "Data", "Valor", "Forma", "Observação", "Cancelado"],
         [[l["nome"], l["data"], r(l["valor"]), l["forma"], l["observacao"], "sim" if l["cancelado_em"] else ""]
          for l in con.execute("SELECT c.nome, p.* FROM pagamentos p JOIN clientes c ON c.id = p.cliente_id"
                               " ORDER BY p.data, p.id")],
         colunas_reais=[3])

    _aba(wb, "Produtos", ["Produto", "Preço atual", "Estoque", "Ativo"],
         [[p["nome"], r(p["preco"]), p["estoque"], "sim" if p["ativo"] else "não"]
          for p in db.produtos(con, apenas_ativos=False)],
         colunas_reais=[2])

    saida = io.BytesIO()
    wb.save(saida)
    saida.seek(0)
    return saida
