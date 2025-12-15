import requests
from bs4 import BeautifulSoup
import re
import json
import concurrent.futures
import time
import os

# CONFIG
BASE_URL = "https://cloud.colocrossing.com/cart.php?a=confproduct&i={0}"
MAIN_URL = "https://cloud.colocrossing.com/cart.php"

def parse_specs(text, title):
    specs = {
        "ram": 0,    # MB
        "disk": "N/A",
        "cpu": 0,
        "bandwidth": "N/A",
        "location": "Buffalo" # Default
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

def check_pid(pid):
    try:
        url = BASE_URL.format(pid)
        
        # KEY FIX: Use Session and Prime Cookies for EVERY Check
        # (Overhead acceptable for accuracy)
        with requests.Session() as s:
            s.headers.update({
                 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            })
            # 1. Prime Cookies by visiting main cart
            s.get(MAIN_URL, timeout=10)
            
            # 2. Visit Product URL
            res = s.get(url, timeout=12)
        
        # KEY FIX: Check for Redirects
        # If PID is invalid, WHMCS redirects to /store/... or /cart.php
        if "confproduct" not in res.url:
            # print(f"PID {pid} redirected to {res.url} (Invalid)")
            return None
        
        if res.status_code != 200: return None
        
        soup = BeautifulSoup(res.text, 'html.parser')
        text_dump = soup.get_text(" ", strip=True)
            
        # Title Extraction
        title = ""
        
        # 0. H4 Priority (ColoCrossing Custom Theme)
        h4 = soup.select_one("h4")
        if h4: 
            t = h4.get_text(strip=True)
            # Filter generic H4s if any
            if len(t) > 3 and "Overview" not in t and "Categorie" not in t:
                title = t
        
        # Fallbacks
        if not title:
            hostim_title = soup.select_one(".product-title")
            if hostim_title: title = hostim_title.get_text(strip=True)

        if not title:
            h1 = soup.select_one("h1")
            if h1:
                t = h1.get_text(strip=True).replace("Configure", "").strip()
                if "Shopping Cart" not in t: title = t

        if not title or title == "Shopping Cart" or "Configure" in title: 
             # Last diff check
             if "1GB RAM" in res.text and not title:
                 # Debug fallback
                 pass
             else:
                 return None

        # Price Extraction
        price = "0.00"
        
        # Look for price in summary
        price_el = soup.select_one("#order-summary .price") or soup.select_one(".amt") or soup.select_one(".product-pricing") or soup.select_one(".total-due-today .amt")
        
        # Also check dropdowns
        billing_select = soup.select_one("select[name='billingcycle']")
        if billing_select:
             # Logic to find price in options
             pass

        if price_el:
            price = price_el.get_text(strip=True)
            
        # Specs logic
        desc_text = soup.select_one(".product-info") or soup.select_one(".description")
        desc_str = desc_text.get_text(" ", strip=True) if desc_text else text_dump
        
        specs = parse_specs(desc_str, title)
            
        # Normalize Price
        try:
            clean_price = re.sub(r'[^\d\.]', '', price)
            price_val = float(clean_price)
        except:
            price_val = 999.0 
        
        if price_val < 0.1: pass

        performance_score = (specs['ram'] * 0.6) + (specs['cpu'] * 0.4)
        if performance_score == 0: return None
        
        value_score = performance_score / (price_val if price_val > 0 else 1)
        
        print(f"FOUND PID {pid}: {title} | {specs['location']} | {specs['ram']}MB | ${price_val}")
        
        return {
            "id": pid,
            "title": title,
            "price": price,
            "specs": specs,
            "description": desc_str[:200],
            "purchase_url": url,
            "value_score": value_score,
            "raw_price": price_val
        }
            
    except Exception as e:
        # print(f"Error {pid}: {e}")
        pass
    return None

def scrape_all():
    print("Starting concurrent scan of ColoCrossing PIDs 0-1000...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor: # Reduced workers to be nice
        results = list(executor.map(check_pid, range(1000))) 
    
    clean_results = [r for r in results if r]
    print(f"Total found: {len(clean_results)}")
    
    # Sort by value score descending
    os.makedirs("public", exist_ok=True)
    clean_results.sort(key=lambda x: x['value_score'], reverse=True)
    
    with open("public/ccs.json", "w", encoding='utf-8') as f:
        json.dump(clean_results, f, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    scrape_all()
