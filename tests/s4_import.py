"""Excel/CSV 导入测试。"""

import asyncio
import csv

from .common import TestEnv


def _write_csv(path, rows, encoding):
    with open(path, "w", encoding=encoding, newline="") as f:
        writer = csv.writer(f)
        for r in rows:
            writer.writerow(r)


def _write_xlsx(path, rows):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    wb.save(path)
    wb.close()


async def test_csv_utf8(env: TestEnv):
    p = env.import_service.imports_dir / "utf8.csv"
    _write_csv(p, [["1001", "Alice"], ["1002", "Bob"]], "utf-8")
    data, errors, skipped = env.import_service.parse_rows(p)
    assert len(data) == 2
    assert data[0] == ("1001", "Alice", "")
    assert skipped == 0 and not errors


async def test_csv_gbk(env: TestEnv):
    p = env.import_service.imports_dir / "gbk.csv"
    _write_csv(p, [["2001", "Zidane"], ["2002", "Ronaldo"]], "gbk")
    data, errors, _ = env.import_service.parse_rows(p)
    assert data[1][1] == "Ronaldo"


async def test_numeric_cell_integrity(env: TestEnv):
    p = env.import_service.imports_dir / "num.xlsx"
    _write_xlsx(p, [[123456789, "Numeric"], [2.5, "Half"]])
    data, errors, _ = env.import_service.parse_rows(p)
    assert data[0][0] == "123456789"
    assert data[1][0] == "2.5"


async def test_error_rows(env: TestEnv):
    p = env.import_service.imports_dir / "bad.csv"
    _write_csv(p, [["", "NoId"], ["3001", ""], ["", ""]], "utf-8")
    data, errors, skipped = env.import_service.parse_rows(p)
    assert len(data) == 0
    assert skipped == 1
    assert len(errors) == 2


async def test_upsert_stats(env: TestEnv):
    p = env.import_service.imports_dir / "upsert.csv"
    _write_csv(p, [["4001", "New"], ["4002", "Old"]], "utf-8")
    data, _, _ = env.import_service.parse_rows(p)
    r1 = await env.import_service.import_rows(data, "admin1")
    assert r1["added"] == 2 and r1["updated"] == 0
    r2 = await env.import_service.import_rows([("4001", "Renamed", "")], "admin1")
    assert r2["updated"] == 1
    player = await env.dao.get_player_by_uid("4001")
    assert player["foreign_name"] == "Renamed"


async def test_cleanup_oldest(env: TestEnv):
    for p in env.import_service.list_files():
        p.unlink()
    env.cfg["import_max_files"] = 2
    for i in range(3):
        _write_csv(env.import_service.imports_dir / f"f{i}.csv", [["1", "x"]], "utf-8")
    removed = env.import_service.cleanup_oldest()
    assert removed == 1
    assert len(env.import_service.list_files()) == 2


async def test_xlsx_parse(env: TestEnv):
    p = env.import_service.imports_dir / "data.xlsx"
    _write_xlsx(p, [["5001", "Xavier"], ["5002", "Yann"]])
    data, errors, _ = env.import_service.parse_rows(p)
    assert len(data) == 2
    assert data[1] == ("5002", "Yann", "")


def run_all():
    async def main():
        env = TestEnv()
        await env.setup()
        try:
            await test_csv_utf8(env)
            print("  PASS test_csv_utf8")
            await test_csv_gbk(env)
            print("  PASS test_csv_gbk")
            await test_numeric_cell_integrity(env)
            print("  PASS test_numeric_cell_integrity")
            await test_error_rows(env)
            print("  PASS test_error_rows")
            await test_upsert_stats(env)
            print("  PASS test_upsert_stats")
            await test_cleanup_oldest(env)
            print("  PASS test_cleanup_oldest")
            await test_xlsx_parse(env)
            print("  PASS test_xlsx_parse")
        finally:
            await env.teardown()

    asyncio.run(main())
    return 7
