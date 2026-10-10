"""
Safe end-to-end smoke tests for the HROne Attendance API.

Usage:
  1. Start the API in another terminal:
       uvicorn app.main:app --port 8000 --reload
  2. From the project root, with the project's .venv active:
       python test_api.py

The script creates one uniquely named test employee and one attendance record.
It removes ONLY those test records from MongoDB at the end. It never clears or
reseeds the existing collections. The MongoDB URI is read from the project's
.env; do not print or commit that file.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB = os.getenv("MONGO_DB", "attendance_db")

if not MONGO_URI:
    print("ERROR: MONGO_URI was not found. Check your local .env file.")
    sys.exit(2)

# Unique code: EMP + 6 digits. This prevents collisions with normal sample data.
TEST_SUFFIX = str(int(time.time() * 1000) % 1_000_000).zfill(6)
EMP_CODE = f"EMP{TEST_SUFFIX}"
EMAIL = f"api-smoke-{TEST_SUFFIX}@example.com"
TODAY_IST = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).date()
TODAY = TODAY_IST.isoformat()


class TestFailure(Exception):
    pass


def request(method: str, path: str, payload: dict[str, Any] | None = None):
    url = f"{BASE_URL}{path}"
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            body = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            body = raw
        return exc.code, body
    except urllib.error.URLError as exc:
        raise TestFailure(
            f"Cannot reach {BASE_URL}. Is uvicorn running? Details: {exc}"
        ) from exc


passed = 0
failed = 0


def check(name: str, method: str, path: str, expected_status: int,
          payload: dict[str, Any] | None = None, validator=None):
    global passed, failed
    try:
        status, body = request(method, path, payload)
        if status != expected_status:
            raise TestFailure(
                f"expected HTTP {expected_status}, got HTTP {status}; response={body}"
            )
        if validator:
            validator(body)
        passed += 1
        print(f"PASS  {name} (HTTP {status})")
        return body
    except Exception as exc:
        failed += 1
        print(f"FAIL  {name}: {exc}")
        return None


def has_items(body):
    assert isinstance(body, dict) and isinstance(body.get("items"), list), (
        "expected an object with an items array"
    )


def check_epoch_ms(value):
    assert isinstance(value, int) and value > 0, f"expected positive epoch ms, got {value!r}"


def main():
    mongo = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    db = mongo[MONGO_DB]
    # Check database reachability before starting any writes.
    try:
        mongo.admin.command("ping")
    except Exception as exc:
        print(f"ERROR: Cannot connect to MongoDB: {exc}")
        mongo.close()
        return 2

    created_employee = False
    try:
        print(f"\nAPI: {BASE_URL}")
        print(f"Temporary employee code: {EMP_CODE}")
        print("Existing collections will NOT be cleared.\n")

        check("Health endpoint", "GET", "/health", 200,
              validator=lambda b: (_ for _ in ()).throw(AssertionError(b))
              if not isinstance(b, dict) or b.get("status") != "ok" else None)

        check("OpenAPI schema", "GET", "/openapi.json", 200,
              validator=lambda b: (_ for _ in ()).throw(AssertionError("missing paths"))
              if not isinstance(b, dict) or "/employees" not in b.get("paths", {}) else None)

        check("Employee list and pagination", "GET", "/employees?page=1&page_size=2", 200,
              validator=lambda b: (_ for _ in ()).throw(AssertionError("invalid pagination shape"))
              if not isinstance(b, dict) or not isinstance(b.get("items"), list)
              or b.get("page") != 1 or b.get("page_size") != 2 else None)

        check("Reject page=0", "GET", "/employees?page=0&page_size=2", 422)
        check("Reject page_size=0", "GET", "/employees?page=1&page_size=0", 422)

        check("Attendance list", "GET", "/attendance?page=1&page_size=2", 200, validator=has_items)
        check("Reject invalid attendance date", "GET",
              "/attendance?date_from=not-a-date", 422)
        check("Reject reversed attendance date range", "GET",
              "/attendance?date_from=2026-10-11&date_to=2026-10-10", 422)
        check("Reject invalid attendance status", "GET",
              "/attendance?status=NOT_A_STATUS", 422)

        # Analytics smoke checks use broad response-shape assertions, not fixed
        # sample-data values, so the tests remain valid as data changes.
        check("Department summary", "GET",
              "/analytics/departments/summary?month=2026-07", 200,
              validator=lambda b: (_ for _ in ()).throw(AssertionError("missing items"))
              if not isinstance(b, dict) or not isinstance(b.get("items"), list) else None)
        check("Department filter", "GET",
              "/analytics/departments/summary?month=2026-07&department=Engineering", 200,
              validator=lambda b: (_ for _ in ()).throw(AssertionError("unexpected department result"))
              if not isinstance(b, dict) or any(x.get("department") != "Engineering"
                                                 for x in b.get("items", [])) else None)
        check("Reject invalid analytics month", "GET",
              "/analytics/departments/summary?month=invalid", 422)
        check("Late leaderboard", "GET",
              "/analytics/leaderboard/late?month=2026-07", 200,
              validator=has_items)
        check("Employee monthly analytics", "GET",
              "/analytics/employees/EMP0001/monthly?month=2026-07", 200,
              validator=lambda b: (_ for _ in ()).throw(AssertionError("missing monthly fields"))
              if not isinstance(b, dict) or "attendance_pct" not in b else None)
        check("Department trend", "GET",
              "/analytics/departments/Engineering/trend?from=2026-07-01&to=2026-07-07", 200,
              validator=lambda b: (_ for _ in ()).throw(AssertionError("expected trend items"))
              if not isinstance(b, dict) or not isinstance(b.get("items"), list) else None)
        check("Explain department summary", "GET",
              "/admin/explain/department_summary?month=2026-07&department=Engineering", 200,
              validator=lambda b: (_ for _ in ()).throw(AssertionError("explain not successful"))
              if not isinstance(b, dict) or b.get("endpoint") != "department_summary"
              or not isinstance(b.get("explain"), dict) else None)

        # Write-path tests use a unique employee and clean up only this employee.
        employee_payload = {
            "emp_code": EMP_CODE,
            "name": "API Smoke Test Employee",
            "email": EMAIL,
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": TODAY,
        }
        created = check("Create temporary employee", "POST", "/employees", 201,
                        employee_payload,
                        validator=lambda b: (_ for _ in ()).throw(AssertionError("wrong employee code"))
                        if not isinstance(b, dict) or b.get("emp_code") != EMP_CODE else None)
        if created is not None:
            created_employee = True
            check("Reject duplicate employee code", "POST", "/employees", 409, employee_payload)

            bad_employee = dict(employee_payload, emp_code="BAD", email="bad-email")
            check("Reject invalid employee payload", "POST", "/employees", 422, bad_employee)

            # Choose a time in the past on today's local date so punch-out is valid.
            # Fixed 09:30/18:30 timestamps can be future early in the day, so use
            # a completed shift yesterday for reliable reruns at any time.
            test_date = (TODAY_IST - timedelta(days=1)).isoformat()
            punch_in_dt = datetime.fromisoformat(test_date + "T09:30:00+05:30")
            punch_out_dt = datetime.fromisoformat(test_date + "T18:30:00+05:30")
            punch_in_ms = int(punch_in_dt.timestamp() * 1000)
            punch_out_ms = int(punch_out_dt.timestamp() * 1000)

            punch = {"emp_code": EMP_CODE, "punched_at": punch_in_ms, "status": "PRESENT"}
            punched = check("Punch in", "POST", "/attendance/punch-in", 201, punch,
                             validator=lambda b: (_ for _ in ()).throw(AssertionError("wrong punch-in date"))
                             if not isinstance(b, dict) or b.get("date") != test_date else None)
            if punched is not None:
                check("Reject duplicate punch in", "POST", "/attendance/punch-in", 409, punch)
                check("Punch out", "POST", "/attendance/punch-out", 200,
                      {"emp_code": EMP_CODE, "punched_at": punch_out_ms},
                      validator=lambda b: (_ for _ in ()).throw(AssertionError("work_hours should be 9"))
                      if not isinstance(b, dict) or b.get("work_hours") != 9.0 else None)
                check("Reject duplicate punch out", "POST", "/attendance/punch-out", 409,
                      {"emp_code": EMP_CODE, "punched_at": punch_out_ms})

                check("Attendance filter finds test record", "GET",
                      f"/attendance?emp_code={EMP_CODE}&date_from={test_date}&date_to={test_date}", 200,
                      validator=lambda b: (_ for _ in ()).throw(AssertionError("test record missing"))
                      if not isinstance(b, dict) or not any(x.get("emp_code") == EMP_CODE
                                                             for x in b.get("items", [])) else None)

                check("Regularize attendance", "PATCH",
                      f"/attendance/{EMP_CODE}/{test_date}", 200,
                      {"status": "WFH", "reason": "Smoke test regularization",
                       "regularized_by": "api-smoke"},
                      validator=lambda b: (_ for _ in ()).throw(AssertionError("status/history mismatch"))
                      if not isinstance(b, dict) or b.get("status") != "WFH"
                      or not b.get("history") else None)
                check("Reject too-short regularization reason", "PATCH",
                      f"/attendance/{EMP_CODE}/{test_date}", 422,
                      {"status": "PRESENT", "reason": "bad", "regularized_by": "api-smoke"})
                check("Reject regularization for missing record", "PATCH",
                      f"/attendance/{EMP_CODE}/2099-01-01", 404,
                      {"status": "PRESENT", "reason": "Missing record test",
                       "regularized_by": "api-smoke"})

            check("Reject seconds instead of milliseconds", "POST", "/attendance/punch-in", 422,
                  {"emp_code": EMP_CODE, "punched_at": 1783312500, "status": "PRESENT"})
            check("Reject floating-point timestamp", "POST", "/attendance/punch-in", 422,
                  {"emp_code": EMP_CODE, "punched_at": 1783312500000.5, "status": "PRESENT"})
            check("Reject string timestamp", "POST", "/attendance/punch-in", 422,
                  {"emp_code": EMP_CODE, "punched_at": "1783312500000", "status": "PRESENT"})
            check("Reject timestamp below minimum", "POST", "/attendance/punch-in", 422,
                  {"emp_code": EMP_CODE, "punched_at": 99999999999, "status": "PRESENT"})
            check("Reject timestamp above maximum", "POST", "/attendance/punch-in", 422,
                  {"emp_code": EMP_CODE, "punched_at": 4102444800001, "status": "PRESENT"})
            check("Reject negative punch timestamp", "POST", "/attendance/punch-in", 422,
                  {"emp_code": EMP_CODE, "punched_at": -1, "status": "PRESENT"})
            check("Unknown employee punch-in returns 404", "POST", "/attendance/punch-in", 404,
                  {"emp_code": "EMP9999", "punched_at": punch_in_ms, "status": "PRESENT"})

    finally:
        # Delete only records owned by this run, never seeded/sample records.
        try:
            if created_employee:
                db.attendance_logs.delete_many({"emp_code": EMP_CODE})
                db.employees.delete_one({"emp_code": EMP_CODE, "email": EMAIL})
                print("\nCleanup: removed this run's temporary test records.")
            else:
                print("\nCleanup skipped: this run did not create its temporary employee.")
        except Exception as exc:
            print(f"\nWARNING: cleanup failed for {EMP_CODE}: {exc}")
            print("Check this run's temporary records manually if needed.")
        mongo.close()

    print(f"\nResult: {passed} passed, {failed} failed.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
