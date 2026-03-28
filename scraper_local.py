"""
scraper_local.py
Usa Playwright para scraping da OLX com técnicas anti-detecção.
Roda no GitHub Actions — extrai dados da listagem + detalhes quando possível.
"""

import re
import time
import random
import json
import csv
import os
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

MAX_LINKS   = int(os.environ.get("MAX_LINKS", 20))
OUTPUT_FILE = os.environ.get("CSV_FILE", "imoveis.csv")

LIST_URL = (
    "https://www.olx.com.br/imoveis/venda/estado-sp/sao-paulo-e-regiao"
    "?pe=400000&sf=1&coe=1000&ipe=500&ss=30"
)

# ── Helpers de extração ──────────────────────────────────────────────────────

def extrair_preco(texto: str) -> float:
    match = re.search(r"R\$\s*[\d.,]+", texto)
    return float(re.sub(r"[^\d]", "", match.group(0))) if match else 0.0

def extrair_area(texto: str) -> float:
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*m²", texto)
    return float(match.group(1).replace(",", ".")) if match else 0.0

def extrair_quartos(texto: str) -> int:
    match = re.search(
        r"(\d+)\s*(?:quarto|dormitório|dorm|suíte|q)\s*s?", texto, re.IGNORECASE
    )
    return int(match.group(1)) if match else 0

def extrair_endereco(texto: str) -> str:
    m = re.search(r"([A-Za-záéíóúàãõâêôÁÉÍÓÚÀÃÕÂÊÔ\s]+),\s*São Paulo", texto, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = re.search(r"São Paulo[,\-]\s*([A-Za-záéíóúàãõâêôÁÉÍÓÚÀÃÕÂÊÔ\s]+)", texto, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return ""

def normalizar_endereco(endereco: str) -> str:
    if not endereco:
        return ""
    if "são paulo" not in endereco.lower():
        return f"{endereco}, São Paulo"
    return endereco

def is_pagina_bloqueada(titulo: str, texto_body: str) -> bool:
    """Detecta páginas de bloqueio/CAPTCHA do Cloudflare ou OLX."""
    titulo_lower = titulo.lower()
    texto_lower  = texto_body[:500].lower()
    sinais = [
        "sorry", "blocked", "opt out", "personal informa",
        "just a moment", "checking your browser", "enable javascript",
        "cf-browser-verification", "captcha", "403 forbidden",
        "access denied", "ray id"
    ]
    return any(s in titulo_lower or s in texto_lower for s in sinais)

# ── Script de evasão de fingerprint ─────────────────────────────────────────

STEALTH_JS = """
() => {
    // Ocultar webdriver
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

    // Plugins realistas
    Object.defineProperty(navigator, 'plugins', {
        get: () => [1, 2, 3, 4, 5],
    });

    // Linguagens
    Object.defineProperty(navigator, 'languages', {
        get: () => ['pt-BR', 'pt', 'en-US', 'en'],
    });

    // Permissões não automatizadas
    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : originalQuery(parameters)
    );

    // Chrome runtime fake
    window.chrome = { runtime: {} };

    // Ocultar headless via user agent
    Object.defineProperty(navigator, 'userAgent', {
        get: () => 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    });
}
"""

# ── Coleta de links + dados básicos da listagem ──────────────────────────────

def coletar_da_listagem(page, max_links: int) -> list[dict]:
    """
    Coleta links e dados básicos (título, preço, endereço, área) diretamente
    da página de listagem, sem precisar abrir cada anúncio.
    """
    print("🌐 Warm-up na home do OLX...")
    page.goto("https://www.olx.com.br", wait_until="domcontentloaded", timeout=30000)
    time.sleep(random.uniform(2.5, 4.0))

    # Simular comportamento humano: scroll suave
    page.mouse.move(random.randint(100, 800), random.randint(100, 400))
    time.sleep(random.uniform(0.5, 1.2))

    print("🔍 Acessando página de listagem...")
    page.goto(LIST_URL, wait_until="domcontentloaded", timeout=60000)

    print("⏳ Aguardando anúncios...")
    try:
        page.wait_for_selector('a[data-testid="adcard-link"]', timeout=90000)
    except PlaywrightTimeout:
        print("⚠️ Timeout aguardando seletor principal, tentando seletores alternativos...")
        try:
            page.wait_for_selector('section[data-lurker-detail="ad_list"] li', timeout=15000)
        except PlaywrightTimeout:
            print("⚠️ Nenhum seletor encontrado, prosseguindo mesmo assim...")

    # Scroll para carregar mais anúncios
    for _ in range(3):
        page.keyboard.press("End")
        time.sleep(random.uniform(0.8, 1.5))

    # Tentar extrair dados estruturados diretamente da listagem
    imoveis_da_lista = page.evaluate("""
    () => {
        const cards = document.querySelectorAll('li[data-lurker-detail], section li');
        const resultado = [];

        document.querySelectorAll('a[data-testid="adcard-link"]').forEach(a => {
            try {
                const card = a.closest('li') || a.parentElement;
                const texto = card ? card.innerText : '';

                // Preço
                let preco = 0;
                const precoEl = card && card.querySelector('h3, [class*=price], [class*=Price]');
                if (precoEl) {
                    const m = precoEl.innerText.match(/R\\$\\s*[\\d.,]+/);
                    if (m) preco = Number(m[0].replace(/\\D/g, ''));
                }
                if (!preco) {
                    const m = texto.match(/R\\$\\s*[\\d.,]+/);
                    if (m) preco = Number(m[0].replace(/\\D/g, ''));
                }

                // Título
                let titulo = a.getAttribute('aria-label') || '';
                if (!titulo) {
                    const h2 = card && card.querySelector('h2, h3');
                    titulo = h2 ? h2.innerText.trim() : '';
                }

                // Endereço
                let endereco = '';
                const locEl = card && card.querySelector('[class*=location], [class*=Location]');
                if (locEl) endereco = locEl.innerText.trim();

                // Área e quartos do título/texto
                const areaM = texto.match(/(\\d+(?:[.,]\\d+)?)\\s*m²/);
                const area = areaM ? parseFloat(areaM[1].replace(',', '.')) : 0;

                const qtM = texto.match(/(\\d+)\\s*(?:quarto|dorm|suíte)/i);
                const quartos = qtM ? parseInt(qtM[1]) : 0;

                resultado.push({
                    link: a.href,
                    titulo: titulo.trim(),
                    preco,
                    endereco: endereco.trim(),
                    area,
                    quartos,
                });
            } catch(e) {}
        });

        return resultado;
    }
    """)

    # Deduplica por link
    vistos = set()
    unicos = []
    for item in imoveis_da_lista:
        link = item.get("link", "")
        if link and link not in vistos:
            vistos.add(link)
            unicos.append(item)

    print(f"📦 {len(unicos)} anúncios coletados da listagem")
    return unicos[:max_links]


# ── Enriquecimento via página individual ────────────────────────────────────

def enriquecer_anuncio(page, item: dict) -> dict | None:
    """
    Tenta abrir a página do anúncio para pegar dados que faltam.
    Se bloqueado, retorna o que já temos da listagem (se tiver preço).
    """
    link = item.get("link", "")
    if not link:
        return None

    # Se já temos dados suficientes da listagem, não precisa abrir
    tem_dados_basicos = (
        item.get("preco", 0) > 0 and
        item.get("titulo") and
        item.get("area", 0) > 0
    )

    try:
        time.sleep(random.uniform(2.0, 4.5))
        page.goto(link, wait_until="domcontentloaded", timeout=45000)

        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except PlaywrightTimeout:
            pass

        titulo_page = ""
        try:
            titulo_page = page.inner_text("h1").strip()
        except Exception:
            pass
        if not titulo_page:
            titulo_page = page.evaluate(
                "() => document.querySelector('meta[property=\"og:title\"]')?.content || document.title || ''"
            )

        texto = page.inner_text("body") or ""

        # Verificar bloqueio
        if is_pagina_bloqueada(titulo_page, texto):
            print(f"    ⚠️ Bloqueado na página individual — usando dados da listagem")
            if tem_dados_basicos:
                return _montar_registro(item, item.get("titulo", ""), texto="")
            return None

        # Enriquecer com dados da página
        preco = 0.0
        try:
            preco_el = page.query_selector("h3[class*=price], [class*=Price], .olx-price, .price")
            if preco_el:
                preco = extrair_preco(preco_el.inner_text())
        except Exception:
            pass
        if not preco:
            preco = extrair_preco(texto)

        preco_anterior = 0.0
        try:
            old_el = page.query_selector("[class*=old-price], [class*=oldPrice], .old-price")
            if old_el:
                preco_anterior = extrair_preco(old_el.inner_text())
        except Exception:
            pass

        endereco = ""
        try:
            loc_el = page.query_selector("[class*=location], [class*=Location], [data-testid*=location]")
            if loc_el:
                loc_text = loc_el.inner_text().strip()
                endereco = extrair_endereco(loc_text) or loc_text
        except Exception:
            pass
        if not endereco:
            endereco = extrair_endereco(texto)

        area    = extrair_area(texto) or item.get("area", 0)
        quartos = extrair_quartos(texto) or item.get("quartos", 0)

        enriquecido = {
            "link":           link,
            "titulo":         titulo_page or item.get("titulo", ""),
            "preco":          preco or item.get("preco", 0),
            "preco_anterior": preco_anterior,
            "endereco":       endereco or item.get("endereco", ""),
            "area":           area,
            "quartos":        quartos,
        }
        return _montar_registro(enriquecido, enriquecido["titulo"], texto)

    except Exception as e:
        print(f"    ⚠️ Erro ao enriquecer ({e}) — usando dados da listagem")
        if tem_dados_basicos:
            return _montar_registro(item, item.get("titulo", ""), texto="")
        return None


def _montar_registro(item: dict, titulo: str, texto: str) -> dict:
    endereco = normalizar_endereco(item.get("endereco", ""))
    return {
        "link":           item.get("link", ""),
        "fonte":          "olx",
        "titulo":         titulo,
        "endereco":       endereco,
        "descricao":      texto[:2000] if texto else "",
        "preco":          item.get("preco", 0),
        "preco_anterior": item.get("preco_anterior", 0),
        "area":           item.get("area", 0),
        "quartos":        item.get("quartos", 0),
        "scraped_at":     datetime.utcnow().isoformat(),
    }


# ── Persistência ─────────────────────────────────────────────────────────────

def salvar_csv(imoveis: list[dict], path: str):
    if not imoveis:
        print("⚠️ Nenhum imóvel para salvar.")
        return
    campos = ["link", "fonte", "titulo", "endereco", "descricao",
              "preco", "preco_anterior", "area", "quartos", "scraped_at"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=campos)
        writer.writeheader()
        writer.writerows(imoveis)
    print(f"💾 {len(imoveis)} imóveis salvos em {path}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",  # ← chave anti-detecção
                "--window-size=1280,800",
            ]
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
            viewport={"width": 1280, "height": 800},
            # Cabeçalhos que um browser real enviaria
            extra_http_headers={
                "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Linux"',
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
            }
        )

        # Injetar script de evasão em todas as páginas
        context.add_init_script(STEALTH_JS)

        # Bloquear apenas mídia pesada (manter CSS para não parecer headless)
        def bloquear(route):
            if route.request.resource_type in ["image", "font", "media"]:
                route.abort()
            else:
                route.continue_()
        context.route("**/*", bloquear)

        # 1) Coletar dados da listagem
        page_lista = context.new_page()
        itens = coletar_da_listagem(page_lista, MAX_LINKS)
        page_lista.close()

        if not itens:
            print("❌ Nenhum link coletado. Encerrando.")
            browser.close()
            return

        # 2) Enriquecer cada anúncio (abrindo página individual)
        anuncios = []
        falhas   = 0
        fallbacks = 0  # usou dados da listagem por bloqueio

        for i, item in enumerate(itens, 1):
            link = item.get("link", "")
            print(f"\n  [{i}/{len(itens)}] {link[:80]}")
            page = context.new_page()
            dados = enriquecer_anuncio(page, item)
            page.close()

            if dados:
                # Validação mínima: deve ter preço
                if dados["preco"] > 0:
                    anuncios.append(dados)
                    print(f"    ✅ {dados['titulo'][:50]} | R${dados['preco']} | {dados['area']}m² | {dados['quartos']}q")
                else:
                    # Tentar aproveitar preço da listagem
                    if item.get("preco", 0) > 0:
                        dados["preco"] = item["preco"]
                        anuncios.append(dados)
                        fallbacks += 1
                        print(f"    🔄 Preço da listagem: R${dados['preco']} | {dados['titulo'][:40]}")
                    else:
                        falhas += 1
                        print(f"    ⛔ Sem preço — descartado ({falhas} descartados)")
            else:
                falhas += 1
                print(f"    ⛔ Sem dados — descartado ({falhas} descartados)")

        browser.close()

    print(f"\n✅ Extraídos: {len(anuncios)} | Fallbacks da listagem: {fallbacks} | Descartados: {falhas}")
    salvar_csv(anuncios, OUTPUT_FILE)


if __name__ == "__main__":
    main()
