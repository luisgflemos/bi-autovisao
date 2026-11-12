const inspectorCharts = (() => {
const currency=new Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL'});
const countFormat=new Intl.NumberFormat('pt-BR');
function node(tag,text,cls) {
  const item=document.createElement(tag);
  if(text!==undefined) item.textContent=text;
  if(cls) item.className=cls;
  return item;
}
function inspectorChart(title, rows, series, valueOf, format) {
  const card=node('article',undefined,'card');
  if(series.length===1) card.style.borderTopColor=series[0][1];
  card.append(node('h3',title));
  if(!rows.length){card.append(node('p','Sem dados no período.'));return card;}
  const legend=node('div',undefined,'inspector-legend');
  for(const [label,color] of series) {
    if(!label)continue;
    const item=node('span',label),swatch=node('i');
    swatch.style.background=color;
    item.prepend(swatch);
    legend.append(item);
  }
  card.append(legend);
  const list=node('div',undefined,'inspector-list');
  const entries=rows.map(row=>{
    const values=valueOf(row);
    return {name:row[0],values,total:values.reduce((sum,value)=>sum+value,0)};
  }).sort((a,b)=>b.total-a.total||a.name.localeCompare(b.name,'pt-BR'));
  const max=Math.max(1,...entries.map(entry=>Math.abs(entry.total)));
  for(const {name,values,total} of entries) {
    const row=node('div',undefined,'inspector-row'),label=node('div',undefined,'inspector-label');
    label.append(node('span',name),node('strong',format(total)));
    const track=node('div',undefined,'inspector-track');
    const detail=series.map(([label],i)=>label?label+': '+format(values[i]):format(values[i])).join(' · ');
    track.title=detail;
    track.setAttribute('role','img');
    track.setAttribute('aria-label',detail);
    for(const [i,value] of values.entries()) {
      const segment=node('span');
      segment.style.width=(100*Math.abs(value)/max)+'%';
      segment.style.background=series[i][1];
      if(value) {
        segment.style.boxShadow='inset -1px 0 white';
        track.append(segment);
      }
    }
    row.append(label,track);
    list.append(row);
  }
  card.append(list);
  return card;
}
function inspectorCharts(rows) {
  const money=cents=>currency.format(cents/100);
  return [
    inspectorChart('Serviços por vistoriador · grupo',rows,[['Transferência','#1749a3'],['Cautelar','#ed9b16'],['Outros','#40444b']],r=>r.slice(1,4),v=>countFormat.format(v)),
    inspectorChart('Serviços por vistoriador · base e móvel',rows,[['Base','#6829a6'],['Móvel','#18b899'],['Sem informação','repeating-linear-gradient(135deg,#b6bbc1 0 3px,#676e77 3px 5px)']],r=>r.slice(4,7),v=>countFormat.format(v)),
    inspectorChart('Preço Médio por vistoriador',rows,[['','#397400']],r=>[r[8]/r[7]],money),
    inspectorChart('Faturamento por vistoriador',rows,[['','#bf1515']],r=>[r[8]],money),
  ];
}
return inspectorCharts;
})();
