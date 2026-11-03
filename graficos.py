"""Números dos gráficos de Relatórios: vendas por dia do mês e mapa de calor do ano (dia × mês).

Só prepara os dados; quem desenha é templates/relatorios.html + static/graficos.js.
Dias que ainda não chegaram ficam marcados como "futuro" para não parecerem dias sem venda.
"""
import calendar
import math
from datetime import date

import db

DIAS_SEMANA = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]
MESES_CURTOS = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
NIVEIS = 5   # tons do mapa de calor (static/estilo.css: --calor-1 .. --calor-5)


def grafico_mes(con, ano_mes, hoje):
    """Um item por dia do mês: dia, dia da semana, total vendido, nº de vendas, se é futuro."""
    ano, mes = int(ano_mes[:4]), int(ano_mes[5:7])
    inicio, fim = db.limites_mes(ano_mes)
    por_dia = db.vendas_por_dia(con, inicio, fim)
    dias = []
    for d in range(1, calendar.monthrange(ano, mes)[1] + 1):
        data = date(ano, mes, d)
        total, vendas = por_dia.get(data.isoformat(), (0, 0))
        dias.append(dict(dia=d, semana=DIAS_SEMANA[data.weekday()], total=total, vendas=vendas,
                         futuro=data > hoje))
    com_venda = [d for d in dias if d["total"] > 0]
    return dict(
        dias=dias,
        melhor=max(com_venda, key=lambda d: (d["total"], -d["dia"])) if com_venda else None,
        pior=min(com_venda, key=lambda d: (d["total"], d["dia"])) if len(com_venda) > 1 else None,
        sem_venda=sum(1 for d in dias if d["total"] == 0 and not d["futuro"]),
    )


def nivel(total, maximo):
    """0 = sem venda; 1..NIVEIS proporcional ao maior dia do ano."""
    if total <= 0 or maximo <= 0:
        return 0
    return max(1, min(NIVEIS, math.ceil(NIVEIS * total / maximo)))


def mapa_ano(con, ano, hoje):
    """12 linhas (meses) × 31 colunas (dias), com nível de cor e o melhor dia de cada mês."""
    por_dia = db.vendas_por_dia(con, f"{ano}-01-01", f"{ano + 1}-01-01")
    maximo = max((t for t, _ in por_dia.values()), default=0)
    meses = []
    for m in range(1, 13):
        n_dias = calendar.monthrange(ano, m)[1]
        celulas, melhor, total_mes = [], None, 0
        for d in range(1, 32):
            if d > n_dias:
                celulas.append(None)          # 30 de fevereiro etc.: não existe
                continue
            data = date(ano, m, d)
            total, vendas = por_dia.get(data.isoformat(), (0, 0))
            c = dict(dia=d, semana=DIAS_SEMANA[data.weekday()], total=total, vendas=vendas,
                     nivel=nivel(total, maximo), futuro=data > hoje)
            celulas.append(c)
            total_mes += total
            if total > 0 and (melhor is None or total > melhor["total"]):
                melhor = c
        meses.append(dict(numero=m, nome=MESES_CURTOS[m - 1], nome_longo=db.MESES[m - 1],
                          ano_mes=f"{ano}-{m:02d}", celulas=celulas, melhor=melhor, total=total_mes))
    # faixa de cada tom, em centavos: nivel(t) == k  <=>  maximo*(k-1)/NIVEIS < t <= maximo*k/NIVEIS
    faixas = [(math.floor(maximo * (k - 1) / NIVEIS) + 1, math.floor(maximo * k / NIVEIS))
              for k in range(1, NIVEIS + 1)] if maximo else []
    return dict(ano=ano, meses=meses, maximo=maximo, faixas=faixas)
