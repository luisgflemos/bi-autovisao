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
  fetchOriginal('/api/sessao',{credentials:'same-origin'}).then(r=>r.status===401?irParaLogin():r.json()).then(dados=>{
    if(!dados||!dados.usuario) return;
    const sair=document.querySelector('.view-nav .nav-sair');
    if(sair){sair.textContent='Sair ('+dados.usuario+')';sair.title='Conectado como '+dados.usuario;}
  }).catch(()=>{});
})();
