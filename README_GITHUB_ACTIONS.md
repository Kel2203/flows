# Setup GitHub Actions — Scraper Imóveis

## Estrutura do repositório

```
seu-repo/
├── scraper_local.py          ← coleta do VivaReal
├── upload_databricks.py      ← envia para o Databricks
└── .github/
    └── workflows/
        └── scraper.yml       ← agendamento 4x/dia
```

## Passo a passo

### 1. Criar o repositório no GitHub

Pode ser **privado** — o GitHub Actions gratuito funciona nos dois.

```bash
git init
git add .
git commit -m "primeiro commit"
git remote add origin https://github.com/seu-usuario/seu-repo.git
git push -u origin main
```

### 2. Configurar os Secrets no GitHub

Acesse: **seu repo → Settings → Secrets and variables → Actions → New repository secret**

Adicione os 3 secrets abaixo:

| Secret             | Onde encontrar                                              |
|--------------------|-------------------------------------------------------------|
| `DATABRICKS_HOST`  | URL do seu workspace, ex: `https://xxxx.cloud.databricks.com` |
| `DATABRICKS_TOKEN` | Databricks → Settings → Developer → Access Tokens → Generate |
| `WAREHOUSE_ID`     | Databricks → SQL Warehouses → seu warehouse → ID na URL     |

### 3. Encontrar o WAREHOUSE_ID

No Databricks Community Edition:
1. Menu esquerdo → **SQL Warehouses**
2. Clique no warehouse (ou crie um novo — o Starter gratuito serve)
3. A URL vai ser algo como: `.../sql/warehouses/abc123def456`
4. O `WAREHOUSE_ID` é esse código: `abc123def456`

### 4. Rodar manualmente o primeiro teste

No GitHub: **Actions → Scraper Imóveis → Run workflow → Run workflow**

Acompanhe os logs em tempo real para ver se o scraping e o upload funcionaram.

### 5. Agendamento automático

O workflow já está configurado para rodar 4x por dia automaticamente:
- 07:00 BRT
- 13:00 BRT  
- 18:00 BRT
- 23:00 BRT

Após o scraper rodar, execute manualmente no Databricks o `00_pipeline`
(ou só os notebooks `02_filter` em diante, já que os dados já estarão na tabela).

> 💡 **Dica:** Se quiser automatizar também os notebooks do Databricks após o upload,
> adicione um step no `scraper.yml` que chama a API REST do Databricks para
> disparar o `00_pipeline` após o upload terminar.
