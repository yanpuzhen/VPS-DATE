import requests
from bs4 import BeautifulSoup
import re
import json
import os
import concurrent.futures

# CONFIG
BASE_URL = "https://cloud.colocrossing.com"
STORE_HOME = "https://cloud.colocrossing.com/index.php?rp=/store"

def parse_specs(text, title):
    specs = {
        "ram": 0,    # MB
        "disk": "N/A",
        "cpu": 0,
        "bandwidth": "N/A",
        "location": "Buffalo" # Default
    }
    
    # Normalize text
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
        
    # Location
    if "ny" in text or "new york" in text or "buffalo" in text: specs['location'] = "New York"
    elif "la" in text or "los angeles" in text: specs["location"] = "Los Angeles"
    elif "dallas" in text: specs["location"] = "Dallas"
    elif "chicago" in text: specs["location"] = "Chicago"
    elif "atlanta" in text: specs["location"] = "Atlanta"
    elif "seattle" in text: specs["location"] = "Seattle"
    elif "san jose" in text: specs["location"] = "San Jose"
    
    # Disk
    disk_match = re.search(r'(?:(\d+)\s*[xX]\s*)?(\d+)\s*(TB|GB)\s*(NVMe|SSD|HDD|Storage|Disk)', text, re.IGNORECASE)
    if disk_match:
        multiplier = int(disk_match.group(1)) if disk_match.group(1) else 1
        size_val = int(disk_match.group(2))
        unit = disk_match.group(3).upper()
        dtype = disk_match.group(4).upper()
        
        final_size = size_val * multiplier
        specs['disk'] = f"{final_size}{unit} {dtype}"
        if multiplier > 1:
             specs['disk'] = f"{multiplier}x {size_val}{unit} {dtype}"

    # Bandwidth
    bw_match = re.search(r'(\d+)\s*(TB|GB|MB)\s*Bandwidth', text, re.IGNORECASE)
    if bw_match:
        specs['bandwidth'] = f"{bw_match.group(1)} {bw_match.group(2).upper()}"
    elif "unlimited bandwidth" in text:
        specs['bandwidth'] = "Unlimited"

    return specs

def scrape_category(cat_url):
    print(f"Scraping Category: {cat_url}")
    products = []
    try:
        res = requests.get(cat_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'})
        if res.status_code != 200: return []
        
        soup = BeautifulSoup(res.text, 'html.parser')
        
        # Select all price tables
        cards = soup.select(".price-table")
        
        for card in cards:
            try:
                # Title
                title_el = card.select_one(".top-head h4")
                if not title_el: continue
                title = title_el.get_text(strip=True)
                
                # Price
                price_el = card.select_one(".price")
                price = price_el.get_text(strip=True) if price_el else "0.00"
                
                # Link
                btn = card.select_one("a.order-button")
                link = BASE_URL + btn['href'] if btn and btn['href'].startswith("/") else (btn['href'] if btn else "")
                
                # Description (Specs)
                desc_el = card.select_one("ul")
                desc_text = desc_el.get_text(" ", strip=True) if desc_el else ""
                
                # Clean Price
                try:
                    clean_price = re.sub(r'[^\d\.]', '', price)
                    price_val = float(clean_price)
                except:
                    price_val = 0.0
                
                # Parse Specs
                specs = parse_specs(desc_text, title)
                
                # Calculate Score
                performance_score = (specs['ram'] * 0.6) + (specs['cpu'] * 0.4)
                if performance_score == 0: continue
                value_score = performance_score / (price_val if price_val > 0 else 1)
                
                products.append({
                    "id": title, # Use title as ID since proper PID is hidden behind redirection
                    "title": title,
                    "price": price,
                    "specs": specs,
                    "description": desc_text[:200],
                    "purchase_url": link,
                    "value_score": value_score,
                    "raw_price": price_val
                })
                print(f"  Found: {title} | {specs['ram']}MB | ${price_val}")
                
            except Exception as e:
                print(f"  Error parsing card: {e}")
                
    except Exception as e:
        print(f"Error accessing {cat_url}: {e}")
        
    return products

def scrape_all():
    print("Starting Spider Scan (Categories)...")
    
    # 1. Get Categories
    res = requests.get(STORE_HOME, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'})
    soup = BeautifulSoup(res.text, 'html.parser')
    
    categories = []
    seen_urls = set()
    
    # Find sidebar or menu links to /store/
    for a in soup.find_all("a", href=True):
        href = a['href']
        if "/store/" in href and "index.php?rp=" in href:
             if href not in seen_urls:
                 # Check if relative or absolute
                 full_url = BASE_URL + href if href.startswith("/") else href
                 if full_url not in seen_urls:
                    categories.append(full_url)
                    seen_urls.add(full_url)
    
    print(f"Found {len(categories)} categories.")
    
    all_products = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(scrape_category, categories))
        for r in results:
            all_products.extend(r)
            
    # Deduplicate by Title + Price (sometimes categories overlap)
    unique_products = {}
    for p in all_products:
        key = f"{p['title']}_{p['raw_price']}"
        if key not in unique_products:
            unique_products[key] = p
            
    final_list = list(unique_products.values())
    
    print(f"Total Unique Products: {len(final_list)}")
    
    # Sort
    os.makedirs("public", exist_ok=True)
    final_list.sort(key=lambda x: x['value_score'], reverse=True)
    
    with open("public/ccs.json", "w", encoding='utf-8') as f:
        json.dump(final_list, f, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    scrape_all()
