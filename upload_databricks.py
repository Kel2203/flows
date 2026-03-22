"""
upload_databricks.py
Lê o imoveis.csv gerado pelo scraper e insere na tabela Delta via
Databricks SQL Statement API (não precisa de driver JDBC nem Spark).
"""

import os
import csv
import json
import time
import requests
from datetime import datetime

# ── Configuração via variáveis de ambiente (GitHub Secrets) ───
DATABRICKS_HOST  = os.environ["DATABRICKS_HOST"]   # ex: https://xxxx.cloud.databricks.com
DATABRICKS_TOKEN = os.environ["DATABRICKS_TOKEN"]  # Personal Access Token
WAREHOUSE_ID     = os.environ["WAREHOUSE_ID"]      # SQL Warehouse ID
CATALOG          = os.environ.get("CATALOG", "workspace")
SCHEMA           = os.environ.get("SCHEMA",  "imoveis")
TABLE            = f"{CATALOG}.{SCHEMA}.imoveis_raw"
CSV_FILE         = os.environ.get("CSV_FILE", "imoveis.csv")

HEADERS = {
    "Authorization": f"Bearer {DATABRICKS_TOKEN}",
    "Content-Type":  "application/json",
}

SQL_API = f"{DATABRICKS_HOST}/api/2.0/sql/statements"


def executar_sql(statement: str, parametros: list = None) -> dict:
    """Executa um SQL statement via Databricks SQL API e aguarda conclusão."""
    body = {
        "warehouse_id": WAREHOUSE_ID,
        "statement":    statement,
        "wait_timeout": "30s",
        "on_wait_timeout": "CONTINUE",
    }
    if parametros:
        body["parameters"] = parametros

    resp = requests.post(SQL_API, headers=HEADERS, json=body, timeout=60)
    resp.raise_for_status()
    result = resp.json()

    # Polling enquanto estiver rodando
    statement_id = result.get("statement_id")
    while result.get("status", {}).get("state") in ("PENDING", "RUNNING"):
        time.sleep(2)
        poll = requests.get(
            f"{SQL_API}/{statement_id}",
            headers=HEADERS,
            timeout=30
        )
        poll.raise_for_status()
        result = poll.json()

    state = result.get("status", {}).get("state")
    if state != "SUCCEEDED":
        erro = result.get("status", {}).get("error", {})
        raise RuntimeError(f"SQL falhou ({state}): {erro.get('message', '')}")

    return result


def ler_csv(path: str) -> list[dict]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Arquivo {path} não encontrado.")
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def fazer_upload(imoveis: list[dict]):
    if not imoveis:
        print("⚠️ Nenhum imóvel para enviar.")
        return

    print(f"📤 Enviando {len(imoveis)} imóveis para {TABLE}...")

    # Garantir que a tabela existe
    executar_sql(f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            link           STRING,
            fonte          STRING,
            titulo         STRING,
            endereco       STRING,
            descricao      STRING,
            preco          DOUBLE,
            preco_anterior DOUBLE,
            area           DOUBLE,
            quartos        INT,
            scraped_at     TIMESTAMP
        ) USING DELTA
    """)
    print("✅ Tabela verificada.")

    # Inserir em lotes de 50 para não estourar o limite da API
    LOTE = 50
    inseridos = 0

    for i in range(0, len(imoveis), LOTE):
        lote = imoveis[i:i + LOTE]

        # Montar VALUES como string SQL
        valores = []
        for im in lote:
            def esc(v):
                return str(v).replace("'", "''") if v else ""

            valores.append(
                f"('{esc(im['link'])}', '{esc(im['fonte'])}', '{esc(im['titulo'])}', "
                f"'{esc(im['endereco'])}', '{esc(im['descricao'])}', "
                f"{float(im['preco'] or 0)}, {float(im['preco_anterior'] or 0)}, "
                f"{float(im['area'] or 0)}, {int(float(im['quartos'] or 0))}, "
                f"'{esc(im['scraped_at'])}')"
            )

        sql = f"""
            MERGE INTO {TABLE} AS target
            USING (
                SELECT * FROM VALUES
                {','.join(valores)}
                AS t(link, fonte, titulo, endereco, descricao,
                      preco, preco_anterior, area, quartos, scraped_at)
            ) AS source
            ON target.link = source.link
            WHEN NOT MATCHED THEN INSERT *
        """

        executar_sql(sql)
        inseridos += len(lote)
        print(f"  ✅ Lote {i // LOTE + 1}: {inseridos}/{len(imoveis)} inseridos")

    print(f"\n🎉 Upload concluído: {inseridos} imóveis enviados para {TABLE}")


if __name__ == "__main__":
    print(f"🚀 Iniciando upload para {TABLE}")
    imoveis = ler_csv(CSV_FILE)
    print(f"📋 {len(imoveis)} imóveis lidos de {CSV_FILE}")
    fazer_upload(imoveis)
