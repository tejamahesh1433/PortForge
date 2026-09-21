import asyncio
import requests
import json
from playwright.async_api import async_playwright

API_URL = "http://127.0.0.1:58000"

def get_activity(host_id, port):
    r = requests.get(f"{API_URL}/api/activity?host_id={host_id}&port={port}&limit=20")
    return r.json()

def check_central_reservation(host_id, port):
    r = requests.get(f"{API_URL}/api/reservations")
    items = r.json().get("items", [])
    for item in items:
        if item["host_id"] == host_id and item["port"] == port and item["project"] == "portforge-phase7c1-validation":
            return item
    return None

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={'width': 1280, 'height': 800})
        
        base_url = "http://localhost:3000"
        out_dir = r"C:\Users\ntmke\.gemini\antigravity-ide\brain\55009cde-2062-43b6-a620-4c2b1c219014\scratch"
        host_id = "f90db087-f7b4-4647-958c-e8e13051ddc3"
        
        print("Authenticating...")
        await page.goto(base_url, wait_until="domcontentloaded")
        await page.evaluate("localStorage.setItem('portforgeAdminToken', 'test-admin-bootstrap-token')")
        
        # 1. Recommendations
        await page.goto(base_url + "/recommendations", wait_until="domcontentloaded")
        await page.wait_for_timeout(1000)
        
        # Select host NTMKEYA
        await page.locator('button[role="combobox"]').first.click()
        await page.wait_for_timeout(500)
        await page.locator('text=NTMKEYA').click()
        await page.wait_for_timeout(500)
        
        
        await page.locator('button[type="submit"]').click()
        await page.wait_for_timeout(2000)
        
        await page.screenshot(path=f"{out_dir}\\1_recommendation_result.png")
        print("Captured: Recommendations result")
        
        # Extract the recommended port from the result card
        port_text = await page.locator('.text-6xl').inner_text()
        rec_port = int(port_text.strip())
        print(f"Recommended port: {rec_port}")
        
        # Pre-test activity
        activity_before = get_activity(host_id, rec_port)
        print(f"Activity before: {activity_before['total']} events")
        
        # Create Reservation
        await page.locator('text=Reserve this port').click()
        await page.wait_for_timeout(1000)
        await page.locator('input[id="project"]').fill('portforge-phase7c1-validation')
        await page.locator('text=Confirm Reservation').click()
        await page.wait_for_timeout(2000)
        
        await page.screenshot(path=f"{out_dir}\\2_reservation_active.png")
        print("Captured: Reservation active")
        
        # Verify dashboard state
        await page.goto(base_url + "/reservations", wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        res_count = await page.locator('text=portforge-phase7c1-validation').count()
        print(f"Dashboard reservation count: {res_count}")
        
        # Verify Central
        central_res = check_central_reservation(host_id, rec_port)
        if central_res:
            print(f"Central verified reservation ID: {central_res['id']}")
        else:
            print("Central verification FAILED")
            
        # Verify RESERVATION_CREATED
        activity_after_create = get_activity(host_id, rec_port)
        created_events = [e for e in activity_after_create['events'] if e['event_type'] == 'RESERVATION_CREATED']
        print(f"RESERVATION_CREATED count: {len(created_events)}")
        if created_events:
            print(f"Event references reservation ID: {created_events[0]['reservation_id']}")
            
        # Capture Activity
        await page.goto(base_url + f"/activity?host_id={host_id}&port={rec_port}")
        await page.wait_for_timeout(2000)
        await page.screenshot(path=f"{out_dir}\\3_activity_showing_created.png")
        print("Captured: Activity showing RESERVATION_CREATED")
        
        # Refresh and unchanged check
        await page.reload()
        await page.wait_for_timeout(2000)
        activity_unchanged = get_activity(host_id, rec_port)
        created_events_unchanged = [e for e in activity_unchanged['events'] if e['event_type'] == 'RESERVATION_CREATED']
        print(f"RESERVATION_CREATED count after refresh: {len(created_events_unchanged)}")
        
        # Release
        await page.goto(base_url + "/reservations", wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        
        # Click release for our project
        row = page.locator('tr:has-text("portforge-phase7c1-validation")').first
        await row.locator('button:has-text("Release")').click()
        await page.wait_for_timeout(500)
        page.on("dialog", lambda dialog: dialog.accept())
        await page.wait_for_timeout(2000)
        
        # Verify Central cleanup
        central_res_after = check_central_reservation(host_id, rec_port)
        print(f"Central active reservation remaining: {central_res_after is not None}")
        
        # Verify RESERVATION_RELEASED
        activity_after_release = get_activity(host_id, rec_port)
        released_events = [e for e in activity_after_release['events'] if e['event_type'] == 'RESERVATION_RELEASED']
        print(f"RESERVATION_RELEASED count: {len(released_events)}")
        
        await page.screenshot(path=f"{out_dir}\\5_reservations_after_cleanup.png")
        print("Captured: Reservations after cleanup")
        
        # Capture Activity again
        await page.goto(base_url + f"/activity?host_id={host_id}&port={rec_port}")
        await page.wait_for_timeout(2000)
        await page.screenshot(path=f"{out_dir}\\4_activity_showing_released.png")
        print("Captured: Activity showing RESERVATION_RELEASED")
        
        # Overview recent activity
        await page.goto(base_url + "/", wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        await page.screenshot(path=f"{out_dir}\\6_overview_recent_activity.png")
        print("Captured: Overview Recent Activity")
        
        await browser.close()
        
        # Health check
        health = requests.get(f"{API_URL}/api/health").json()
        hosts = requests.get(f"{API_URL}/api/hosts").json()
        print(f"Central Status: {health['status']}, DB: {health['database']}")
        print(f"Hosts enrolled: {hosts['total']}")
        
asyncio.run(main())
