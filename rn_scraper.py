import requests
from bs4 import BeautifulSoup
import re
import json
import os
import concurrent.futures
import threading
from urllib.parse import urlparse, parse_qs

# CONFIG
BASE_URL = "https://my.racknerd.com"
MAX_PID = 2000 # Standard Range

seen_urls = set()
# ... (omitted)


# ...

def crawl_categories():
    # ...
            for l in links:
                href = l.get('href')
                if href and 'rp=/store' in href:
                    # FILTER: skip shared/reseller categories
                    h_lower = href.lower()
                    if "shared" in h_lower or "reseller" in h_lower or "web-hosting" in h_lower: continue

# ...

def check_pid(pid):
    # ...
            # FILTER: skip shared/reseller redirects
            f_lower = final_url.lower()
            if "shared" in f_lower or "reseller" in f_lower or "web-hosting" in f_lower: return []

url_lock = threading.Lock() 
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
    
    ram_match = re.search(r'(\d+(?:\.\d+)?)\s*(mb|gb)\s*ram', text)
    if ram_match:
        val = float(ram_match.group(1))
        unit = ram_match.group(2)
        if unit == 'gb': specs['ram'] = int(val * 1024)
        else: specs['ram'] = val

    # Custom RackNerd patterns
    if specs['cpu'] == 0:
        # "16x 2.60 GHz"
        c_desc = re.search(r'(\d+)\s*x\s*[\d\.]+\s*GHz', text, re.IGNORECASE)
        if c_desc: specs['cpu'] = int(c_desc.group(1))
        
        # "Dual Intel..." (Count Threads or assume 2x4=8 at least?)
        # Better to look for "32x Threads"
        c_threads = re.search(r'(\d+)\s*x\s*Threads', text, re.IGNORECASE)
        if c_threads: 
            # Treat threads as vCPUs for scoring
            specs['cpu'] = int(c_threads.group(1))

    # Existing logic...
    cpu_match = re.search(r'(\d+)\s*x?\s*(?:vcpu|vcore|core|cpu)', text, re.IGNORECASE)
    if cpu_match: specs['cpu'] = int(cpu_match.group(1))
    
    disk_match = re.search(r'(?:(\d+)\s*[xX]\s*)?(\d+)\s*(TB|GB)\s*(NVMe|SSD|HDD|Storage|Disk)', text, re.IGNORECASE)
    if disk_match:
        multiplier = int(disk_match.group(1)) if disk_match.group(1) else 1
        size_val = int(disk_match.group(2))
        unit = disk_match.group(3).upper()
        dtype = disk_match.group(4).upper()
        final_size = size_val * multiplier
        specs['disk'] = f"{final_size}{unit} {dtype}"
        if multiplier > 1: specs['disk'] = f"{multiplier}x {size_val}{unit} {dtype}"

    bw_match = re.search(r'(\d+)\s*(TB|GB|MB)\s*Bandwidth', text, re.IGNORECASE)
    if bw_match: specs['bandwidth'] = f"{bw_match.group(1)} {bw_match.group(2).upper()}"
    elif "unlimited bandwidth" in text: specs['bandwidth'] = "Unlimited"
        
    if "la" in text or "los angeles" in text: specs['location'] = "Los Angeles"
    elif "sanjose" in text or "san jose" in text: specs['location'] = "San Jose"
    elif "dallas" in text: specs['location'] = "Dallas"
    elif "chicago" in text: specs['location'] = "Chicago"
    elif "new york" in text or "ny" in text: specs['location'] = "New York"
    elif "seattle" in text: specs['location'] = "Seattle"
    elif "atlanta" in text: specs['location'] = "Atlanta"
    elif "ashburn" in text: specs['location'] = "Ashburn"
    elif "miami" in text: specs['location'] = "Miami"
    elif "strasbourg" in text: specs['location'] = "France"
    elif "frankfurt" in text: specs['location'] = "Germany"
    elif "singapore" in text: specs['location'] = "Singapore"

    return specs

def scrape_page(url, soup):
    found = []
    
    cards = soup.select(".product") or soup.select(".package") or soup.select(".plan") or soup.select(".price-table")
    
    if cards:
        for card in cards:
            try:
                title_el = card.select_one("header span") or card.select_one("h3") or card.select_one("h4") or card.select_one(".name")
                if not title_el: continue
                title = title_el.get_text(strip=True)
                
                price_el = card.select_one(".price") or card.select_one(".amt")
                price = price_el.get_text(strip=True) if price_el else "0.00"
                
                btn = card.select_one("a.btn") or card.select_one("a.order-button")
                if not btn: continue
                link = BASE_URL + btn['href'] if btn['href'].startswith("/") else btn['href']
                
                # FIX: Add .product-desc and p selector
                desc_el = card.select_one(".product-desc p") or card.select_one(".product-desc") or card.select_one(".features") or card.select_one("ul") or card.select_one(".description")
                desc_text = desc_el.get_text(" ", strip=True) if desc_el else ""
                
                try: clean_price = re.sub(r'[^\d\.]', '', price); price_val = float(clean_price)
                except: price_val = 0.0
                
                
                # CPU Regex Enhancement (Match "1x AMD Ryzen CPU Core")
                specs = parse_specs(desc_text, title) # Call parse_specs first to get initial values
                if specs['cpu'] == 0:
                    spec_cpu = re.search(r'(\d+)\s*x?\s*(?:[A-Za-z0-9\-\.]+\s+){0,4}(?:vCPU|vCore|Core|CPU)', desc_text, re.IGNORECASE)
                    if spec_cpu: specs['cpu'] = int(spec_cpu.group(1))

                performance_score = (specs['ram'] * 0.6) + (specs['cpu'] * 0.4)
                
                # FILTER: Remove "Shared/Reseller" products
                # Do not ban "hosting" generically, as "VPS Hosting" is valid.
                t_lower = title.lower()
                blacklist = ["shared hosting", "reseller hosting", "web hosting", "email hosting", "cpanel", "directadmin"]
                if any(x in t_lower for x in blacklist): continue
                
                # FILTER: Remove if no RAM (REMOVED per user request "Memory match not advisable")
                # if specs['ram'] == 0: continue
                    
                if performance_score == 0 and specs['disk'] == "N/A": continue
                    
                value_score = performance_score / (price_val if price_val > 0 else 1)
                
                found.append({
                    "id": title, 
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
        if "Configure" in soup.get_text() or "Order Summary" in soup.get_text():
            try:
                title_el = soup.select_one("h1")
                if title_el:
                    title = title_el.get_text(strip=True)
                    price_el = soup.select_one("#order-summary .price")
                    price = price_el.get_text(strip=True) if price_el else "0.00"
                    
                    specs = parse_specs(soup.get_text(), title)
                    try: clean_price = re.sub(r'[^\d\.]', '', price); price_val = float(clean_price)
                    except: price_val = 0.0
                    
                    performance_score = (specs['ram'] * 0.6) + (specs['cpu'] * 0.4)
                    if performance_score == 0 and specs['disk'] != "N/A": performance_score = 100
                    
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

def crawl_categories():
    print("Crawling Store Categories...")
    url = f"{BASE_URL}/index.php?rp=/store"
    try:
        res = requests.get(url, timeout=15, headers={'User-Agent': 'Mozilla/5.0...'})
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            # Sidebar categories or Main list
            links = soup.select(".list-group-item") or soup.select(".nav-link")
            print(f"Found {len(links)} potential category links.")
            
            for l in links:
                href = l.get('href')
                if href and 'rp=/store' in href:
                    # FILTER: skip shared/reseller categories
                    h_lower = href.lower()
                    if "shared" in h_lower or "reseller" in h_lower or "web-hosting" in h_lower: continue
                    
                    full_url = BASE_URL + href if href.startswith('/') else href
                    
                    with url_lock:
                        if full_url in seen_urls: continue
                        seen_urls.add(full_url)
                    
                    # Scrape this category
                    try:
                        print(f"Scraping Category: {full_url}")
                        cat_res = requests.get(full_url, timeout=15, headers={'User-Agent': 'Mozilla/5.0...'})
                        cat_soup = BeautifulSoup(cat_res.text, 'html.parser')
                        items = scrape_page(full_url, cat_soup)
                        if items:
                            print(f"Category found {len(items)} items.", flush=True)
                            for p in items:
                                key = f"{p['title']}_{p['raw_price']}"
                                all_products[key] = p
                    except Exception as e:
                        print(f"Cat Error {full_url}: {e}")
                        
    except Exception as e:
        print(f"Crawl Error: {e}")

def check_pid(pid):
    url = f"https://my.racknerd.com/cart.php?a=confproduct&i={pid}"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
    
    try:
        # print(f"Checking PID {pid}...", flush=True)
        with requests.Session() as s:
            s.headers.update(headers)
            res = s.get(url, allow_redirects=True, timeout=10)
            
            # FIX: Do not strip query params! RackNerd uses index.php?rp=...
            final_url = res.url 
            
            # FILTER: skip shared/reseller redirects
            f_lower = final_url.lower()
            if "shared" in f_lower or "reseller" in f_lower or "web-hosting" in f_lower: return []
            
            with url_lock:
                if final_url in seen_urls:
                    return []
                seen_urls.add(final_url)
            
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, 'html.parser')
                if "Shopping Cart" in soup.title.string or "RackNerd" in soup.title.string:
                     items = scrape_page(res.url, soup)
                     if items:
                         print(f"PID {pid} found {len(items)} products on {final_url}", flush=True)
                         return items
                         
    except Exception as e:
        print(f"Error {pid}: {e}", flush=True)
    return []

def scrape_all():
    crawl_categories() # Step 1: Discover known categories
    
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
