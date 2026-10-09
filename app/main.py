"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
import re
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def attendance_date_for_punch(
    punch_in: datetime,
    shift_start: str,
    shift_end: str,
) -> str:
    local_punch = punch_in.astimezone(IST)
    attendance_day = local_punch.date()

    if (
        shift_end <= shift_start
        and local_punch.strftime("%H:%M") < shift_end
    ):
        attendance_day -= timedelta(days=1)

    return attendance_day.isoformat()


def compute_late_minutes(
    punch_in: datetime,
    shift_start: str,
    attendance_date: str,
) -> int:
    shift_day = datetime.strptime(attendance_date, "%Y-%m-%d").date()
    shift_time = datetime.strptime(shift_start, "%H:%M").time()
    shift_start_local = datetime.combine(shift_day, shift_time, tzinfo=IST)
    punch_local = punch_in.astimezone(IST)

    elapsed_seconds = (punch_local - shift_start_local).total_seconds()

    if elapsed_seconds > 600:
        return int(elapsed_seconds // 60)

    return 0


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    seconds = (as_utc(punch_out) - as_utc(punch_in)).total_seconds()
    hours = Decimal(str(seconds)) / Decimal("3600")
    return float(hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def compute_overtime(
    punch_out: datetime,
    shift_start: str,
    shift_end: str,
    date_str: str,
) -> int:
    shift_day = datetime.strptime(date_str, "%Y-%m-%d").date()
    end_time = datetime.strptime(shift_end, "%H:%M").time()
    shift_end_local = datetime.combine(shift_day, end_time, tzinfo=IST)

    if shift_end <= shift_start:
        shift_end_local += timedelta(days=1)

    elapsed_seconds = (
        punch_out.astimezone(IST) - shift_end_local
    ).total_seconds()

    if elapsed_seconds < 1800:
        return 0

    return int(elapsed_seconds // 60)


def to_epoch_ms(value: Optional[datetime]) -> Optional[int]:
    if value is None:
        return None
    return int(as_utc(value).timestamp() * 1000)
# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.on_event("startup")
def create_indexes():
    db.employees.create_index(
        [("emp_code", ASCENDING)],
        unique=True,
        name="uq_employees_emp_code",
    )
    db.employees.create_index(
        [("department", ASCENDING), ("emp_code", ASCENDING)],
        name="ix_employees_department_emp_code",
    )

    db.attendance_logs.create_index(
        [("emp_code", ASCENDING), ("date", ASCENDING)],
        unique=True,
        name="uq_attendance_emp_code_date",
    )
    db.attendance_logs.create_index(
        [("date", DESCENDING), ("emp_code", ASCENDING)],
        name="ix_attendance_date_emp_code",
    )
    db.attendance_logs.create_index(
        [("status", ASCENDING), ("date", DESCENDING)],
        name="ix_attendance_status_date",
    )


@app.get("/health")
def health():
    try:
        client.admin.command("ping")
    except PyMongoError:
        raise HTTPException(
            status_code=503,
            detail="MongoDB unavailable",
        )
    return {"status": "ok"}
@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if not re.fullmatch(r"EMP\d{4,6}", body.emp_code):
        raise HTTPException(
            status_code=422,
            detail="emp_code must start with EMP followed by 4 to 6 digits",
        )

    for field_name in ("name", "email", "department"):
        value = getattr(body, field_name)
        if not value.strip():
            raise HTTPException(
                status_code=422,
                detail=f"{field_name} must not be empty",
            )

    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", body.email):
        raise HTTPException(status_code=422, detail="Invalid email format")

    time_pattern = r"(?:[01]\d|2[0-3]):[0-5]\d"
    if not re.fullmatch(time_pattern, body.shift_start):
        raise HTTPException(
            status_code=422,
            detail="shift_start must use HH:MM in 24-hour format",
        )
    if not re.fullmatch(time_pattern, body.shift_end):
        raise HTTPException(
            status_code=422,
            detail="shift_end must use HH:MM in 24-hour format",
        )
    if body.shift_start == body.shift_end:
        raise HTTPException(
            status_code=422,
            detail="shift_start and shift_end must be different",
        )

    try:
        joined_date = datetime.strptime(body.joined_on, "%Y-%m-%d")
        if joined_date.strftime("%Y-%m-%d") != body.joined_on:
            raise ValueError("Invalid date format")
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="joined_on must be a valid date in YYYY-MM-DD format",
        )

    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc)

    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="emp_code already exists",
        )

    doc.pop("_id", None)
    doc["created_at"] = int(doc["created_at"].timestamp() * 1000)
    return doc
@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    if page < 1:
        raise HTTPException(status_code=422, detail="page must be at least 1")

    if page_size < 1 or page_size > 100:
        raise HTTPException(
            status_code=422,
            detail="page_size must be between 1 and 100",
        )

    query = {}
    if department is not None:
        query["department"] = department

    skip = (page - 1) * page_size
    total = db.employees.count_documents(query)

    items = list(
        db.employees.find(query, {"_id": 0})
        .sort("emp_code", 1)
        .skip(skip)
        .limit(page_size)
    )

    for employee in items:
        created_at = employee.get("created_at")
        if isinstance(created_at, datetime):
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            employee["created_at"] = int(created_at.timestamp() * 1000)

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }
@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    allowed_statuses = {"PRESENT", "WFH", "ON_DUTY"}

    if body.status not in allowed_statuses:
        raise HTTPException(
            status_code=422,
            detail="status must be PRESENT, WFH, or ON_DUTY",
        )

    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    if body.punched_at is None:
        ts = datetime.now(timezone.utc).replace(microsecond=0)
    else:
        if body.punched_at <= 0:
            raise HTTPException(
                status_code=422,
                detail="punched_at must be a positive epoch-millisecond timestamp",
            )
        try:
            ts = datetime.fromtimestamp(
                body.punched_at / 1000,
                tz=timezone.utc,
            ).replace(microsecond=0)
        except (ValueError, OSError, OverflowError):
            raise HTTPException(
                status_code=422,
                detail="Invalid punched_at timestamp",
            )

    attendance_date = attendance_date_for_punch(
        ts,
        emp["shift_start"],
        emp["shift_end"],
    )

    doc = {
        "emp_code": body.emp_code,
        "date": attendance_date,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(
            ts,
            emp["shift_start"],
            attendance_date,
        ),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="already punched in for this attendance date",
        )

    doc.pop("_id", None)
    doc["punch_in"] = to_epoch_ms(doc["punch_in"])
    doc["punch_out"] = to_epoch_ms(doc["punch_out"])
    return doc
@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    if body.punched_at is None:
        ts = datetime.now(timezone.utc).replace(microsecond=0)
    else:
        if body.punched_at <= 0:
            raise HTTPException(
                status_code=422,
                detail="punched_at must be a positive epoch-millisecond timestamp",
            )
        try:
            ts = datetime.fromtimestamp(
                body.punched_at / 1000,
                tz=timezone.utc,
            ).replace(microsecond=0)
        except (ValueError, OSError, OverflowError):
            raise HTTPException(
                status_code=422,
                detail="Invalid punched_at timestamp",
            )

    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$lte": ts},
        },
        sort=[("punch_in", DESCENDING)],
    )

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="No punch-in found before the requested punch-out time",
        )

    if record.get("punch_out") is not None:
        raise HTTPException(
            status_code=409,
            detail="Attendance record is already punched out",
        )

    punch_in_time = as_utc(record["punch_in"])
    if ts <= punch_in_time:
        raise HTTPException(
            status_code=422,
            detail="punch-out must be after punch-in",
        )

    if (ts - punch_in_time).total_seconds() > 86400:
        raise HTTPException(
            status_code=422,
            detail="punch-out cannot be more than 24 hours after punch-in",
        )

    work_hours = compute_work_hours(punch_in_time, ts)
    overtime_minutes = compute_overtime(
        ts,
        emp["shift_start"],
        emp["shift_end"],
        record["date"],
    )
    half_day = work_hours < 4.50

    result = db.attendance_logs.update_one(
        {"_id": record["_id"], "punch_out": None},
        {
            "$set": {
                "punch_out": ts,
                "work_hours": work_hours,
                "overtime_minutes": overtime_minutes,
                "half_day": half_day,
            }
        },
    )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Attendance record was updated by another request",
        )

    record["punch_out"] = ts
    record["work_hours"] = work_hours
    record["overtime_minutes"] = overtime_minutes
    record["half_day"] = half_day
    record.pop("_id", None)
    record["punch_in"] = to_epoch_ms(record.get("punch_in"))
    record["punch_out"] = to_epoch_ms(record.get("punch_out"))
    return record


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    if page < 1:
        raise HTTPException(
            status_code=422,
            detail="page must be at least 1",
        )

    if page_size < 1 or page_size > 100:
        raise HTTPException(
            status_code=422,
            detail="page_size must be between 1 and 100",
        )

    for field_name, date_value in (
        ("date_from", date_from),
        ("date_to", date_to),
    ):
        if date_value is not None:
            try:
                parsed_date = datetime.strptime(date_value, "%Y-%m-%d")
                if parsed_date.strftime("%Y-%m-%d") != date_value:
                    raise ValueError("Invalid date")
            except ValueError:
                raise HTTPException(
                    status_code=422,
                    detail=f"{field_name} must use YYYY-MM-DD format",
                )

    if date_from and date_to and date_from > date_to:
        raise HTTPException(
            status_code=422,
            detail="date_from must be on or before date_to",
        )

    allowed_statuses = {"PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"}
    if status is not None and status not in allowed_statuses:
        raise HTTPException(
            status_code=422,
            detail="Invalid attendance status",
        )

    query = {}

    if emp_code is not None:
        query["emp_code"] = emp_code

    if status is not None:
        query["status"] = status

    if date_from is not None or date_to is not None:
        date_filter = {}
        if date_from is not None:
            date_filter["$gte"] = date_from
        if date_to is not None:
            date_filter["$lte"] = date_to
        query["date"] = date_filter

    total = db.attendance_logs.count_documents(query)
    skip = (page - 1) * page_size

    items = list(
        db.attendance_logs.find(query, {"_id": 0})
        .sort([("date", DESCENDING), ("emp_code", ASCENDING)])
        .skip(skip)
        .limit(page_size)
    )

    for item in items:
        item["punch_in"] = to_epoch_ms(item.get("punch_in"))
        item["punch_out"] = to_epoch_ms(item.get("punch_out"))

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }
# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #










