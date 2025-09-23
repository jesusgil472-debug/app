import os
import asyncio
from urllib.parse import quote
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List
from playwright.async_api import async_playwright

# Configuración para Render
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "/tmp/playwright"

app = FastAPI()

SEARCH_URL = "https://spinetohogar.com/search?options%5Bprefix%5D=last&q="

# --- Helpers ---
async def get_text(page, selectors):
    for sel in selectors:
        try:
            el = await page.query_selector(sel)
            if el:
                txt = await el.text_content()
                if txt and txt.strip():
                    return txt.strip()
        except:
            continue
    return ""

def normalize_sku(s: str) -> str:
    return (s or "").strip().lower().replace(" ", "").replace("#", "")

async def extract_sku(page):
    sku_raw = await get_text(page, [
        "span.product__sku.fs-body-50.t-opacity-70",
        "span.product__sku",
        ".product-sku",
        ".sku",
        "div.product__sku"
    ])
    if not sku_raw:
        return ""
    return sku_raw.replace("SKU:", "").strip()

async def extract_image_url(page):
    imgs = await page.query_selector_all("img")
    for img in imgs:
        src = await img.get_attribute("src")
        if src and "/cdn/" in src and not any(x in src for x in ["Logo", "Navidad", "banner"]):
            if src.startswith("//"):
                return "https:" + src
            elif src.startswith("/"):
                return "https://spinetohogar.com" + src
            return src
    return "No disponible"

async def extract_price(page):
    try:
        await page.wait_for_selector("span.mw-price", timeout=8000)
        el = await page.query_selector("span.mw-price")
        if el:
            txt = (await el.text_content() or "").strip()
            if txt and not txt.endswith("0,00"):
                return txt
    except:
        pass

    try:
        meta = await page.query_selector("meta[property='og:price:amount']")
        if meta:
            val = await meta.get_attribute("content")
            if val:
                return f"US$ {val}"
    except:
        pass

    return "Precio no disponible"

async def extract_product_details(page, url, sku_input):
    name = await get_text(page, ["h1.product__title", "h1.product-title", "h1"])
    price = await extract_price(page)
    brand = await get_text(page, ["div.product__vendor", "a.product__vendor", ".vendor"])
    sku = await extract_sku(page)
    image_url = await extract_image_url(page)

    return {
        "found": True,
        "match": normalize_sku(sku) == normalize_sku(sku_input),
        "url": url,
        "name": name or "Nombre no disponible",
        "price": price,
        "brand": brand or "",
        "sku": sku or "SKU no disponible",
        "image_url": image_url,
    }

async def buscar_por_skus(lista_skus, headless=True):
    resultados = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context()
        page = await context.new_page()

        for sku in lista_skus:
            query = quote(sku)
            url_busqueda = f"{SEARCH_URL}{query}"
            await page.goto(url_busqueda, timeout=30000)
            await page.wait_for_load_state("domcontentloaded")

            enlaces = await page.query_selector_all("a[href*='/products/']")
            if not enlaces:
                resultados.append({"sku": sku, "found": False, "message": "Sin resultados"})
                continue

            hrefs = []
            seen = set()
            for a in enlaces:
                href = await a.get_attribute("href")
                if href and "/products/" in href:
                    if href.startswith("/"):
                        href = "https://spinetohogar.com" + href
                    if href not in seen:
                        seen.add(href)
                        hrefs.append(href)

            encontrado = False
            for product_url in hrefs:
                prod_page = await context.new_page()
                try:
                    await prod_page.goto(product_url, timeout=30000)
                    await prod_page.wait_for_load_state("domcontentloaded")
                    detalles = await extract_product_details(prod_page, product_url, sku)
                    if detalles["match"]:
                        resultados.append(detalles)
                        encontrado = True
                        break
                finally:
                    await prod_page.close()

            if not encontrado:
                resultados.append({"sku": sku, "found": False, "message": "SKU no coincide en ningún producto"})

        await browser.close()
    return resultados

# --- Modelo para POST ---
class SKURequest(BaseModel):
    skus: List[str]

# --- Endpoint GET ---
@app.get("/buscar")
async def buscar(skus: List[str] = Query(...)):
    try:
        return await buscar_por_skus(skus)
    except Exception as e:
        return {"error": str(e)}

# --- Endpoint POST ---
@app.post("/buscar")
async def buscar_post(data: SKURequest):
    try:
        return await buscar_por_skus(data.skus)
    except Exception as e:
        return {"error": str(e)}

# --- Ruta raíz ---
@app.get("/")
def home():
    return {"message": "Bienvenido a la API de scraping. Usa /buscar?skus=... o POST /buscar para consultar."}
