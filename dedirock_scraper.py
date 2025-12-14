import requests
from bs4 import BeautifulSoup
import json
import os
import concurrent.futures
import re

BASE_URL = "https://billing.dedirock.com/cart.php?a=add&pid={}"
PRODUCTS = []

def parse_specs(text, title):
    specs = {
        "cpu": 0,
        "ram": 0, # in MB
        "disk": "N/A",
        "bandwidth": "N/A",
        "location": "US"
    }
    
    text = text.lower() + " " + title.lower()
    
    # RAM
    ram_match = re.search(r'(\d+(?:\.\d+)?)\s*(mb|gb)\s*ram', text)
    if ram_match:
        val = float(ram_match.group(1)) # float support
        unit = ram_match.group(2)
        if unit == 'gb':
            specs['ram'] = int(val * 1024) # Store as MB (int)

        else:
            specs['ram'] = val
    else:
        # Heuristic search for standalone numbers near "MB" or "GB"
        pass

    # CPU
    cpu_match = re.search(r'(\d+)\s*(vcore|core|cpu)', text)
    if cpu_match:
        specs['cpu'] = int(cpu_match.group(1))
        
    # Location
    if "ny" in text or "new york" in text: specs['location'] = "New York"
    elif "la" in text or "los angeles" in text: specs["location"] = "Los Angeles"
    elif "hk" in text or "hong kong" in text: specs["location"] = "Hong Kong"
    elif "jp" in text or "tokyo" in text: specs["location"] = "Tokyo"
    
    # Disk
    # Support "2x 2TB NVMe" or "480GB SSD"
    # Avoid "256GB RAM" by enforcing disk keywords
    disk_match = re.search(r'(?:(\d+)\s*[xX]\s*)?(\d+)\s*(TB|GB)\s*(NVMe|SSD|HDD|Storage|Disk)', text, re.IGNORECASE)
    if disk_match:
        multiplier = int(disk_match.group(1)) if disk_match.group(1) else 1
        size_val = int(disk_match.group(2))
        unit = disk_match.group(3).upper()
        dtype = disk_match.group(4).upper()
        
        # Calculate Total Storage for display (e.g. 4TB NVMe) or keep partial?
        # User wants to see "2x 2TB" probably, or the total.
        # Let's show formatted total if multiplier > 1
        
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
        
        # Use Session for cookies
        with requests.Session() as s:
            s.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            })
            res = s.get(url, timeout=12)
        
        # Check success
        if res.status_code != 200: return None
        
        soup = BeautifulSoup(res.text, 'html.parser')
        text_dump = soup.get_text(" ", strip=True)
            
        # Title Extraction Strategy
        title = ""
        
        # 0. Guaranteed selector from debug (Hostim Theme)
        hostim_title = soup.select_one(".product-title")
        if hostim_title:
            title = hostim_title.get_text(strip=True)

        # 1. Try Order Summary Product Title (Common in Six/Twenty-One)
        if not title:
            summary_title = soup.select_one("#order-summary .product-name") or soup.select_one(".summary-product-name")
            if summary_title:
                title = summary_title.get_text(strip=True)
            
        # 2. Try Config Product Header
        if not title:
             config_header = soup.select_one(".product-info h3") or soup.select_one("header span.product-name")
             if config_header:
                 title = config_header.get_text(strip=True)
        
        # 3. Fallback to H1 but filter garbage
        if not title:
            h1 = soup.select_one("h1")
            if h1:
                t = h1.get_text(strip=True).replace("Configure", "").strip()
                if "Shopping Cart" not in t and "Review" not in t and "Login" not in t:
                    title = t

        # Deep Search for fallback titles (like "Product - Group")
        if not title and "Shopping Cart" in (soup.select_one("h1") and soup.select_one("h1").get_text() or ""):
            # Look for item in table?
            # Usually strict cart structure
            pass

        if not title: # Allow "Shopping Cart" title IF we found a product-title element? No, if we found hostim_title we have a title.
             return None
        
        if title == "Shopping Cart": return None

        # Extract Price logic
        # Usually from summary or total
        price = "0.00"
        
        # 1. Billing Cycle Dropdown (Prioritize Annually)
        billing_select = soup.select_one("select[name='billingcycle']")
        price_text = ""
        billing_cycle_used = "Monthly" # Default tracking

        if billing_select:
            options = billing_select.find_all("option")
            annual_option = None
            monthly_option = None
            
            for opt in options:
                txt = opt.get_text(strip=True).lower()
                if "annually" in txt or "year" in txt:
                    annual_option = opt
                    break # Found gold
                if "monthly" in txt and not monthly_option:
                    monthly_option = opt # Fallback
            
            if annual_option:
                price_text = annual_option.get_text(strip=True)
                billing_cycle_used = "Annually"
            elif monthly_option:
                price_text = monthly_option.get_text(strip=True)
                billing_cycle_used = "Monthly"
            elif options:
                price_text = options[0].get_text(strip=True) # Last resort
        
        if price_text:
            price = price_text
        else:
            # Fallback to summary if no dropdown
            price_el = soup.select_one("#order-summary .price") or soup.select_one(".amt") or soup.select_one(".product-pricing") or soup.select_one(".total-due-today .amt")
            if price_el:
                price = price_el.get_text(strip=True)
            
        # Specs logic
        desc_text = soup.select_one(".product-info") or soup.select_one(".description")
        desc_str = desc_text.get_text(" ", strip=True) if desc_text else text_dump
        
        specs = parse_specs(desc_str, title)
            
        # Normalize Price
        try:
            # handle "$6.45 USD" or "R$ 30.00"
            clean_price = re.sub(r'[^\d\.]', '', price)
            price_val = float(clean_price)
        except:
            price_val = 999.0 
        
        if price_val < 0.1: # Skip essentially free/setup fee only items if confusing
             # Check if it's "Free" text
             if "Free" not in price:
                  # Maybe price failed to parse?
                  pass

        performance_score = (specs['ram'] * 0.6) + (specs['cpu'] * 0.4)
        if performance_score == 0: return None # Skip empty/config-only items
        
        value_score = performance_score / (price_val if price_val > 0 else 1)
        
        print(f"FOUND PID {pid}: {title} | {specs['location']} | {specs['ram']}MB | ${price_val} -> Score: {value_score:.2f}")
        
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
        print(f"Error {pid}: {e}")
        # pass
    return None

def scrape_all():
    print("Starting concurrent scan of PIDs 0-1000...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        results = list(executor.map(check_pid, range(1000))) 
    
    clean_results = [r for r in results if r]
    print(f"Total found: {len(clean_results)}")
    
    # Sort by value score descending
    os.makedirs("public", exist_ok=True)
    clean_results.sort(key=lambda x: x['value_score'], reverse=True)
    
    with open("public/dedirock.json", "w", encoding='utf-8') as f:
        json.dump(clean_results, f, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    scrape_all()
