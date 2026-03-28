"""
scraper_local.py
Extrai dados diretamente da página de listagem da OLX (sem abrir anúncios individuais),
já que o OLX bloqueia 100% das páginas individuais em ambientes de CI/datacenter.
Os dados da listagem (título, preço, área, quartos, endereço) são suficientes.
"""

import re
import time
import random
import csv
import os
import json
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

MAX_LINKS   = int(os.environ.get("MAX_LINKS", 20))
OUTPUT_FILE = os.environ.get("CSV_FILE", "imoveis.csv")

LIST_URL = (
    "https://www.olx.com.br/imoveis/venda/estado-sp/sao-paulo-e-regiao"
    "?pe=400000&sf=1&coe=1000&ipe=500&ss=30"
)

# Script de evasão de fingerprint - injeta em todas as páginas
STEALTH_JS = """
() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
    Object.defineProperty(navigator, 'languages', { get: () => ['pt-BR', 'pt', 'en-US', 'en'] });
    window.chrome = { runtime: {} };
    const orig = window.navigator.permissions.query;
    window.navigator.permissions.query = (p) =>
        p.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : orig(p);
}
"""

# ── Helpers de extração ──────────────────────────────────────────────────────

def extrair_preco(texto: str) -> float:
    """Extrai o primeiro valor R$ encontrado no texto."""
    match = re.search(r"R\$\s*[\d.,]+", texto)
    if not match:
        return 0.0
    return float(re.sub(r"[^\d]", "", match.group(0)))

def extrair_area(texto: str) -> float:
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*m²", texto)
    return float(match.group(1).replace(",", ".")) if match else 0.0

def extrair_quartos(texto: str) -> int:
    match = re.search(
        r"(\d+)\s*(?:quarto|dormitório|dorm|suíte|q)\s*s?", texto, re.IGNORECASE
    )
    return int(match.group(1)) if match else 0

def extrair_quartos_titulo(titulo: str) -> int:
    """Extrai quartos de padrões comuns no título: '2 dorm', '3 qtos', etc."""
    padroes = [
        r"(\d+)\s*(?:dorm|quarto|qto|suíte)",
        r"(\d+)\s*(?:dormitório|dormitorios)",
    ]
    for p in padroes:
        m = re.search(p, titulo, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return 0

def extrair_area_titulo(titulo: str) -> float:
    """Extrai área de padrões como '44m²', '44 m2', '44metros' no título."""
    padroes = [
        r"(\d+(?:[.,]\d+)?)\s*m²",
        r"(\d+(?:[.,]\d+)?)\s*m2",
        r"(\d+(?:[.,]\d+)?)\s*metros",
        r"com\s+(\d+)\s*m",
    ]
    for p in padroes:
        m = re.search(p, titulo, re.IGNORECASE)
        if m:
            return float(m.group(1).replace(",", "."))
    return 0.0

def extrair_endereco_texto(texto: str) -> str:
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

# ── Extração da listagem ─────────────────────────────────────────────────────

def debug_pagina(page) -> None:
    """Imprime diagnóstico da página atual para facilitar ajuste de seletores."""
    titulo = page.title()
    url    = page.url
    print(f"  [DEBUG] URL: {url}")
    print(f"  [DEBUG] Título: {titulo}")
    # Conta quantos links de anúncio existem com diferentes seletores
    for sel in ['a[data-testid="adcard-link"]', 'li[data-lurker-detail]', 'section li a']:
        try:
            count = page.eval_on_selector_all(sel, "els => els.length")
            print(f"  [DEBUG] seletor '{sel}': {count} elementos")
        except Exception:
            print(f"  [DEBUG] seletor '{sel}': erro")


def coletar_da_listagem(page, max_links: int) -> list[dict]:
    """
    Coleta dados diretamente da listagem sem abrir páginas individuais.
    Estratégia em camadas:
      1. JS direto nos cards (mais dados estruturados)
      2. Fallback: extrair do JSON do __NEXT_DATA__ (dados do Next.js)
      3. Fallback: regex no HTML bruto
    """

    # ── Warm-up humano ───────────────────────────────────────────────────────
    print("🌐 Warm-up na home do OLX...")
    page.goto("https://www.olx.com.br", wait_until="domcontentloaded", timeout=30000)
    time.sleep(random.uniform(2.5, 4.0))
    page.mouse.move(random.randint(200, 900), random.randint(100, 500))
    time.sleep(random.uniform(0.8, 1.5))

    # ── Página de listagem ───────────────────────────────────────────────────
    print("🔍 Acessando página de listagem...")
    page.goto(LIST_URL, wait_until="domcontentloaded", timeout=60000)

    print("⏳ Aguardando anúncios...")
    seletor_principal = 'a[data-testid="adcard-link"]'
    try:
        page.wait_for_selector(seletor_principal, timeout=90000)
    except PlaywrightTimeout:
        print("⚠️ Timeout no seletor principal")

    # Scroll para garantir que todos os cards carregaram
    for _ in range(4):
        page.keyboard.press("End")
        time.sleep(random.uniform(0.6, 1.2))
    page.keyboard.press("Home")
    time.sleep(1.0)

    debug_pagina(page)

    # ── Estratégia 1: __NEXT_DATA__ (JSON embutido pelo Next.js) ─────────────
    imoveis = _extrair_next_data(page)
    if imoveis:
        print(f"✅ {len(imoveis)} anúncios extraídos via __NEXT_DATA__")
        return imoveis[:max_links]

    # ── Estratégia 2: JS direto nos cards ────────────────────────────────────
    imoveis = _extrair_via_js(page)
    if imoveis:
        print(f"✅ {len(imoveis)} anúncios extraídos via JS nos cards")
        return imoveis[:max_links]

    # ── Estratégia 3: Regex no HTML bruto ────────────────────────────────────
    imoveis = _extrair_via_regex(page)
    if imoveis:
        print(f"✅ {len(imoveis)} anúncios extraídos via regex no HTML")
        return imoveis[:max_links]

    print("❌ Nenhuma estratégia funcionou.")
    return []


def _extrair_next_data(page) -> list[dict]:
    """
    Extrai anúncios de .props.pageProps.ads no __NEXT_DATA__ do OLX.
    Campos confirmados: subject, priceValue, oldPrice, friendlyUrl, location.
    Área e quartos vêm do subject (título), pois não há campo separado na listagem.
    """
    try:
        raw = page.evaluate("""
        () => {
            const el = document.getElementById('__NEXT_DATA__');
            return el ? el.textContent : null;
        }
        """)
        if not raw:
            return []

        data = json.loads(raw)

        # Caminho confirmado: .props.pageProps.ads
        ads = (
            data.get("props", {})
                .get("pageProps", {})
                .get("ads", [])
        )

        # Fallback: busca genérica se o caminho mudar no futuro
        if not ads:
            def buscar_ads(obj, depth=0):
                if depth > 6 or not isinstance(obj, (dict, list)):
                    return []
                if isinstance(obj, list) and obj and isinstance(obj[0], dict):
                    if "subject" in obj[0] and "priceValue" in obj[0]:
                        return obj
                if isinstance(obj, dict):
                    for k in ["ads", "adList", "listings", "results"]:
                        if k in obj and isinstance(obj[k], list) and obj[k]:
                            found = buscar_ads(obj[k], depth + 1)
                            if found:
                                return found
                    for v in obj.values():
                        found = buscar_ads(v, depth + 1)
                        if found:
                            return found
                return []
            ads = buscar_ads(data)

        if not ads:
            print("  [DEBUG] Nenhum anúncio encontrado no __NEXT_DATA__")
            return []

        resultado = []
        for ad in ads:
            if not isinstance(ad, dict):
                continue

            # ── Link ────────────────────────────────────────────────────────
            link = ad.get("friendlyUrl") or ad.get("url") or ad.get("link") or ""
            if not link or "olx.com.br" not in link:
                continue

            # ── Título ──────────────────────────────────────────────────────
            titulo = ad.get("subject") or ad.get("title") or ""

            # ── Preço ───────────────────────────────────────────────────────
            # priceValue vem como "R$ 335.000" — remover tudo que não for dígito
            preco_raw = ad.get("priceValue") or ""
            preco = float(re.sub(r"[^\d]", "", str(preco_raw)) or 0)

            # Preço anterior (quando há queda de preço)
            old_raw = ad.get("oldPrice") or ""
            preco_anterior = float(re.sub(r"[^\d]", "", str(old_raw)) or 0)

            # ── Endereço ────────────────────────────────────────────────────
            # location é um dict com neighbourhood, municipality, state
            loc = ad.get("location") or {}
            if isinstance(loc, dict):
                bairro    = loc.get("neighbourhood") or loc.get("neighborhood") or ""
                municipio = loc.get("municipality") or loc.get("city") or ""
                estado    = loc.get("uf") or loc.get("state") or ""
                partes = [p for p in [bairro, municipio] if p]
                endereco = ", ".join(partes)
                if estado and estado.upper() not in endereco.upper():
                    endereco = f"{endereco}, {estado}" if endereco else estado
            else:
                endereco = str(loc).strip()

            if not endereco:
                endereco = normalizar_endereco(extrair_endereco_texto(titulo))
            else:
                endereco = normalizar_endereco(endereco)

            # ── Área e Quartos ───────────────────────────────────────────────
            # Não há campos separados na listagem — extrair do título
            # Padrões comuns no OLX:
            #   "52 metros quadrados com 2 quartos"
            #   "Apartamento com 40m², 2 quartos"
            #   "LINDO APARTAMENTO 3 DORMITÓRIOS 94 M²"
            area    = extrair_area(titulo) or extrair_area_titulo(titulo)
            quartos = extrair_quartos(titulo) or extrair_quartos_titulo(titulo)

            resultado.append({
                "link":           link,
                "titulo":         titulo,
                "preco":          preco,
                "preco_anterior": preco_anterior,
                "endereco":       endereco,
                "area":           area,
                "quartos":        quartos,
            })

        return resultado

    except Exception as e:
        print(f"  [DEBUG] __NEXT_DATA__ falhou: {e}")
        return []


def _extrair_via_js(page) -> list[dict]:
    """Extrai dados diretamente dos elementos DOM dos cards."""
    try:
        resultado = page.evaluate("""
        () => {
            const itens = [];
            const links = document.querySelectorAll('a[data-testid="adcard-link"]');

            links.forEach(a => {
                try {
                    const card = a.closest('li') || a.closest('section') || a.parentElement;
                    const textoCard = card ? (card.innerText || '') : '';

                    // Link
                    const link = a.href || '';

                    // Título - aria-label é mais confiável
                    let titulo = a.getAttribute('aria-label') || '';
                    if (!titulo) {
                        const h = card && (card.querySelector('h2') || card.querySelector('h3'));
                        titulo = h ? (h.innerText || '').trim() : '';
                    }
                    if (!titulo) titulo = (a.innerText || '').trim();

                    // Preço - tentar vários seletores
                    let preco = 0;
                    const precoSels = [
                        '[class*="price"],[class*="Price"]',
                        'h3', 'strong', 'b',
                        '[data-testid*="price"]'
                    ];
                    for (const sel of precoSels) {
                        const el = card && card.querySelector(sel);
                        if (el) {
                            const t = el.innerText || '';
                            const m = t.match(/R\\$\\s*[\\d.,]+/);
                            if (m) {
                                preco = Number(m[0].replace(/\\D/g, ''));
                                break;
                            }
                        }
                    }
                    // Último recurso: regex no texto completo do card
                    if (!preco) {
                        const m = textoCard.match(/R\\$\\s*[\\d.,]+/);
                        if (m) preco = Number(m[0].replace(/\\D/g, ''));
                    }

                    // Endereço
                    let endereco = '';
                    const locSels = [
                        '[class*="location"],[class*="Location"]',
                        '[class*="address"],[class*="Address"]',
                        '[data-testid*="location"]',
                        'p'
                    ];
                    for (const sel of locSels) {
                        const el = card && card.querySelector(sel);
                        if (el) {
                            const t = (el.innerText || '').trim();
                            if (t && t.length < 80) { endereco = t; break; }
                        }
                    }

                    // Área e quartos do texto do card
                    const areaM = textoCard.match(/(\\d+(?:[.,]\\d+)?)\\s*m²/);
                    const area = areaM ? parseFloat(areaM[1].replace(',', '.')) : 0;

                    const qtM = textoCard.match(/(\\d+)\\s*(?:quarto|dorm|suíte)/i);
                    const quartos = qtM ? parseInt(qtM[1]) : 0;

                    itens.push({ link, titulo: titulo.trim(), preco, endereco: endereco.trim(), area, quartos });
                } catch(e) {}
            });

            return itens;
        }
        """)

        # Filtrar itens sem link
        return [i for i in (resultado or []) if i.get("link")]

    except Exception as e:
        print(f"  [DEBUG] JS nos cards falhou: {e}")
        return []


def _extrair_via_regex(page) -> list[dict]:
    """Fallback: extrai links e dados básicos do HTML bruto via regex Python."""
    try:
        html = page.content()

        links = re.findall(
            r'href="(https://sp\.olx\.com\.br/sao-paulo-e-regiao/imoveis/[^"]+)"',
            html
        )
        links = list(dict.fromkeys(links))  # dedup mantendo ordem

        resultado = []
        for link in links:
            # Tentar pegar título da URL (slug)
            slug = link.rstrip("/").split("/")[-1]
            titulo = slug.replace("-", " ").title()

            resultado.append({
                "link":     link,
                "titulo":   titulo,
                "preco":    0.0,
                "endereco": "São Paulo",
                "area":     extrair_area_titulo(titulo),
                "quartos":  extrair_quartos_titulo(titulo),
                "preco_anterior": 0.0,
            })

        return resultado

    except Exception as e:
        print(f"  [DEBUG] Regex no HTML falhou: {e}")
        return []


# ── Persistência ─────────────────────────────────────────────────────────────

def montar_registro(item: dict) -> dict:
    return {
        "link":           item.get("link", ""),
        "fonte":          "olx",
        "titulo":         item.get("titulo", ""),
        "endereco":       normalizar_endereco(item.get("endereco", "")),
        "descricao":      "",
        "preco":          item.get("preco", 0.0),
        "preco_anterior": item.get("preco_anterior", 0.0),
        "area":           item.get("area", 0.0),
        "quartos":        item.get("quartos", 0),
        "scraped_at":     datetime.utcnow().isoformat(),
    }


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


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
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
            extra_http_headers={
                "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Linux"',
            }
        )
        context.add_init_script(STEALTH_JS)

        # Bloqueia apenas imagem/fonte/mídia — manter CSS para não parecer headless
        def bloquear(route):
            if route.request.resource_type in ["image", "font", "media"]:
                route.abort()
            else:
                route.continue_()
        context.route("**/*", bloquear)

        page = context.new_page()
        itens = coletar_da_listagem(page, MAX_LINKS)
        page.close()
        browser.close()

    if not itens:
        print("❌ Nenhum anúncio coletado.")
        return

    # Montar registros e mostrar diagnóstico
    sem_preco  = 0
    sem_area   = 0
    registros  = []
    for item in itens:
        r = montar_registro(item)
        registros.append(r)
        if r["preco"] == 0:
            sem_preco += 1
        if r["area"] == 0:
            sem_area += 1
        print(
            f"  {'✅' if r['preco'] > 0 else '⚠️ '} {r['titulo'][:50]:<50} | "
            f"R${r['preco']:>9,.0f} | {r['area']:>5.0f}m² | {r['quartos']}q | {r['endereco'][:30]}"
        )

    print(f"\n📊 Total: {len(registros)} | Sem preço: {sem_preco} | Sem área: {sem_area}")
    salvar_csv(registros, OUTPUT_FILE)


if __name__ == "__main__":
    main()
