// Sessão do BI: as telas podem vir de um CDN (Vercel), então o login é conferido aqui.
// Qualquer resposta 401 da API leva à tela de login, voltando depois para a página atual.
(function(){
  const irParaLogin=()=>{location.replace('/login?proximo='+encodeURIComponent(location.pathname+location.search));};
  const fetchOriginal=window.fetch.bind(window);
  window.fetch=async(...args)=>{
    const resposta=await fetchOriginal(...args);
    if(resposta.status===401) irParaLogin();
    return resposta;
  };
  // Cabeçalho do PDF: título do relatório, data/hora e quem gerou.
  let usuarioAtual='';
  const preencherCabecalhoPdf=()=>{
    const titulo=document.querySelector('h1');
    document.querySelectorAll('[data-av-print-title]').forEach(el=>{el.textContent=titulo?titulo.textContent.trim():document.title;});
    const agora=new Date().toLocaleString('pt-BR',{dateStyle:'short',timeStyle:'short'});
    document.querySelectorAll('[data-av-print-when]').forEach(el=>{el.textContent='Gerado em '+agora+(usuarioAtual?' por '+usuarioAtual:'');});
  };
  window.addEventListener('beforeprint',preencherCabecalhoPdf);
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',preencherCabecalhoPdf);else preencherCabecalhoPdf();
  fetchOriginal('/api/sessao',{credentials:'same-origin'}).then(r=>r.status===401?irParaLogin():r.json()).then(dados=>{
    if(!dados||!dados.usuario) return;
    usuarioAtual=dados.usuario;preencherCabecalhoPdf();
    const sair=document.querySelector('.view-nav .nav-sair');
    if(sair){sair.textContent='Sair ('+dados.usuario+')';sair.title='Conectado como '+dados.usuario;}
  }).catch(()=>{});
})();
