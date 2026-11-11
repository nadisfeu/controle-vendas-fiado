// Tela de nova venda: monta o carrinho e envia como JSON no campo "itens".
// Os preços andam como texto "12,50"; quem converte de verdade é o servidor (db.reais_para_centavos).
(function () {
  const ler = id => JSON.parse(document.getElementById(id).textContent);
  const produtos = ler('dados-produtos');
  const carrinho = ler('dados-carrinho');
  const saldoAtual = ler('dados-saldo');

  const $ = id => document.getElementById(id);
  const normalizar = s => s.normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase().trim();

  // "1.234,56" / "12,5" / "12.5" -> centavos; NaN se inválido. Só para mostrar o total ao vivo.
  function centavos(texto) {
    let s = String(texto).replace(/R\$|\s/g, '');
    if (s.includes(',')) s = s.replace(/\./g, '').replace(',', '.');
    else if (/^\d{1,3}(\.\d{3})+$/.test(s)) s = s.replace(/\./g, '');
    if (!/^-?\d+(\.\d{1,2})?$/.test(s)) return NaN;
    return Math.round(parseFloat(s) * 100);
  }
  function reais(c) {
    const sinal = c < 0 ? '-' : '';
    c = Math.abs(c);
    const inteiro = Math.floor(c / 100).toString().replace(/\B(?=(\d{3})+(?!\d))/g, '.');
    return `${sinal}R$ ${inteiro},${String(c % 100).padStart(2, '0')}`;
  }
  const produtoDe = item => produtos.find(p => p.id === item.produto_id);
  // centavos -> "180,00" (como a pessoa digitaria no campo)
  const campoReais = c => `${Math.floor(c / 100)},${String(c % 100).padStart(2, '0')}`;
  // preço sobre o qual a porcentagem é calculada: o normal do produto, ou o digitado no item avulso
  function precoBase(item) {
    const prod = produtoDe(item);
    if (prod) return centavos(prod.preco);
    if (item.base === undefined) item.base = item.preco;
    return centavos(item.base);
  }
  // "10" / "10,5" / "10%" -> 10.5; NaN se não for uma porcentagem de 0 a 100
  function porcentagem(texto) {
    const s = String(texto).replace('%', '').replace(',', '.').trim();
    if (!/^\d+(\.\d+)?$/.test(s)) return NaN;
    const n = parseFloat(s);
    return n <= 100 ? n : NaN;
  }
  const mostrarPct = n => (Math.round(n * 10) / 10).toString().replace('.', ',');

  function desenharProdutos() {
    const lista = $('produtos');
    if (!lista) return;
    const q = normalizar($('busca-produto').value);
    const tipo = $('filtro-tipo') ? $('filtro-tipo').value : '', marca = $('filtro-marca') ? $('filtro-marca').value : '';
    lista.innerHTML = '';
    for (const p of produtos) {
      if (!normalizar([p.nome, p.codigo, p.tipo, p.marca].join(' ')).includes(q)) continue;
      if ((tipo && p.tipo !== tipo) || (marca && p.marca !== marca)) continue;
      const li = document.createElement('li');
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'produto';
      const qtd = noCarrinho(p.id);
      const resta = p.estoque - qtd;
      b.innerHTML = '<span class="nome"></span><span class="extra"></span><span class="preco"></span><span class="estoque"></span>' +
        (qtd ? `<span class="qtd">${qtd}</span>` : '');
      b.querySelector('.nome').textContent = p.nome;
      const extra = [p.tipo, p.marca, p.codigo && 'cód. ' + p.codigo].filter(Boolean).join(' · ');
      if (extra) b.querySelector('.extra').textContent = extra; else b.querySelector('.extra').remove();
      b.querySelector('.preco').textContent = 'R$ ' + p.preco;
      const est = b.querySelector('.estoque');
      est.textContent = resta > 0 ? `restam ${resta}` : (resta === 0 ? 'sem estoque' : `faltam ${-resta}`);
      if (resta < 0 || (resta === 0 && !qtd)) est.classList.add('alerta');
      b.addEventListener('click', () => adicionarProduto(p));
      li.appendChild(b);
      lista.appendChild(li);
    }
  }

  function noCarrinho(id) {
    return carrinho.filter(i => i.produto_id === id).reduce((s, i) => s + i.quantidade, 0);
  }

  function adicionarProduto(p) {
    const existente = carrinho.find(i => i.produto_id === p.id && i.preco === p.preco);
    if (existente) existente.quantidade++;
    else carrinho.push({ produto_id: p.id, descricao: p.nome, quantidade: 1, preco: p.preco });
    desenhar();
  }

  // Linhas do carrinho já desenhadas: [{item, subtotal, desconto}] para atualizar os números
  // enquanto a pessoa digita o preço, sem redesenhar (e sem tirar o cursor do campo).
  let linhas = [];

  function desenharCarrinho() {
    const ul = $('carrinho');
    ul.innerHTML = '';
    linhas = [];
    carrinho.forEach((item, idx) => {
      const li = document.createElement('li');
      li.innerHTML = `
        <span class="descricao"></span>
        <span class="controle-qtd">
          <button type="button" class="menos" aria-label="Diminuir">−</button>
          <span class="qtd">${item.quantidade}</span>
          <button type="button" class="mais" aria-label="Aumentar">+</button>
        </span>
        <label class="preco-item">
          <small>Vendido por (cada)</small>
          <span class="campo-preco">R$ <input inputmode="decimal" aria-label="Vendido por, cada"></span>
        </label>
        <span class="subtotal"></span>
        <button type="button" class="remover" aria-label="Remover item">✕</button>
        <label class="pct-item">Desconto <input inputmode="decimal" placeholder="0" aria-label="Desconto em porcentagem"> %</label>
        <span class="desconto" hidden></span>`;
      li.querySelector('.descricao').textContent = item.descricao;
      const prod = produtoDe(item);
      if (prod && noCarrinho(prod.id) > prod.estoque) {
        const aviso = document.createElement('span');
        aviso.className = 'aviso-estoque';
        aviso.textContent = prod.estoque > 0 ? `⚠ só tem ${prod.estoque} em estoque` : '⚠ sem estoque';
        li.appendChild(aviso);
      }
      const campo = li.querySelector('.preco-item input'), campoPct = li.querySelector('.pct-item input');
      campo.value = item.preco;
      // porcentagem que o preço atual representa (para mostrar no campo de %)
      const sincronizarPct = () => {
        const base = precoBase(item), c = centavos(item.preco);
        campoPct.value = !isNaN(c) && base > 0 && c < base ? mostrarPct((base - c) * 100 / base) : '';
      };
      sincronizarPct();
      campo.addEventListener('focus', () => campo.select());               // tocar já seleciona para digitar por cima
      campoPct.addEventListener('focus', () => campoPct.select());
      // Só atualiza números, nunca redesenha aqui: ao sair do campo tocando em "Salvar venda", redesenhar
      // faz a página pular entre o dedo encostar e soltar, e o toque cai fora do botão (venda não salva).
      campo.addEventListener('input', () => { item.preco = campo.value; sincronizarPct(); atualizarNumeros(); });
      campoPct.addEventListener('input', () => {
        const pct = campoPct.value.trim() === '' ? 0 : porcentagem(campoPct.value), base = precoBase(item);
        if (isNaN(pct) || isNaN(base)) return;                             // continua digitando: não mexe ainda
        item.preco = campoReais(Math.round(base * (100 - pct) / 100));
        campo.value = item.preco;
        atualizarNumeros();
      });
      li.querySelector('.menos').addEventListener('click', () => {
        if (item.quantidade > 1) item.quantidade--; else carrinho.splice(idx, 1);
        desenhar();
      });
      li.querySelector('.mais').addEventListener('click', () => { item.quantidade++; desenhar(); });
      li.querySelector('.remover').addEventListener('click', () => { carrinho.splice(idx, 1); desenhar(); });
      linhas.push({ item, subtotal: li.querySelector('.subtotal'), desconto: li.querySelector('.desconto') });
      ul.appendChild(li);
    });
    $('carrinho-vazio').hidden = carrinho.length > 0;
    atualizarNumeros();
  }

  function atualizarNumeros() {
    let total = 0, descontos = 0, valido = carrinho.length > 0;
    for (const { item, subtotal, desconto } of linhas) {
      const c = centavos(item.preco);
      if (isNaN(c) || c < 0) {
        valido = false;
        subtotal.textContent = 'valor inválido';
        desconto.hidden = true;
        continue;
      }
      total += c * item.quantidade;
      subtotal.textContent = reais(c * item.quantidade);
      const prod = produtoDe(item);
      const normal = prod ? centavos(prod.preco) : NaN;
      desconto.hidden = isNaN(normal) || c === normal;
      if (!desconto.hidden) {
        const diferenca = (normal - c) * item.quantidade;
        if (diferenca > 0) descontos += diferenca;
        desconto.classList.toggle('acima', diferenca < 0);
        desconto.textContent = diferenca > 0
          ? `Preço normal ${reais(normal)} · desconto de ${reais(diferenca)} (${mostrarPct((normal - c) * 100 / normal)}%)`
          : `Preço normal ${reais(normal)} · ${reais(-diferenca)} acima do normal`;
      }
    }
    $('total').textContent = reais(total);
    $('descontos').hidden = descontos === 0;
    $('descontos-valor').textContent = reais(descontos);
    $('saldo-depois').textContent = reais(saldoAtual + total);
    $('salvar').disabled = !valido;
    $('campo-itens').value = JSON.stringify(carrinho);
  }

  function desenhar() { desenharProdutos(); desenharCarrinho(); }

  $('avulso-adicionar').addEventListener('click', () => {
    const descricao = $('avulso-descricao').value.trim(), preco = $('avulso-preco').value.trim();
    if (!descricao) { $('avulso-descricao').focus(); return; }
    if (isNaN(centavos(preco))) { alert('Valor inválido. Use, por exemplo, 25,00.'); $('avulso-preco').focus(); return; }
    carrinho.push({ produto_id: null, descricao, quantidade: 1, preco });
    $('avulso-descricao').value = ''; $('avulso-preco').value = '';
    desenhar();
  });
  const busca = $('busca-produto');
  if (busca) busca.addEventListener('input', desenharProdutos);
  for (const id of ['filtro-tipo', 'filtro-marca']) if ($(id)) $(id).addEventListener('change', desenharProdutos);
  $('form-venda').addEventListener('submit', () => { $('salvar').disabled = true; });  // evita salvar duas vezes

  desenhar();
})();
