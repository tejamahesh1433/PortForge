import asyncio
from playwright.async_api import async_playwright
import os
import requests

async def main():
    base_url = "http://localhost:3000"
    host_id = "f90db087-f7b4-4647-958c-e8e13051ddc3"
    port = 10000
    
    out_dir = r"C:\Users\ntmke\.gemini\antigravity-ide\brain\55009cde-2062-43b6-a620-4c2b1c219014\scratch"
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 1280, 'height': 800})
        page = await context.new_page()
        
        print("Authenticating...")
        await page.goto(base_url, wait_until="domcontentloaded")
        await page.evaluate("localStorage.setItem('portforgeAdminToken', 'test-admin-bootstrap-token')")
        
        print("Capturing Active Reservation...")
        await page.goto(base_url + "/reservations", wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        await page.screenshot(path=os.path.join(out_dir, "2_reservation_active.png"), full_page=True)
        
        print("Capturing Activity Created...")
        await page.goto(base_url + f"/activity?host_id={host_id}&port={port}", wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        await page.screenshot(path=os.path.join(out_dir, "3_activity_created.png"), full_page=True)
        
        print("Releasing via API...")
        r = requests.get('http://127.0.0.1:58000/api/reservations?limit=100')
        for res in r.json().get('items', []):
            if res['project'] == 'portforge-phase7c1-validation':
                requests.delete(f"http://127.0.0.1:58000/api/reservations/dashboard/{host_id}/{res['id']}", headers={'Authorization': 'Bearer test-admin-bootstrap-token'})
        
        print("Capturing Activity Released...")
        await page.goto(base_url + f"/activity?host_id={host_id}&port={port}", wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        await page.screenshot(path=os.path.join(out_dir, "4_activity_released.png"), full_page=True)
        
        print("Capturing Reservations Cleanup...")
        await page.goto(base_url + "/reservations", wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        await page.screenshot(path=os.path.join(out_dir, "5_reservations_cleanup.png"), full_page=True)
        
        print("Capturing Overview...")
        await page.goto(base_url + "/", wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        await page.screenshot(path=os.path.join(out_dir, "6_overview_recent.png"), full_page=True)

        await browser.close()
        print("Done!")

if __name__ == "__main__":
    asyncio.run(main())
