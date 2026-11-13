// Filtros de Tipo e Marca ligados: escolhida uma marca, o filtro de tipo só oferece os tipos que existem
// nessa marca (e o contrário). Refaz as opções em vez de esconder, porque o Safari do iPhone ignora
// <option hidden>. pares = [[valorDoTipo, valorDaMarca], ...], com '-' para "sem tipo"/"sem marca".
function ligarTipoEMarca(selTipo, selMarca, pares) {
  if (!selTipo || !selMarca) return;
  const originais = new Map([selTipo, selMarca].map(s => [s, Array.from(s.options).map(o => [o.value, o.text])]));
  const juntoCom = (escolhido, lado) =>
    escolhido ? new Set(pares.filter(p => p[lado] === escolhido).map(p => p[1 - lado])) : null;
  function refazer(sel, permitidos) {
    const atual = sel.value;
    sel.innerHTML = '';
    for (const [valor, texto] of originais.get(sel))
      if (valor === '' || !permitidos || permitidos.has(valor)) sel.add(new Option(texto, valor));
    sel.value = permitidos && !permitidos.has(atual) ? '' : atual;
  }
  function atualizar() {
    refazer(selMarca, juntoCom(selTipo.value, 0));
    refazer(selTipo, juntoCom(selMarca.value, 1));
  }
  selTipo.addEventListener('change', atualizar);
  selMarca.addEventListener('change', atualizar);
  atualizar();
}
