# HROne Employee Attendance & Analytics API

FastAPI service for the HROne Software Engineer Trainee assignment. The API manages employees, records attendance punches, supports HR attendance regularization, returns attendance analytics, and exposes MongoDB query-plan inspection for key list and analytics operations.

The implementation uses Python, FastAPI, PyMongo, and MongoDB. MongoDB stores employees in `employees` and attendance records in `attendance_logs`; API timestamps are epoch milliseconds while attendance dates are IST `YYYY-MM-DD` strings.

## Features

- Employee creation and paginated employee listing, with optional department filtering.
- Punch-in and punch-out flows for `PRESENT`, `WFH`, and `ON_DUTY` attendance.
- Attendance listing with employee, date-range, status, and pagination filters.
- Attendance regularization with validation, derived-field recomputation, and history entries.
- Monthly employee analytics.
- Department summary analytics.
- Late-arrival leaderboard with ranking and optional department filter.
- Department attendance trend with daily rows and 7-day moving average.
- MongoDB `explain` output with `executionStats` for supported endpoint queries.
- Input validation, HTTP error handling, and startup-created MongoDB indexes.

## Prerequisites

- Python 3.12 recommended for this repository. The assignment contract accepts Python 3.11+.
- MongoDB 6.0 or newer. The assignment grader uses MongoDB 7.0.
- A local MongoDB instance or MongoDB Atlas connection.
- Dependencies from `requirements.txt`: FastAPI, Uvicorn, PyMongo, Pydantic, and python-dotenv.

## Local Setup

Run these commands from the repository root in Windows PowerShell.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

There is no `.env.example` in this checkout. Create a local `.env` with non-secret local values, or set real environment variables before starting the app. Real environment variables take priority over `.env`. Do not commit `.env`.

```powershell
@'
MONGO_URI=mongodb://localhost:27017
MONGO_DB=attendance_db
'@ | Set-Content -Encoding UTF8 .env
```

For a local MongoDB Windows service:

```powershell
Start-Service MongoDB
```

For MongoDB Atlas, set `MONGO_URI` to your Atlas connection string and `MONGO_DB` to the target database. Keep credentials out of git and do not paste real credentials into documentation.

`sample_seed.py` loads the nine sample documents from `sample_data/`, but it first clears the `employees` and `attendance_logs` collections. Use it only against a disposable local/dev database.

```powershell
python .\sample_seed.py
```

Start the API:

```powershell
uvicorn app.main:app --port 8000 --reload
```

Swagger UI is available at `http://127.0.0.1:8000/docs`. The generated OpenAPI JSON is available at `http://127.0.0.1:8000/openapi.json`.

## API Reference

Pagination uses `page` starting at `1` and `page_size` defaulting to `20` with a maximum of `100` where implemented. Dates use `YYYY-MM-DD`, months use `YYYY-MM`, shift times use IST `HH:MM`, and request/response instants use epoch milliseconds.

### Health

| Method | Route | Purpose | Important parameters |
|---|---|---|---|
| `GET` | `/health` | Check API readiness and MongoDB connectivity. | None |

### Employees

| Method | Route | Purpose | Important parameters |
|---|---|---|---|
| `POST` | `/employees` | Create an employee. | Body: `emp_code`, `name`, `email`, `department`, `joined_on`; optional `shift_start`, `shift_end`. |
| `GET` | `/employees` | List employees sorted by `emp_code`. | Query: `department`, `page`, `page_size`. |

### Attendance

| Method | Route | Purpose | Important parameters |
|---|---|---|---|
| `POST` | `/attendance/punch-in` | Create the employee's attendance record for the derived attendance date. | Body: `emp_code`; optional `punched_at`, `status`. |
| `POST` | `/attendance/punch-out` | Close the latest open attendance record at or before the punch-out time. | Body: `emp_code`; optional `punched_at`. |
| `GET` | `/attendance` | List attendance records sorted by `date` descending and `emp_code` ascending. | Query: `emp_code`, `date_from`, `date_to`, `status`, `page`, `page_size`. |
| `PATCH` | `/attendance/{emp_code}/{date}` | Regularize one attendance record and append audit history. | Path: `emp_code`, `date`; body: `reason`, `regularized_by`; optional `status`, `punch_in`, `punch_out`. |

Attendance statuses are `PRESENT`, `WFH`, `ON_DUTY`, `ABSENT`, and `LEAVE`. Punch-in accepts only `PRESENT`, `WFH`, or `ON_DUTY`.

### Analytics

| Method | Route | Purpose | Important parameters |
|---|---|---|---|
| `GET` | `/analytics/employees/{emp_code}/monthly` | Monthly attendance summary for one employee. | Path: `emp_code`; query: `month`. |
| `GET` | `/analytics/departments/summary` | Department summary for a month. | Query: `month`, optional exact-match `department`. |
| `GET` | `/analytics/leaderboard/late` | Late-arrival leaderboard for a month. | Query: `month`, optional `limit` default `10`, optional `department`. |
| `GET` | `/analytics/departments/{department}/trend` | Daily department trend for an inclusive date range. | Path: `department`; query: `from`, `to`. |

The department trend range is limited to 92 days. Unknown departments return `404` for the trend endpoint.

### Admin Explain

| Method | Route | Purpose | Important parameters |
|---|---|---|---|
| `GET` | `/admin/explain/{endpoint}` | Return raw MongoDB `explain` output with `executionStats`. | Path `endpoint`: `attendance_list`, `employee_monthly`, `department_summary`, `late_leaderboard`, `department_trend`. |

The explain endpoint accepts the relevant query parameters for the endpoint being explained, including `emp_code`, `month`, `department`, `limit`, `date_from`, `date_to`, `status`, `from`, `to`, `page`, and `page_size`. For `employee_monthly`, `department_summary`, and `late_leaderboard`, `month` is required; for `employee_monthly`, `emp_code` is also required; for `department_trend`, `department`, `from`, and `to` are required.

## Example Requests

```powershell
curl.exe "http://127.0.0.1:8000/health"
```

```powershell
curl.exe "http://127.0.0.1:8000/employees?page=1&page_size=10&department=Engineering"
```

```powershell
curl.exe -X POST "http://127.0.0.1:8000/employees" `
  -H "Content-Type: application/json" `
  -d '{"emp_code":"EMP9001","name":"Nisha Verma","email":"nisha.verma@example.test","department":"Engineering","shift_start":"09:30","shift_end":"18:30","joined_on":"2026-07-01"}'
```

```powershell
curl.exe -X POST "http://127.0.0.1:8000/attendance/punch-in" `
  -H "Content-Type: application/json" `
  -d '{"emp_code":"EMP9001","punched_at":1783312500000,"status":"PRESENT"}'
```

```powershell
curl.exe "http://127.0.0.1:8000/attendance?emp_code=EMP9001&date_from=2026-07-01&date_to=2026-07-31&page=1&page_size=20"
```

```powershell
curl.exe "http://127.0.0.1:8000/analytics/employees/EMP9001/monthly?month=2026-07"
```

```powershell
curl.exe "http://127.0.0.1:8000/analytics/departments/summary?month=2026-07&department=Engineering"
```

```powershell
curl.exe "http://127.0.0.1:8000/admin/explain/attendance_list?date_from=2026-07-01&date_to=2026-07-31&page=1&page_size=20"
```

## Business Rules and Design

- Attendance dates and shift times are handled in IST. Overnight shifts assign early-morning punches before `shift_end` to the previous attendance date.
- Late arrival uses a 10-minute grace period. A punch more than 10 minutes after shift start records whole elapsed minutes from shift start.
- Overtime is whole minutes after shift end and counts only when at least 30 minutes.
- Work hours are rounded to 2 decimals using half-up rounding. Half day is set when rounded work hours are below `4.50`.
- `PRESENT`, `WFH`, and `ON_DUTY` count as present; `ABSENT` and `LEAVE` do not.
- Working days are Monday through Friday with no holiday calendar. Monthly working days start from the employee's `joined_on`.
- Unique MongoDB indexes enforce unique `emp_code` values and one attendance record per `(emp_code, date)`, which supports concurrent duplicate create and punch-in conflict handling.
- Regularization uses a concurrency filter and appends one history entry for actual changes.
- Analytics use MongoDB aggregation pipelines and stored derived attendance fields. The department summary aggregation starts from eligible employees and looks up attendance logs, so employees with no logs still contribute to headcount.
- Department headcount includes employees whose `joined_on` is on or before the reporting period end.
- Startup creates indexes for employee code, department/employee listing, eligible employee matching, attendance uniqueness, date/employee listing, and status/date filtering.

## Validation and Errors

Errors use FastAPI-style JSON bodies with a `detail` field.

- `404` is returned for missing employees, missing attendance records, no eligible punch-in for punch-out, and unknown departments in trend analytics.
- `409` is returned for duplicate employee codes, duplicate punch-ins for the same attendance date, already closed attendance records, and concurrent update conflicts.
- `422` is returned for invalid request bodies, invalid date/month formats, invalid statuses, invalid pagination, invalid timestamp values, and regularization requests that do not change the record.
- `503` is returned by `/health` when MongoDB is unavailable and by regularization if the database update fails.

No automated test suite is present in this repository. Use the API examples, Swagger UI, and any additional local checks against disposable data.

## Repository Documentation

- `REVIEW.md` lists defects found in the starter implementation, symptoms or tests that expose them, and the applied fixes.
- `DECISIONS.md` summarizes index choices, concurrency handling, leaderboard tie behavior, headcount handling, and scaling considerations.

## Limitations

- This checkout does not include `.env.example`, so local environment setup is documented with a minimal local `.env` template.
- `sample_seed.py` is destructive for the two assignment collections and should not be run against shared or production-like data.
- `punch-in` and `punch-out` validate `punched_at` as a positive integer timestamp, while regularization applies the stricter OpenAPI epoch-millisecond range.
- No automated tests were found in the repository during README inspection.

## Submission and Security

Do not commit `.env`, credentials, virtual environments, cache folders, database dumps, or generated private data. The assignment explicitly excludes adding a Dockerfile. Preserve the repository visibility and confidentiality requirements from the assignment, and keep the full problem statement out of this README.
