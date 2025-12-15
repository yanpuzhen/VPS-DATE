import requests
from bs4 import BeautifulSoup
import re
import json
import os
import concurrent.futures
from urllib.parse import urlparse, parse_qs

# CONFIG
BASE_URL = "https://my.racknerd.com"
MAX_PID = 1500 # Scan up to 1500

seen_urls = set()
products_lock = concurrent.futures.Lock() # Not strictly needed if using dict merge, but good practice
all_products = {}

def parse_specs(text, title):
    specs = {
        "ram": 0,    # MB
        "disk": "N/A",
        "cpu": 0,
        "bandwidth": "N/A",
        "location": "Global" 
    }
    
    text = text.lower() + " " + title.lower()
    
    # RAM
    ram_match = re.search(r'(\d+(?:\.\d+)?)\s*(mb|gb)\s*ram', text)
    if ram_match:
        val = float(ram_match.group(1))
        unit = ram_match.group(2)
        if unit == 'gb':
             specs['ram'] = int(val * 1024)
        else:
             specs['ram'] = val

    # CPU
    cpu_match = re.search(r'(\d+)\s*x?\s*(vcpu|vcore|core|cpu)', text)
    if cpu_match:
        specs['cpu'] = int(cpu_match.group(1))
    
    # Disk
    disk_match = re.search(r'(?:(\d+)\s*[xX]\s*)?(\d+)\s*(TB|GB)\s*(NVMe|SSD|HDD|Storage|Disk)', text, re.IGNORECASE)
    if disk_match:
        multiplier = int(disk_match.group(1)) if disk_match.group(1) else 1
        size_val = int(disk_match.group(2))
        unit = disk_match.group(3).upper()
        dtype = disk_match.group(4).upper()
        final_size = size_val * multiplier
        specs['disk'] = f"{final_size}{unit} {dtype}"
        if multiplier > 1: specs['disk'] = f"{multiplier}x {size_val}{unit} {dtype}"

    # Bandwidth
    bw_match = re.search(r'(\d+)\s*(TB|GB|MB)\s*Bandwidth', text, re.IGNORECASE)
    if bw_match:
        specs['bandwidth'] = f"{bw_match.group(1)} {bw_match.group(2).upper()}"
    elif "unlimited bandwidth" in text:
        specs['bandwidth'] = "Unlimited"

    return specs

def scrape_page(url, soup):
    """Scrapes all products found on a page (Listing or Config)."""
    found = []
    
    # 1. Check for Listing (Multiple Cards)
    cards = soup.select(".product") or soup.select(".package") or soup.select(".plan") or soup.select(".price-table")
    
    if cards:
        # It's a group/category listing
        for card in cards:
            try:
                # Title
                title_el = card.select_one("header span") or card.select_one("h3") or card.select_one("h4") or card.select_one(".name")
                if not title_el: continue
                title = title_el.get_text(strip=True)
                
                # Price
                price_el = card.select_one(".price") or card.select_one(".amt")
                price = price_el.get_text(strip=True) if price_el else "0.00"
                
                # Link
                btn = card.select_one("a.btn") or card.select_one("a.order-button")
                if not btn: continue
                link = BASE_URL + btn['href'] if btn['href'].startswith("/") else btn['href']
                
                # Description
                desc_el = card.select_one(".features") or card.select_one("ul") or card.select_one(".description")
                desc_text = desc_el.get_text(" ", strip=True) if desc_el else ""
                
                # Clean Price
                try:
                    clean_price = re.sub(r'[^\d\.]', '', price)
                    price_val = float(clean_price)
                except: price_val = 0.0
                
                specs = parse_specs(desc_text, title)
                performance_score = (specs['ram'] * 0.6) + (specs['cpu'] * 0.4)
                if performance_score == 0: continue
                value_score = performance_score / (price_val if price_val > 0 else 1)
                
                found.append({
                    "id": title, # Use title as ID
                    "title": title,
                    "price": price,
                    "specs": specs,
                    "description": desc_text[:200],
                    "purchase_url": link,
                    "value_score": value_score,
                    "raw_price": price_val
                })
            except: pass
            
    else:
        # 2. Check for Single Config Page (No list loop)
        # Verify valid product page
        if "Configure" in soup.get_text() or "Order Summary" in soup.get_text():
            # This is harder to parse generically, but usually implies ONE product
            # For RackNerd, usually redirects to listing, so this branch might be rare
            # If we hit it, we scrape H1 as title, etc.
            try:
                title = soup.select_one("h1").get_text(strip=True)
                price_el = soup.select_one("#order-summary .price")
                price = price_el.get_text(strip=True) if price_el else "0.00"
                
                specs = parse_specs(soup.get_text(), title)
                 # Clean Price
                try:
                    clean_price = re.sub(r'[^\d\.]', '', price)
                    price_val = float(clean_price)
                except: price_val = 0.0
                
                performance_score = (specs['ram'] * 0.6) + (specs['cpu'] * 0.4)
                if performance_score > 0:
                    found.append({
                        "id": title,
                        "title": title,
                        "price": price,
                        "specs": specs,
                        "description": "Single Product Page",
                        "purchase_url": url,
                        "value_score": performance_score / (price_val if price_val > 0 else 1),
                        "raw_price": price_val
                    })
            except: pass

    return found

def check_pid(pid):
    # Try both 'pid' and 'i' just in case? No, RackNerd uses pid/generic friendly
    # To find hidden pages, we visit the PID url.
    # If it redirects to a page we haven't seen, we scrape it.
    url = f"https://my.racknerd.com/cart.php?a=confproduct&i={pid}" # check_pid usually uses i or pid
    # RackNerd probe showed redirect to /store/...
    
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
    
    try:
        with requests.Session() as s:
            s.headers.update(headers)
            res = s.get(url, allow_redirects=True, timeout=10)
            
            final_url = res.url.split('?')[0] # Ignore query params for dedup
            
            # Check if we already scraped this Layout/Page
            if final_url in seen_urls:
                return []
            
            # Mark as seen
            seen_urls.add(final_url)
            
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, 'html.parser')
                # If page title is valid
                if "Shopping Cart" in soup.title.string or "RackNerd" in soup.title.string:
                     # Scrape content
                     items = scrape_page(res.url, soup)
                     if items:
                         print(f"PID {pid} found {len(items)} products on {final_url}")
                         return items
                         
    except Exception as e:
        # print(f"PID {pid} Error: {e}")
        pass
    return []

def scrape_all():
    print(f"Starting Hybrid PID Scan 0-{MAX_PID}...")
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        results = list(executor.map(check_pid, range(MAX_PID)))
        
    for r in results:
        for p in r:
            key = f"{p['title']}_{p['raw_price']}"
            all_products[key] = p
            
    final_list = list(all_products.values())
    print(f"Total Unique Products: {len(final_list)}")
    
    os.makedirs("public", exist_ok=True)
    final_list.sort(key=lambda x: x['value_score'], reverse=True)
    
    with open("public/rn.json", "w", encoding='utf-8') as f:
        json.dump(final_list, f, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    scrape_all()
