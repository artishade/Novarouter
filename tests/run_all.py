"""Run the complete v2 test battery."""
import subprocess

PY = "/root/.code_runner/py/bin/python"
CWD = "/root/novarouter"

r = subprocess.run([PY, "-m", "pytest", "tests/test_basic.py", "-q"],
                   capture_output=True, text=True, cwd=CWD, timeout=120)
last = [l for l in r.stdout.strip().split("\n") if "passed" in l or "failed" in l]
print("PYTEST     :", last[-1] if last else r.stdout[-200:])

r = subprocess.run([PY, "tests/verify_v2.py"], capture_output=True, text=True, cwd=CWD, timeout=120)
print("V2 UNITS   :", "PASS (12/12)" if "ALL 12 VERIFICATION TESTS PASSED" in r.stdout
      else "FAIL\n" + r.stdout[-500:])

r = subprocess.run([PY, "tests/verify_endpoints.py"], capture_output=True, text=True, cwd=CWD, timeout=120)
print("ENDPOINTS  :", "PASS (9/9)" if "9/9 endpoint checks passed" in r.stdout
      else "FAIL\n" + r.stdout[-500:])

r = subprocess.run([PY, "tests/verify_e2e.py"], capture_output=True, text=True, cwd=CWD, timeout=180)
print("E2E        :", "PASS (13/13)" if "ALL 13 END-TO-END TESTS PASSED" in r.stdout
      else "FAIL\n" + r.stdout[-800:])

print()
print("BATTERY COMPLETE")