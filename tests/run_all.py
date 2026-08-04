"""测试运行入口：python -m tests.run_all（插件根目录）或 python tests/run_all.py"""

import importlib
import os
import sys

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGINS_DIR = os.path.dirname(PLUGIN_ROOT)
if PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)
if PLUGINS_DIR not in sys.path:
    sys.path.insert(0, PLUGINS_DIR)


def main():
    modules = [
        "s1_formula",
        "s2_core_flow",
        "s3_concurrency_security",
        "s4_import",
        "s5_races",
        "s6_backup",
        "s7_config",
        "s8_migration",
        "s9_handlers",
        "s10_main",
        "s11_perf",
    ]
    total = 0
    failed = []
    for name in modules:
        try:
            mod = importlib.import_module(f"tests.{name}")
            n = mod.run_all()
            total += n
            print(f"[OK] {name} ({n} tests)")
        except AssertionError as e:
            failed.append(name)
            print(f"[FAIL] {name}: {e}")
        except Exception as e:
            failed.append(name)
            print(f"[ERROR] {name}: {e!r}")
    print(f"\nTotal: {total} tests passed")
    if failed:
        print(f"Failed modules: {failed}")
        sys.exit(1)
    print("All tests passed.")


if __name__ == "__main__":
    main()
