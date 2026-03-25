"""
TITAN FORGE — Symbol Finder
Run this locally to find the exact NAS100 ticker name on your OANDA account.
Usage: python find_symbols.py
"""
import asyncio
import os
import sys

METAAPI_TOKEN = "eyJhbGciOiJSUzUxMiIsInR5cCI6IkpXVCJ9.eyJfaWQiOiJhN2JmYWRRaU9pSmhOMkV6Tnpna00ySXdaakppTWpNMU1XRmhNREU1WW1GaE9UZGxZamMxWWpJd1lUWTJNVEZoWVRJMU9EWTBOall3WkRObE9HRXhPV1kxWVRnMk56UTBNakkxWXpRMVlqSTVOR1ZoWWpJek1qQXdNekUwWXpZM05UQTBPREpoTURreVkyVmlNREV3TkRReVlqSTRaVEUwTXpjMFl6UTJZVFJrWlRVek5tSmtNREE1TWpsbVpqZ3hPRFF6TURFNU5EY3haak5pWVRrd05UZ3dOak5pWWpaaldqSTJaVE5tWWpjeVlqWTFZamhpT0dZMlptRmpZMlU0Wm1ZME1UZzFPVGd5TWpVMVlURXpNVEl4WVdaak1Ua3dNak0zT0RnM01EZzFZalJoTmpVd1pXVXpNek15TmpJMk9USXhZVFkwTkRVMk9UTTVPVFkzWWprMk5UazNaVGd5WmpGalltSTJZekkwWmpFM01qUmpPVE5sWkdOak1XSTVaV1JpTVRCbU1EazBaV0UzTkRsaE4yVmtaak5qWmpNd1lqSTBNV0l6TlRjM016UXhNMlptT0dRMU9HUm1Nek5pTURWaFkyUTFPVE1pTENKaGJHeHZkeTFsZUhBaU9pSXlNREl3TWkwd09TMHhOeUF4TlRveU9UbzFPQzQxT0Nrc01Fb2lMQ0p1WVcxbElqb2lNVFl3TURFd09UWTJJaXdpWkc5dFlXbHVJam9pYW05cVpXeGtRR0Z1WVM1amIyMGlMQ0p2WVhBaU9pSmpiMjV1WldOMElpd2laWGgwWlc1emFXOXVJam9pY21Wc1lYa2lMQ0pwWkNJNkltRTNZbVpoWkRraUxDSmtZVzFwYkY5cFpDSTZJak0zTURKak1qRXlNQ0lzSW1WNGNHbHlZWFJwYjI1ZlpHbHpjeUk2SWpJd01qWXRNRE10TURFaWZRLk9hcjBrUU1UcklLT0tMaFRJSGxyWGZFbFE2eEhia1NHY01NVkw5ZkFONXNfc3BVUVZ2bnV0LUZMN213UlE2SzBMUlA2Wm5uZ09VSXZTSkZLT0RHbEFaT2dha0F2Rl9HekpCVGhwWW14VGpRX01DUGNmSnkydTVuTlpUb2M5T3J6QkpsdmtFTzlHS1RzRXVSLUNaaFV3R3k2YUFaZjVWZjFuVmdLTjVRMUgzSk8wZU4zSkpaUWFMQWJaRzhlQVFKRjJkeXVWcXZNNkc4UHUxbEpMem5aTXc4QjdJZklMY1dMWVZJdXBvYjJ0NHpGcDRBOHMxNEdaN1VOR3Z3NTJLd1l1c21KeTN6ZzVhSHhyY01IcTRySTJqT2RvNHpLOW5Hb21pRkVIVVlGVGNJMXR0MXU2cVQxS0JsUWNRYWdNRlpXWkZwY3JPNA=="
ACCOUNT_ID = "8a2f19ff-aab1-41e5-b20a-b54faf1632a4"

async def find_nas100_symbol():
    try:
        import aiohttp
    except ImportError:
        print("Installing aiohttp...")
        import subprocess
        subprocess.run([sys.executable, "-m", "pip", "install", "aiohttp", "-q"])
        import aiohttp

    url = f"https://mt-client-api-v1.agiliumtrade.agiliumtrade.ai/users/current/accounts/{ACCOUNT_ID}/symbols"
    
    print(f"Querying MetaAPI for symbols on account {ACCOUNT_ID}...")
    
    async with aiohttp.ClientSession() as session:
        async with session.get(
            url,
            headers={"auth-token": METAAPI_TOKEN},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            print(f"Status: {resp.status}")
            if resp.status == 200:
                symbols = await resp.json()
                if symbols and isinstance(symbols[0], dict):
                    symbols = [s.get("symbol", "") for s in symbols]
                
                print(f"\nTotal symbols: {len(symbols)}")
                
                # Find NAS100 candidates
                keywords = ["nas", "ustec", "us100", "ndx", "nasdaq", "nq", "100"]
                candidates = [s for s in symbols if any(k in s.lower() for k in keywords)]
                
                print(f"\n🎯 NAS100 CANDIDATES ({len(candidates)} found):")
                for s in candidates:
                    print(f"  → {s}")
                
                if not candidates:
                    print("\nAll symbols:")
                    for s in sorted(symbols):
                        print(f"  {s}")
            else:
                body = await resp.text()
                print(f"Error: {body}")

asyncio.run(find_nas100_symbol())
