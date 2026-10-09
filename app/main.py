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
from pydantic import BaseModel, Field, StrictInt
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


class RegularizeIn(BaseModel):
    status: Optional[str] = None
    punch_in: Optional[StrictInt] = None
    punch_out: Optional[StrictInt] = None
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    emp_code: str,
    date: str,
    payload: RegularizeIn,
):
    from pymongo.errors import PyMongoError

    try:
        parsed_date = datetime.strptime(date, "%Y-%m-%d")
        if parsed_date.strftime("%Y-%m-%d") != date:
            raise ValueError("Invalid date")
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="date must use YYYY-MM-DD format",
        )

    reason = payload.reason.strip()
    regularized_by = payload.regularized_by.strip()

    if len(reason) < 5:
        raise HTTPException(status_code=422, detail="reason must contain at least 5 non-space characters")
    if not regularized_by:
        raise HTTPException(status_code=422, detail="regularized_by cannot be blank")

    supplied = payload.model_fields_set

    if payload.status is not None and payload.status not in {
        "PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"
    }:
        raise HTTPException(status_code=422, detail="Invalid attendance status")

    if "status" in supplied and payload.status is None:
        raise HTTPException(status_code=422, detail="status cannot be null")
    if "punch_in" in supplied and payload.punch_in is None:
        raise HTTPException(status_code=422, detail="punch_in cannot be null")
    if "punch_out" in supplied and payload.punch_out is None:
        raise HTTPException(status_code=422, detail="punch_out cannot be null")

    def parse_epoch_ms(value):
        if value is None:
            return None
        if value < 100000000000 or value > 4102444800000:
            raise HTTPException(
                status_code=422,
                detail="Timestamps must be epoch milliseconds in the supported range",
            )
        try:
            return datetime.fromtimestamp(
                value / 1000,
                tz=timezone.utc,
            ).replace(microsecond=0)
        except (OverflowError, OSError, ValueError):
            raise HTTPException(status_code=422, detail="Invalid timestamp")

    employee = db.employees.find_one({"emp_code": emp_code})
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    original = db.attendance_logs.find_one({
        "emp_code": emp_code,
        "date": date,
    })

    if original is None:
        raise HTTPException(status_code=404, detail="Attendance record not found")

    old_status = original.get("status")
    old_in = original.get("punch_in")
    old_out = original.get("punch_out")

    new_status = payload.status if "status" in supplied else old_status
    new_in = parse_epoch_ms(payload.punch_in) if "punch_in" in supplied else old_in
    new_out = parse_epoch_ms(payload.punch_out) if "punch_out" in supplied else old_out

    if new_status in {"ABSENT", "LEAVE"}:
        if "punch_in" in supplied or "punch_out" in supplied:
            raise HTTPException(
                status_code=422,
                detail="Do not supply punch times when setting ABSENT or LEAVE",
            )
        new_in = None
        new_out = None

    elif new_status in {"PRESENT", "WFH", "ON_DUTY"}:
        if new_in is None:
            raise HTTPException(
                status_code=422,
                detail="A presence status requires punch_in",
            )

        attendance_day = attendance_date_for_punch(
            new_in,
            employee["shift_start"],
            employee["shift_end"],
        )
        if attendance_day != date:
            raise HTTPException(
                status_code=422,
                detail="punch_in must belong to the attendance date",
            )

        if new_out is not None:
            duration = (as_utc(new_out) - as_utc(new_in)).total_seconds()
            if duration <= 0:
                raise HTTPException(
                    status_code=422,
                    detail="punch_out must be after punch_in",
                )
            if duration > 86400:
                raise HTTPException(
                    status_code=422,
                    detail="punch_out cannot be more than 24 hours after punch_in",
                )
    else:
        raise HTTPException(status_code=422, detail="Invalid attendance status")

    if new_status in {"ABSENT", "LEAVE"}:
        new_work_hours = None
        new_late_minutes = 0
        new_overtime_minutes = 0
        new_half_day = False
    else:
        new_late_minutes = compute_late_minutes(
            new_in,
            employee["shift_start"],
            date,
        )

        if new_out is None:
            new_work_hours = None
            new_overtime_minutes = 0
            new_half_day = False
        else:
            new_work_hours = compute_work_hours(new_in, new_out)
            new_overtime_minutes = compute_overtime(
                new_out,
                employee["shift_start"],
                employee["shift_end"],
                date,
            )
            new_half_day = new_work_hours < 4.50

    old_values = {
        "status": old_status,
        "punch_in": old_in,
        "punch_out": old_out,
        "work_hours": original.get("work_hours"),
        "late_minutes": original.get("late_minutes", 0),
        "overtime_minutes": original.get("overtime_minutes", 0),
        "half_day": original.get("half_day", False),
    }

    new_values = {
        "status": new_status,
        "punch_in": new_in,
        "punch_out": new_out,
        "work_hours": new_work_hours,
        "late_minutes": new_late_minutes,
        "overtime_minutes": new_overtime_minutes,
        "half_day": new_half_day,
    }

    changes = {}

    for field in (
        "status",
        "punch_in",
        "punch_out",
        "work_hours",
        "late_minutes",
        "overtime_minutes",
        "half_day",
    ):
        old_value = old_values[field]
        new_value = new_values[field]

        if field in {"punch_in", "punch_out"} and old_value is not None and new_value is not None:
            equal = as_utc(old_value) == as_utc(new_value)
        else:
            equal = old_value == new_value

        if not equal:
            changes[field] = {
                "from": as_utc(old_value) if field in {"punch_in", "punch_out"} and old_value is not None else old_value,
                "to": as_utc(new_value) if field in {"punch_in", "punch_out"} and new_value is not None else new_value,
            }

    if not changes:
        raise HTTPException(status_code=422, detail="The request does not change the record")

    history_entry = {
        "at": datetime.now(timezone.utc).replace(microsecond=0),
        "by": regularized_by,
        "reason": reason,
        "changes": changes,
    }

    # Match the original values so concurrent corrections cannot silently
    # overwrite one another or lose an audit entry.
    concurrency_filter = {"_id": original["_id"]}
    for field in ("status", "punch_in", "punch_out"):
        if field in original:
            concurrency_filter[field] = original[field]
        else:
            concurrency_filter[field] = {"$exists": False}

    try:
        result = db.attendance_logs.update_one(
            concurrency_filter,
            {
                "$set": {
                    "status": new_status,
                    "punch_in": new_in,
                    "punch_out": new_out,
                    "work_hours": new_work_hours,
                    "late_minutes": new_late_minutes,
                    "overtime_minutes": new_overtime_minutes,
                    "half_day": new_half_day,
                },
                "$push": {"history": history_entry},
            },
        )
    except PyMongoError:
        raise HTTPException(status_code=503, detail="Database update failed")

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Attendance record was updated by another request",
        )

    record = db.attendance_logs.find_one(
        {"emp_code": emp_code, "date": date},
        {"_id": 0},
    )

    record["punch_in"] = to_epoch_ms(record.get("punch_in"))
    record["punch_out"] = to_epoch_ms(record.get("punch_out"))

    for entry in record.get("history", []):
        entry["at"] = to_epoch_ms(entry.get("at"))
        for field in ("punch_in", "punch_out"):
            if field in entry.get("changes", {}):
                for direction in ("from", "to"):
                    entry["changes"][field][direction] = to_epoch_ms(
                        entry["changes"][field].get(direction)
                    )

    record.setdefault("history", [])
    record.setdefault("half_day", False)
    record.setdefault("late_minutes", 0)
    record.setdefault("overtime_minutes", 0)

    return record



def round_half_up(value, places=2):
    quantum = Decimal("1").scaleb(-places)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    # Validate YYYY-MM strictly.
    try:
        month_start = datetime.strptime(month, "%Y-%m").date()
        if month_start.strftime("%Y-%m") != month:
            raise ValueError("Invalid month")
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="month must use YYYY-MM format",
        )

    employee = db.employees.find_one(
        {"emp_code": emp_code},
        {"_id": 0, "emp_code": 1, "joined_on": 1},
    )
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    try:
        joined_on = datetime.strptime(employee["joined_on"], "%Y-%m-%d").date()
    except (KeyError, ValueError):
        raise HTTPException(status_code=500, detail="Employee has invalid joined_on data")

    next_month = (
        month_start.replace(day=28) + timedelta(days=4)
    ).replace(day=1)
    month_end = next_month - timedelta(days=1)

    effective_start = max(month_start, joined_on)
    working_days = 0
    current_day = effective_start
    while current_day <= month_end:
        if current_day.weekday() < 5:
            working_days += 1
        current_day += timedelta(days=1)

    pipeline = [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {
                    "$gte": month_start.isoformat(),
                    "$lte": month_end.isoformat(),
                },
            }
        },
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    {
                                        "$lt": [
                                            {
                                                "$dayOfWeek": {
                                                    "$dateFromString": {
                                                        "dateString": "$date",
                                                        "format": "%Y-%m-%d",
                                                        "timezone": "UTC",
                                                    }
                                                }
                                            },
                                            7,
                                        ]
                                    },
                                    {
                                        "$gt": [
                                            {
                                                "$dayOfWeek": {
                                                    "$dateFromString": {
                                                        "dateString": "$date",
                                                        "format": "%Y-%m-%d",
                                                        "timezone": "UTC",
                                                    }
                                                }
                                            },
                                            1,
                                        ]
                                    },
                                ]
                            },
                            {
                                "$cond": [
                                    {"$eq": [{"$ifNull": ["$half_day", False]}, True]},
                                    0.5,
                                    1,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]},
                            1,
                            0,
                        ]
                    }
                },
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$late_minutes", 0]}
                },
                "total_overtime_minutes": {
                    "$sum": {"$ifNull": ["$overtime_minutes", 0]}
                },
            }
        },
    ]

    results = list(db.attendance_logs.aggregate(pipeline))
    totals = results[0] if results else {}

    present_days = float(totals.get("present_days", 0))
    attendance_pct = (
        round_half_up(present_days / working_days * 100, 4)
        if working_days > 0
        else None
    )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": totals.get("leave_days", 0),
        "late_count": totals.get("late_count", 0),
        "total_late_minutes": totals.get("total_late_minutes", 0),
        "total_overtime_minutes": totals.get("total_overtime_minutes", 0),
        "attendance_pct": attendance_pct,
    }

@app.get("/analytics/departments/summary")
def department_summary(month: str):
    try:
        month_start = datetime.strptime(month, "%Y-%m").date()
        if month_start.strftime("%Y-%m") != month:
            raise ValueError("Invalid month")
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="month must use YYYY-MM format",
        )

    next_month = (
        month_start.replace(day=28) + timedelta(days=4)
    ).replace(day=1)
    month_end = next_month - timedelta(days=1)

    # Include only employees who had joined by the reporting period end.
    employees = list(
        db.employees.find(
            {"joined_on": {"$lte": month_end.isoformat()}},
            {"_id": 0, "emp_code": 1, "department": 1, "joined_on": 1},
        ).sort("emp_code", ASCENDING)
    )

    if not employees:
        return {"month": month, "departments": []}

    # Aggregate attendance in MongoDB; do not fetch individual logs for Python loops.
    pipeline = [
        {
            "$match": {
                "date": {
                    "$gte": month_start.isoformat(),
                    "$lte": month_end.isoformat(),
                }
            }
        },
        {
            "$addFields": {
                "_date": {
                    "$dateFromString": {
                        "dateString": "$date",
                        "format": "%Y-%m-%d",
                        "timezone": "UTC",
                    }
                }
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "record_count": {"$sum": 1},
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    {"$gt": [{"$dayOfWeek": "$_date"}, 1]},
                                    {"$lt": [{"$dayOfWeek": "$_date"}, 7]},
                                ]
                            },
                            {
                                "$cond": [
                                    {"$eq": [{"$ifNull": ["$half_day", False]}, True]},
                                    0.5,
                                    1,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]},
                            1,
                            0,
                        ]
                    }
                },
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$late_minutes", 0]}
                },
                "total_overtime_minutes": {
                    "$sum": {"$ifNull": ["$overtime_minutes", 0]}
                },
                "work_hours_sum": {
                    "$sum": {"$ifNull": ["$work_hours", 0]}
                },
                "work_hours_count": {
                    "$sum": {
                        "$cond": [
                            {"$ne": [{"$ifNull": ["$work_hours", None]}, None]},
                            1,
                            0,
                        ]
                    }
                },
            }
        },
    ]

    attendance_by_employee = {
        item["_id"]: item
        for item in db.attendance_logs.aggregate(pipeline)
    }

    # Employees with no logs contribute to headcount and zero attendance totals.
    departments = {}
    for employee in employees:
        department = employee.get("department", "Unknown")
        emp_code = employee["emp_code"]
        totals = attendance_by_employee.get(emp_code, {})

        summary = departments.setdefault(
            department,
            {
                "department": department,
                "headcount": 0,
                "present_days": 0.0,
                "leave_days": 0,
                "late_count": 0,
                "total_late_minutes": 0,
                "total_overtime_minutes": 0,
                "work_hours_sum": 0.0,
                "work_hours_count": 0,
            },
        )

        summary["headcount"] += 1
        summary["present_days"] += float(totals.get("present_days", 0))
        summary["leave_days"] += totals.get("leave_days", 0)
        summary["late_count"] += totals.get("late_count", 0)
        summary["total_late_minutes"] += totals.get("total_late_minutes", 0)
        summary["total_overtime_minutes"] += totals.get("total_overtime_minutes", 0)
        summary["work_hours_sum"] += totals.get("work_hours_sum", 0)
        summary["work_hours_count"] += totals.get("work_hours_count", 0)

    output = []
    for department in sorted(departments):
        summary = departments[department]
        count = summary.pop("work_hours_count")
        work_hours_sum = summary.pop("work_hours_sum")
        summary["avg_work_hours"] = (
            round_half_up(work_hours_sum / count, 2) if count else 0.0
        )
        summary["present_days"] = round_half_up(summary["present_days"], 2)
        output.append(summary)

    return {"month": month, "departments": output}
