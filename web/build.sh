#!/bin/sh
# Monta o site estático da Vercel em dist/ com as telas e recursos visuais do BI.
# A API, o login e as bases continuam no Railway (ver vercel.json).
set -eu
rm -rf dist
mkdir -p dist/assets/fonts
cd app
cp painel.html gerencial.html relatorio_cliente.html design-system.css filtros.js sessao.js \
   graficos_vistoriadores.js og-banner.png Logo_AutoVisao_PNG.png ../dist/
cp assets/fonts/*.ttf assets/fonts/fonts.css assets/fonts/*-OFL.txt ../dist/assets/fonts/
cp Logo_AutoVisao_PNG.png ../dist/logo-auto-visao.png
echo "Site estático pronto em dist/"
