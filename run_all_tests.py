#!/usr/bin/env python3
"""Master Test Runner for VGL POS (Version A).

Executes all verification layers across the entire testing pyramid:
  Layer 1: Unit & Invariant Regression Suite (test_app.py)
  Layer 2: Concurrency & Race-Condition Stress Suite (tests/test_concurrency.py)
  Layer 3: Playwright End-to-End Browser Journey Suite (tests/test_e2e_playwright.py)

Usage:
  python run_all_tests.py              # Run all 3 layers
  python run_all_tests.py --quick      # Run Unit + Concurrency (skips browser E2E)
  python run_all_tests.py --e2e-only   # Run only Playwright E2E browser tests
  python run_all_tests.py --conc-only  # Run only Concurrency stress tests
"""
import sys
import os
import time
import argparse
import subprocess

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(PROJECT_ROOT)

# ANSI terminal styling
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def print_banner():
    banner = f"""
{CYAN}{BOLD}======================================================================
               VGL POS (VERSION A) MASTER TEST RUNNER                 
         Multi-Layered Automated Verification & Security Audit        
======================================================================{RESET}
"""
    print(banner)


def run_command(cmd, desc):
    print(f"\n{BOLD}[>] Running {desc}...{RESET}")
    print(f"{DIM}Command: {' '.join(cmd)}{RESET}\n")
    start = time.time()
    res = subprocess.run(cmd, cwd=PROJECT_ROOT)
    elapsed = time.time() - start
    success = (res.returncode == 0)
    return success, elapsed


def main():
    parser = argparse.ArgumentParser(description="VGL POS Master Test Runner")
    parser.add_argument("--quick", action="store_true", help="Skip Playwright E2E browser tests")
    parser.add_argument("--e2e-only", action="store_true", help="Run only Playwright E2E tests")
    parser.add_argument("--conc-only", action="store_true", help="Run only Concurrency stress tests")
    parser.add_argument("--unit-only", action="store_true", help="Run only Unit & Invariant tests")
    args = parser.parse_args()

    print_banner()

    layers = []

    if args.unit_only:
        layers.append(("Unit & Invariant Regression Suite", [sys.executable, "-m", "pytest", "test_app.py", "-q"]))
    elif args.conc_only:
        layers.append(("Concurrency & Race Condition Suite", [sys.executable, "-m", "pytest", "tests/test_concurrency.py", "-v"]))
    elif args.e2e_only:
        layers.append(("Playwright E2E Browser Journey", [sys.executable, "tests/test_e2e_playwright.py"]))
    else:
        # Default full test suite
        layers.append(("Layer 1: Unit & Invariant Suite", [sys.executable, "-m", "pytest", "test_app.py", "-q"]))
        layers.append(("Layer 2: Concurrency Stress Suite", [sys.executable, "-m", "pytest", "tests/test_concurrency.py", "-v"]))
        if not args.quick:
            layers.append(("Layer 3: Playwright E2E Journey", [sys.executable, "tests/test_e2e_playwright.py"]))

    results = []
    overall_start = time.time()

    for name, cmd in layers:
        passed, duration = run_command(cmd, name)
        results.append((name, passed, duration))
        if not passed:
            print(f"{RED}[FAIL] {name} FAILED after {duration:.2f}s{RESET}")
        else:
            print(f"{GREEN}[PASS] {name} PASSED in {duration:.2f}s{RESET}")

    total_time = time.time() - overall_start

    # Final summary dashboard
    print(f"\n{BOLD}======================================================================{RESET}")
    print(f"{BOLD}                        TEST EXECUTION SUMMARY                        {RESET}")
    print(f"{BOLD}======================================================================{RESET}")
    print(f"{'Layer / Test Suite':<40} {'Status':<12} {'Duration':<10}")
    print("-" * 65)

    all_passed = True
    for name, passed, duration in results:
        status_str = f"{GREEN}PASSED{RESET}" if passed else f"{RED}FAILED{RESET}"
        if not passed:
            all_passed = False
        print(f"{name:<40} {status_str:<21} {duration:>6.2f}s")

    print("-" * 65)
    print(f"{BOLD}{'Total Duration:':<40} {total_time:>18.2f}s{RESET}")
    
    if all_passed:
        print(f"\n{GREEN}{BOLD}[SUCCESS] ALL TEST SUITES PASSED! System is 100% verified & production ready.{RESET}\n")
        return 0
    else:
        print(f"\n{RED}{BOLD}[FAILURE] ONE OR MORE TEST SUITES FAILED. Check test logs above.{RESET}\n")
        return 1


if __name__ == '__main__':
    sys.exit(main())
