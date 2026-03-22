"""
scraper_local.py
Usa Playwright (equivalente Python do Puppeteer) para scraping da OLX.
Roda no GitHub Actions com Chrome real — contorna o Cloudflare.
"""

import re
import time
import random
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

def is_bloqueado(titulo: str, preco: float) -> bool:
    titulo_lower = titulo.lower()
    return (
        "sorry" in titulo_lower or
        "blocked" in titulo_lower or
        "opt out" in titulo_lower or
        "personal informa" in titulo_lower or
        preco == 0.0
    )

def coletar_links(page, max_links: int) -> list[str]:
    print("🌐 Acessando OLX...")

    # Warm-up na página inicial para pegar cookies
    page.goto("https://www.olx.com.br", wait_until="domcontentloaded", timeout=30000)
    time.sleep(random.uniform(2.0, 3.5))

    page.goto(LIST_URL, wait_until="domcontentloaded", timeout=60000)

    print("⏳ Aguardando anúncios...")
    try:
        page.wait_for_selector('a[data-testid="adcard-link"]', timeout=90000)
    except PlaywrightTimeout:
        print("⚠️ Timeout aguardando seletor, tentando mesmo assim...")

    links = page.eval_on_selector_all(
        'a[data-testid="adcard-link"]',
        "els => els.map(el => el.href)"
    )

    links = list(dict.fromkeys(links))[:max_links]
    print(f"📦 {len(links)} links coletados")
    return links


def scrape_anuncio(page, link: str) -> dict | None:
    try:
        # Delay aleatório maior entre anúncios para parecer humano
        time.sleep(random.uniform(3.0, 6.0))

        page.goto(link, wait_until="domcontentloaded", timeout=45000)

        # Aguarda o corpo carregar
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except PlaywrightTimeout:
            pass

        texto = page.inner_text("body") or ""

        # Título
        titulo = ""
        try:
            titulo = page.inner_text("h1").strip()
        except Exception:
            pass
        if not titulo:
            titulo = page.evaluate(
                "() => document.querySelector('meta[property=\"og:title\"]')?.content || document.title || ''"
            )

        # Checar bloqueio antes de continuar
        preco_inicial = extrair_preco(texto)
        if is_bloqueado(titulo, preco_inicial):
            return None

        # Preço
        preco = 0.0
        try:
            preco_el = page.query_selector("h3.olx-adcard__price, h3[class*=price], .olx-price, .price")
            if preco_el:
                preco = extrair_preco(preco_el.inner_text())
        except Exception:
            pass
        if not preco:
            preco = preco_inicial

        # Preço anterior
        preco_anterior = 0.0
        try:
            old_el = page.query_selector("p.olx-adcard__old-price, .old-price, .price-old")
            if old_el:
                preco_anterior = extrair_preco(old_el.inner_text())
        except Exception:
            pass

        # Endereço
        endereco = ""
        try:
            loc_el = page.query_selector("p.olx-adcard__location, .olx-adcard__location, .location")
            if loc_el:
                loc_text = loc_el.inner_text().strip()
                m = re.search(r"([A-Za-záéíóúàãõâêô\s]+),\s*São Paulo", loc_text, re.IGNORECASE)
                if m:
                    endereco = m.group(1).strip()
                else:
                    m = re.search(r"São Paulo[,\-]\s*([A-Za-záéíóúàãõâêô\s]+)", loc_text, re.IGNORECASE)
                    if m:
                        endereco = m.group(1).strip()
        except Exception:
            pass
        if not endereco:
            endereco = extrair_endereco(texto)
        if endereco and "são paulo" not in endereco.lower():
            endereco = f"{endereco}, São Paulo"

        area    = extrair_area(texto)
        quartos = extrair_quartos(texto)

        return {
            "link":           link,
            "fonte":          "olx",
            "titulo":         titulo,
            "endereco":       endereco,
            "descricao":      texto[:2000],
            "preco":          preco,
            "preco_anterior": preco_anterior,
            "area":           area,
            "quartos":        quartos,
            "scraped_at":     datetime.utcnow().isoformat(),
        }
    except Exception as e:
        print(f"  ❌ Erro em {link[:60]}: {e}")
        return None


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


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
            ]
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="pt-BR",
            viewport={"width": 1280, "height": 800},
        )

        # Bloquear recursos pesados
        def bloquear(route):
            if route.request.resource_type in ["image", "stylesheet", "font", "media"]:
                route.abort()
            else:
                route.continue_()
        context.route("**/*", bloquear)

        # Listagem
        page_lista = context.new_page()
        links = coletar_links(page_lista, MAX_LINKS)
        page_lista.close()

        # Anúncios individuais
        anuncios = []
        bloqueados = 0
        for i, link in enumerate(links, 1):
            print(f"\n  [{i}/{len(links)}] {link[:80]}")
            page = context.new_page()
            dados = scrape_anuncio(page, link)
            page.close()

            if dados:
                anuncios.append(dados)
                print(f"    ✅ {dados['titulo'][:50]} | R${dados['preco']} | {dados['area']}m² | {dados['quartos']}q")
            else:
                bloqueados += 1
                print(f"    ⛔ Bloqueado ou sem dados — pulando ({bloqueados} até agora)")

        browser.close()

    print(f"\n✅ Total extraído: {len(anuncios)} | Bloqueados: {bloqueados}")
    salvar_csv(anuncios, OUTPUT_FILE)


if __name__ == "__main__":
    main()
