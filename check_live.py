import json
import sys
import time
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "https://gridwise-1.onrender.com").rstrip("/")
CASES_FILE = "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"


def post(path, payload, timeout=90):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(BASE + path, body, {"content-type": "application/json"})
    start = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp), time.perf_counter() - start


def get(path, timeout=90):
    start = time.perf_counter()
    with urllib.request.urlopen(BASE + path, timeout=timeout) as resp:
        return json.load(resp), time.perf_counter() - start


print(f"Testing {BASE}\n")

print("1. GET /health")
try:
    data, dt = get("/health")
    print(f"   {data}   {dt:.2f}s")
    if dt > 20:
        print("   (slow first call = the service was asleep; that is expected on a free tier)")
except Exception as exc:
    print(f"   FAILED: {exc}")
    sys.exit(1)

cases = json.load(open(CASES_FILE))["cases"]

print("\n2. POST /optimize-energy, all 10 public cases")
slowest = 0.0
passed = 0
for case in cases:
    try:
        result, dt = post("/optimize-energy", case["input"])
        slowest = max(slowest, dt)
        ref = case["expected_output"]["total_cost_bdt"]
        good = abs(result["total_cost_bdt"] - ref) < 0.01
        hours_ok = len(result["hourly_plan"]) == 24
        notes_ok = len(result["directive_interpretation"]) == len(case["input"]["operator_notes"])
        passed += good and hours_ok and notes_ok
        flag = "OK  " if (good and hours_ok and notes_ok) else "BAD "
        print(f"   {case['id']:10} {flag} cost {result['total_cost_bdt']:9.0f}  ref {ref:9.0f}  {dt:5.2f}s")
    except urllib.error.HTTPError as exc:
        print(f"   {case['id']:10} HTTP {exc.code}  {exc.read()[:120]}")
    except Exception as exc:
        print(f"   {case['id']:10} FAILED  {exc}")

print(f"\n   {passed}/10 correct   slowest {slowest:.2f}s", end="")
print("   (p95 budget is 5s)" if slowest < 5 else "   <-- over the 5s p95 budget")

print("\n3. Is the LLM actually running on the server?")
try:
    result, _ = post("/optimize-energy", cases[0]["input"])
    text = result["directive_interpretation"][0]["explanation"]
    print(f"   explanation: {text}")
    if "0.25" in text or "[12, 13]" in text:
        print("   >>> FALLBACK wording. GEMINI_API_KEY is probably not set on the server.")
    else:
        print("   >>> LLM wording. The key is working.")
except Exception as exc:
    print(f"   FAILED: {exc}")

print("\n4. Malformed request should be 400")
try:
    post("/optimize-energy", {"scenario_id": "X"})
    print("   returned 200 — unexpected")
except urllib.error.HTTPError as exc:
    print(f"   HTTP {exc.code}" + ("  (correct)" if exc.code == 400 else "  (spec says 400)"))
except Exception as exc:
    print(f"   FAILED: {exc}")