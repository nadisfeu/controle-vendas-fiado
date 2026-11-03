// Gráficos de Relatórios: colunas de vendas por dia (desenhadas aqui, em SVG) e a caixinha de valor
// (dica) que aparece ao passar o mouse, tocar ou focar com o teclado em qualquer elemento com data-dica.
(function () {
  // ------------------------------------------------------------------ dica
  const dica = document.createElement('div');
  dica.className = 'dica-grafico';
  dica.setAttribute('role', 'tooltip');
  dica.hidden = true;
  document.body.appendChild(dica);
  let ativo = null;

  function mostrar(el) {
    if (ativo && ativo !== el) ativo.classList.remove('ativo');
    ativo = el;
    el.classList.add('ativo');
    dica.textContent = el.dataset.dica;          // textContent: o texto nunca vira HTML
    dica.hidden = false;
    const r = el.getBoundingClientRect(), largura = dica.offsetWidth, altura = dica.offsetHeight;
    const maxX = window.scrollX + document.documentElement.clientWidth - largura - 8;
    const x = Math.max(window.scrollX + 8, Math.min(maxX, window.scrollX + r.left + r.width / 2 - largura / 2));
    let y = window.scrollY + r.top - altura - 8;
    if (y < window.scrollY + 4) y = window.scrollY + r.bottom + 8;   // sem espaço em cima: vai para baixo
    dica.style.left = x + 'px';
    dica.style.top = y + 'px';
  }
  function esconder() {
    if (ativo) ativo.classList.remove('ativo');
    ativo = null;
    dica.hidden = true;
  }
  document.addEventListener('pointerover', e => {
    const el = e.target.closest('[data-dica]');
    if (el) mostrar(el);
  });
  document.addEventListener('pointerout', e => {
    // no toque, o "sair" vem logo depois do toque: a dica fica até tocar em outro lugar
    if (e.pointerType !== 'touch' && e.target.closest('[data-dica]')) esconder();
  });
  document.addEventListener('pointerdown', e => { if (!e.target.closest('[data-dica]')) esconder(); });
  document.addEventListener('focusin', e => { const el = e.target.closest('[data-dica]'); if (el) mostrar(el); });
  document.addEventListener('focusout', esconder);
  window.addEventListener('scroll', () => { if (ativo) mostrar(ativo); }, { passive: true });

  // ------------------------------------------------------------------ colunas do mês
  const alvo = document.getElementById('grafico-mes');
  const fonte = document.getElementById('dados-grafico-mes');
  if (!alvo || !fonte) return;
  const dias = JSON.parse(fonte.textContent);
  const NS = 'http://www.w3.org/2000/svg';

  function el(tag, atributos, pai) {
    const e = document.createElementNS(NS, tag);
    for (const k in atributos) e.setAttribute(k, atributos[k]);
    if (pai) pai.appendChild(e);
    return e;
  }
  function reais(c) {
    const inteiro = Math.floor(c / 100).toString().replace(/\B(?=(\d{3})+(?!\d))/g, '.');
    return `R$ ${inteiro},${String(c % 100).padStart(2, '0')}`;
  }
  function compacto(c) {          // eixo: R$ 0 / R$ 500 / R$ 1,5 mil
    const r = c / 100;
    if (r >= 1000) return 'R$ ' + (r / 1000).toLocaleString('pt-BR', { maximumFractionDigits: 1 }) + ' mil';
    return 'R$ ' + Math.round(r).toLocaleString('pt-BR');
  }
  function passoRedondo(maximo) {  // ~4 linhas de grade com valores redondos
    const bruto = maximo / 4, p = Math.pow(10, Math.floor(Math.log10(bruto)));
    for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= bruto) return m * p;
    return 10 * p;
  }
  function textoDica(d) {
    if (d.futuro) return `Dia ${d.dia} (${d.semana}): ainda não chegou`;
    if (!d.total) return `Dia ${d.dia} (${d.semana}): sem vendas`;
    return `Dia ${d.dia} (${d.semana}): ${reais(d.total)} · ${d.vendas} venda${d.vendas > 1 ? 's' : ''}`;
  }

  let larguraDesenhada = 0;
  function desenhar() {
    const largura = alvo.clientWidth;
    if (!largura || largura === larguraDesenhada) return;
    larguraDesenhada = largura;
    const altura = 230, esq = 62, dir = 6, topo = 24, base = 26;
    const plotW = largura - esq - dir, plotH = altura - topo - base, faixa = plotW / dias.length;
    const maximo = Math.max(...dias.map(d => d.total));
    const passo = passoRedondo(maximo), teto = Math.ceil(maximo / passo) * passo;
    const y = v => topo + plotH - (v / teto) * plotH;

    alvo.textContent = '';
    const svg = el('svg', { width: largura, height: altura, viewBox: `0 0 ${largura} ${altura}`, 'aria-hidden': 'true' }, alvo);
    for (let v = 0; v <= teto + 1; v += passo) {
      el('line', { class: 'grade', x1: esq, x2: largura - dir, y1: y(v), y2: y(v) }, svg);
      el('text', { class: 'eixo', x: esq - 8, y: y(v) + 4, 'text-anchor': 'end' }, svg).textContent = compacto(v);
    }
    const larguraBarra = Math.max(3, Math.min(24, faixa * 0.7));
    const todosOsDias = faixa >= 22;
    const melhor = dias.reduce((a, d) => (d.total > (a ? a.total : 0) ? d : a), null);

    dias.forEach((d, i) => {
      const cx = esq + faixa * (i + 0.5);
      const g = el('g', { class: 'dia' + (d.futuro ? ' futuro' : ''), 'data-dica': textoDica(d) }, svg);
      if (d.total && !d.futuro) g.setAttribute('tabindex', '0');
      el('rect', { class: 'alvo', x: esq + faixa * i, y: topo, width: faixa, height: plotH + base, fill: 'transparent' }, g);
      if (d.total > 0) {
        const x0 = cx - larguraBarra / 2, x1 = cx + larguraBarra / 2, yb = y(0), yt = y(d.total);
        const r = Math.min(4, larguraBarra / 2, yb - yt);         // ponta arredondada, base reta
        el('path', { class: 'barra', d: `M${x0},${yb} V${yt + r} Q${x0},${yt} ${x0 + r},${yt} H${x1 - r} Q${x1},${yt} ${x1},${yt + r} V${yb} Z` }, g);
      }
      if (todosOsDias || d.dia === 1 || d.dia % 5 === 0) {
        el('text', { class: 'eixo' + (d.futuro ? ' futuro' : ''), x: cx, y: altura - 8, 'text-anchor': 'middle' }, g).textContent = d.dia;
      }
    });
    if (melhor) {   // só o maior valor ganha número escrito; os outros ficam na dica e na tabela
      const i = dias.indexOf(melhor), cx = esq + faixa * (i + 0.5);
      const texto = el('text', { class: 'rotulo-valor', x: cx, y: y(melhor.total) - 7, 'text-anchor': 'middle' }, svg);
      texto.textContent = compacto(melhor.total);
      const caixa = texto.getBBox();                              // não deixa o rótulo sair pelas bordas
      if (caixa.x < 0) texto.setAttribute('x', cx - caixa.x);
      if (caixa.x + caixa.width > largura) texto.setAttribute('x', cx - (caixa.x + caixa.width - largura));
    }
  }
  desenhar();
  new ResizeObserver(desenhar).observe(alvo);
})();
