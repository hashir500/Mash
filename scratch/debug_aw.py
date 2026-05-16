import httpx
try:
    r = httpx.get("http://127.0.0.1:5600/api/0/buckets", timeout=5.0, follow_redirects=True)
    print(f"Status: {r.status_code}")
    print(f"URL: {r.url}")
    print(f"Buckets: {list(r.json().keys())[:5]}")
except Exception as e:
    print(f"Error: {e}")
