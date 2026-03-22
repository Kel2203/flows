"""
scraper_local.py
Roda no GitHub Actions — coleta imóveis do VivaReal e salva em imoveis.csv
"""

import requests
import time
import random
import csv
import os
from datetime import datetime

# ── Configuração ──────────────────────────────────────────────
MAX_IMOVEIS = int(os.environ.get("MAX_IMOVEIS", 50))
OUTPUT_FILE = "imoveis.csv"

VIVAREAL_API = "https://glue-api.vivareal.com/v2/listings"

PARAMS_BASE = {
    "addressCity":    "São Paulo",
    "addressState":   "São Paulo",
    "addressCountry": "Brasil",
    "business":       "SALE",
    "unitTypes":      "APARTMENT",
    "unitSubTypes":   "UnitSubType_NONE,APARTMENT,PENTHOUSE,FLAT",
    "priceMax":       400000,
    "areaMin":        30,
    "bedrooms":       "1,2,3,4",
    "listingType":    "USED",
    "parentId":       "null",
    "categoryPage":   "RESULT",
    "size":           24,
    "from":           0,
    "q":              "",
    "sort":           "publicationDate",
    "sortOrder":      "DESC",
}

# ── Sessão com cookies ────────────────────────────────────────
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent":         "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept":             "application/json, text/plain, */*",
    "Accept-Language":    "pt-BR,pt;q=0.9",
    "Origin":             "https://www.vivareal.com.br",
    "Referer":            "https://www.vivareal.com.br/venda/sp/sao-paulo/apartamento_residencial/",
    "X-Domain":           "www.vivareal.com.br",
    "sec-ch-ua":          '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    "sec-ch-ua-mobile":   "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest":     "empty",
    "Sec-Fetch-Mode":     "cors",
    "Sec-Fetch-Site":     "same-site",
})


def aquecer_sessao():
    print("🌐 Aquecendo sessão...")
    SESSION.get("https://www.vivareal.com.br", timeout=20)
    time.sleep(random.uniform(2.0, 4.0))
    SESSION.get(
        "https://www.vivareal.com.br/venda/sp/sao-paulo/apartamento_residencial/",
        timeout=20
    )
    time.sleep(random.uniform(1.5, 3.0))
    print(f"✅ Cookies: {list(SESSION.cookies.keys())}")


def extrair_imovel(listing: dict) -> dict | None:
    try:
        ld     = listing.get("listing", {})
        prices = ld.get("pricingInfos", [{}])
        preco  = 0.0
        for p in prices:
            if p.get("businessType") == "SALE":
                preco = float(p.get("price", 0) or 0)
                break

        area    = float((ld.get("usableAreas") or ld.get("totalAreas") or [0])[0] or 0)
        quartos = int((ld.get("bedrooms") or [0])[0] or 0)

        loc      = ld.get("address", {})
        bairro   = loc.get("neighborhood", "")
        cidade   = loc.get("city", "São Paulo")
        estado   = loc.get("stateAcronym", "SP")
        endereco = f"{bairro}, {cidade} - {estado}".strip(", ")

        titulo    = ld.get("title", "") or ""
        descricao = (ld.get("description", "") or "")[:2000]
        slug      = ld.get("slug", ld.get("id", ""))
        link      = f"https://www.vivareal.com.br/imovel/{slug}/"

        return {
            "link":           link,
            "fonte":          "vivareal",
            "titulo":         titulo,
            "endereco":       endereco,
            "descricao":      descricao,
            "preco":          preco,
            "preco_anterior": 0.0,
            "area":           area,
            "quartos":        quartos,
            "scraped_at":     datetime.utcnow().isoformat(),
        }
    except Exception as e:
        print(f"  ⚠️ Erro ao extrair: {e}")
        return None


def coletar_imoveis(max_imoveis: int) -> list[dict]:
    imoveis = []
    pagina  = 0

    while len(imoveis) < max_imoveis:
        params = {**PARAMS_BASE, "from": pagina * PARAMS_BASE["size"]}
        print(f"📄 Página {pagina + 1}...")

        try:
            resp = SESSION.get(VIVAREAL_API, params=params, timeout=30)
            print(f"  HTTP {resp.status_code}")

            if resp.status_code != 200:
                print(f"  ❌ Bloqueado: {resp.status_code}")
                break

            listings = (
                resp.json()
                    .get("search", {})
                    .get("result", {})
                    .get("listings", [])
            )

            if not listings:
                print("  ℹ️ Sem mais resultados.")
                break

            for listing in listings:
                imovel = extrair_imovel(listing)
                if imovel:
                    imoveis.append(imovel)
                if len(imoveis) >= max_imoveis:
                    break

            print(f"  ✅ {len(imoveis)} coletados")
            pagina += 1
            time.sleep(random.uniform(1.5, 3.0))

        except Exception as e:
            print(f"  ❌ Erro: {e}")
            break

    return imoveis


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


if __name__ == "__main__":
    aquecer_sessao()
    imoveis = coletar_imoveis(MAX_IMOVEIS)
    salvar_csv(imoveis, OUTPUT_FILE)
    print(f"\n✅ Scraping concluído: {len(imoveis)} imóveis")
