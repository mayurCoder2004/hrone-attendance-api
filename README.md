# HROne Employee Attendance & Analytics API

A FastAPI backend for the HROne Software Engineer Trainee assignment. It manages employee records, attendance punch-in and punch-out, HR regularization, attendance analytics, and MongoDB query-plan inspection.

## Features

- Create employees and list them with pagination and optional department filtering.
- Record punch-in and punch-out for `PRESENT`, `WFH`, and `ON_DUTY` attendance.
- Filter attendance records by employee, date range, status, and pagination.
- Regularize attendance records with validation and audit history.
- Calculate monthly employee attendance analytics.
- Summarize attendance by department.
- Produce a late-arrival leaderboard with optional department filtering.
- Return daily department trends and a 7-day moving average.
- Inspect supported query plans using MongoDB `explain` with `executionStats`.
- Create MongoDB indexes at application startup.

## Technology stack

- Python 3.12
- FastAPI and Uvicorn
- PyMongo
- MongoDB (local instance or Atlas)
- Pydantic and python-dotenv

Install the dependencies listed in `requirements.txt`.

## Prerequisites

- Python 3.12 installed and available through the Windows `py` launcher.
- Access to a MongoDB instance.
- Windows PowerShell for the commands in this guide.

## Local setup (Windows PowerShell)

Run the commands from the repository root.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If PowerShell blocks virtual-environment activation, allow it for the current PowerShell process and activate again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

## Environment configuration

The application loads environment variables from `.env` and reads:

| Variable | Default | Purpose |
|---|---|---|
| `MONGO_URI` | `mongodb://localhost:27017` | MongoDB connection string. |
| `MONGO_DB` | `attendance_db` | Database name. |

For a local MongoDB instance, you can create a `.env` file with:

```powershell
@'
MONGO_URI=mongodb://localhost:27017
MONGO_DB=attendance_db
'@ | Set-Content -Encoding UTF8 .env
```

For MongoDB Atlas, set `MONGO_URI` to your Atlas connection string and `MONGO_DB` to the database name you intend to use. Keep credentials out of source control and documentation. Do not commit `.env`.

## Database setup and sample data

Start your local MongoDB service if you use one:

```powershell
Start-Service MongoDB
```

The command above applies only if MongoDB is installed as a Windows service. For Atlas, ensure your connection string, network access, and database credentials are configured.

**Warning:** `sample_seed.py` clears the `employees` and `attendance_logs` collections before inserting sample data. Run it only against a disposable local or development database, never against production or shared data.

To load the sample data:

```powershell
python .\sample_seed.py
```

The sample data contains six employees and nine attendance records.

## Run the API

```powershell
uvicorn app.main:app --port 8000 --reload
```

The application creates its MongoDB indexes on startup.

- Swagger UI: <http://127.0.0.1:8000/docs>
- OpenAPI JSON: <http://127.0.0.1:8000/openapi.json>
- OpenAPI specification in the repository: `openapi.yaml`

## API reference

Dates use `YYYY-MM-DD`, months use `YYYY-MM`, shift times use 24-hour `HH:MM`, and request/response instants use epoch milliseconds. Where pagination is supported, `page` starts at `1`, `page_size` defaults to `20`, and the maximum page size is `100`.

### Health

| Method | Route | Description |
|---|---|---|
| `GET` | `/health` | Pings MongoDB and returns `{"status":"ok"}` when available. Returns `503` if MongoDB is unavailable. |

### Employees

| Method | Route | Description |
|---|---|---|
| `POST` | `/employees` | Creates an employee. Required fields: `emp_code`, `name`, `email`, `department`, and `joined_on`. `shift_start` and `shift_end` default to `09:30` and `18:30`. |
| `GET` | `/employees` | Lists employees sorted by `emp_code` ascending. Supports `department`, `page`, and `page_size` query parameters. |

Employee codes must match `EMP` followed by 4 to 6 digits.

### Attendance

| Method | Route | Description |
|---|---|---|
| `POST` | `/attendance/punch-in` | Records an employee's punch-in. Body fields include `emp_code`, optional `punched_at`, and optional `status`. |
| `POST` | `/attendance/punch-out` | Closes the latest eligible open attendance record. Body fields include `emp_code` and optional `punched_at`. |
| `GET` | `/attendance` | Lists attendance records. Supports `emp_code`, `date_from`, `date_to`, `status`, `page`, and `page_size`. |
| `PATCH` | `/attendance/{emp_code}/{date}` | Regularizes a record. Requires `reason` and `regularized_by`; optional fields are `status`, `punch_in`, and `punch_out`. |

Attendance statuses are `PRESENT`, `WFH`, `ON_DUTY`, `ABSENT`, and `LEAVE`. Punch-in accepts only `PRESENT`, `WFH`, and `ON_DUTY`.

### Analytics

| Method | Route | Description |
|---|---|---|
| `GET` | `/analytics/employees/{emp_code}/monthly?month=YYYY-MM` | Monthly attendance summary for an employee. |
| `GET` | `/analytics/departments/summary?month=YYYY-MM` | Department summary for a month. An optional `department` query parameter filters the results. |
| `GET` | `/analytics/leaderboard/late?month=YYYY-MM` | Late-arrival leaderboard. Supports `limit` (default `10`, range `1`-`50`) and optional `department`. |
| `GET` | `/analytics/departments/{department}/trend?from=YYYY-MM-DD&to=YYYY-MM-DD` | Daily department trend for an inclusive date range of at most 92 days. |

The monthly employee endpoint requires an existing employee. The department trend endpoint returns `404` for an unknown department.

### Admin explain

| Method | Route | Description |
|---|---|---|
| `GET` | `/admin/explain/{endpoint}` | Returns MongoDB `explain` output with `executionStats` for a supported operation. |

Supported endpoint identifiers are `attendance_list`, `employee_monthly`, `department_summary`, `late_leaderboard`, and `department_trend`. The explain endpoint accepts the parameters required by the selected operation, such as `emp_code`, `month`, `department`, `limit`, date filters, and pagination.

## Request examples

Run these examples while the API is running. Use employee codes and dates that exist in your database where an existing employee or attendance record is required.

### Health check

```powershell
curl.exe "http://127.0.0.1:8000/health"
```

### Create an employee

```powershell
curl.exe -X POST "http://127.0.0.1:8000/employees" `
  -H "Content-Type: application/json" `
  -d '{"emp_code":"EMP9001","name":"Nisha Verma","email":"nisha.verma@example.test","department":"Engineering","shift_start":"09:30","shift_end":"18:30","joined_on":"2026-07-01"}'
```

Use a new employee code when running this example; employee codes must be unique.

### List employees

```powershell
curl.exe "http://127.0.0.1:8000/employees?page=1&page_size=10&department=Engineering"
```

### Punch in and punch out

The following timestamps are illustrative epoch-millisecond values. For reliable testing, use valid timestamps appropriate to the attendance date and employee shift.

```powershell
curl.exe -X POST "http://127.0.0.1:8000/attendance/punch-in" `
  -H "Content-Type: application/json" `
  -d '{"emp_code":"EMP9001","punched_at":1783310400000,"status":"PRESENT"}'
```

```powershell
curl.exe -X POST "http://127.0.0.1:8000/attendance/punch-out" `
  -H "Content-Type: application/json" `
  -d '{"emp_code":"EMP9001","punched_at":1783343100000}'
```

### List attendance

```powershell
curl.exe "http://127.0.0.1:8000/attendance?emp_code=EMP9001&date_from=2026-07-01&date_to=2026-07-31&page=1&page_size=20"
```

### Regularize attendance

Use a record that already exists for the employee and date.

```powershell
curl.exe -X PATCH "http://127.0.0.1:8000/attendance/EMP9001/2026-07-06" `
  -H "Content-Type: application/json" `
  -d '{"status":"WFH","reason":"Approved remote work correction","regularized_by":"hr.admin"}'
```

### Analytics

```powershell
curl.exe "http://127.0.0.1:8000/analytics/employees/EMP9001/monthly?month=2026-07"
curl.exe "http://127.0.0.1:8000/analytics/departments/summary?month=2026-07&department=Engineering"
curl.exe "http://127.0.0.1:8000/analytics/leaderboard/late?month=2026-07&limit=10"
curl.exe "http://127.0.0.1:8000/analytics/departments/Engineering/trend?from=2026-07-01&to=2026-07-07"
```

### Explain an endpoint query

```powershell
curl.exe "http://127.0.0.1:8000/admin/explain/attendance_list?date_from=2026-07-01&date_to=2026-07-31&page=1&page_size=20"
```

## Business rules and calculations

- Attendance dates and shift times are handled in IST.
- Overnight shifts are supported. If `shift_end <= shift_start`, early-morning punches before `shift_end` are assigned to the previous attendance date.
- Late arrival has a 10-minute grace period. A punch more than 10 minutes after shift start records whole elapsed minutes from shift start.
- Work hours are calculated from punch-in to punch-out and rounded to two decimal places using half-up rounding.
- Overtime is measured in whole minutes after shift end and counts only when the overtime duration is at least 30 minutes.
- `half_day` is `true` when rounded `work_hours` is below `4.50`.
- `PRESENT`, `WFH`, and `ON_DUTY` count as presence statuses; `ABSENT` and `LEAVE` do not.
- Working days are Monday through Friday. There is no holiday calendar.
- Monthly employee analytics starts from the later of the month start and the employee's `joined_on` date.
- Department summary headcount includes employees who joined on or before the reporting month end. Department trend headcount is calculated for each day.
- The late leaderboard uses MongoDB `$rank`; tied employees share a rank.

## Validation and HTTP errors

Selected validation rules include:

- Employee code must match `EMP` followed by 4 to 6 digits.
- Name, email, and department cannot be blank; email must match the implemented email pattern.
- Shift times must use valid 24-hour `HH:MM` values and cannot be equal.
- Date fields must use valid `YYYY-MM-DD` dates; month fields must use `YYYY-MM`.
- Pagination requires `page >= 1` and `1 <= page_size <= 100`.
- Punch-in status must be `PRESENT`, `WFH`, or `ON_DUTY`.
- Regularization requires a reason and the regularizer's identifier. The trimmed reason must contain at least five characters.
- Punch-out must be after punch-in and no more than 24 hours after it.
- Regularization rejects no-op changes. Setting `ABSENT` or `LEAVE` rejects supplied punch times; presence statuses require `punch_in`.

Common HTTP statuses:

| Status | Meaning |
|---|---|
| `404` | Employee or attendance record not found, no eligible punch-in for punch-out, or unknown department for trend analytics. |
| `409` | Duplicate employee code, duplicate attendance record, already closed record, or concurrent update conflict. |
| `422` | Invalid request data, date/month, status, pagination, timestamp, range, or no-op regularization. |
| `503` | MongoDB unavailable for `/health`, or a regularization database update failure. |

Errors use JSON responses with a `detail` field.

## Database indexes and design decisions

Indexes are created in `app/main.py` at application startup.

| Collection | Index | Purpose |
|---|---|---|
| `employees` | `uq_employees_emp_code` - unique `emp_code` | Enforces unique employee codes. |
| `employees` | `ix_employees_department_emp_code` - `department`, `emp_code` | Supports department-filtered employee listing. |
| `employees` | `ix_employees_joined_on_department` - `joined_on`, `department` | Supports employee eligibility and headcount queries. |
| `attendance_logs` | `uq_attendance_emp_code_date` - unique `emp_code`, `date` | Enforces one attendance record per employee per date. |
| `attendance_logs` | `ix_attendance_date_emp_code` - `date` descending, `emp_code` | Supports attendance date sorting and filtering. |
| `attendance_logs` | `ix_attendance_status_date` - `status`, `date` descending | Supports status/date filtering. |

Important implementation choices:

- Unique indexes enforce employee-code and employee/date uniqueness.
- Punch-out updates an eligible open attendance record and detects conflicts.
- Regularization applies a concurrency filter and appends history for actual changes.
- Analytics use MongoDB aggregation pipelines and stored derived attendance fields.
- `REVIEW.md` records starter defects and fixes; `DECISIONS.md` describes index choices, concurrency handling, leaderboard ties, headcount, and scaling considerations.

## Automated API smoke tests

If `test_api.py` is present in your local checkout, it can be run as a script-style smoke/integration test. Start the API in one terminal:

```powershell
uvicorn app.main:app --port 8000 --reload
```

In a second terminal, activate the virtual environment and run:

```powershell
.\.venv\Scripts\Activate.ps1
python test_api.py
```

The script uses the database configured by `.env`, creates temporary test records, and cleans up only its own records. Run it only against a disposable development or test database, never against production or shared data. Verify `MONGO_URI` and `MONGO_DB` before running it, and review the script's output and cleanup messages. Smoke tests are not exhaustive coverage of every concurrency case, analytics edge case, date boundary, or malformed payload.

## Known limitations

- There is no authentication or authorization layer.
- There is no holiday calendar; weekdays Monday through Friday are treated as working days.
- Punch-in, punch-out, and regularization validate epoch-millisecond timestamps as strict integers within the supported range. Invalid seconds-based, floating-point, string, negative, and out-of-range values are rejected.
- The smoke tests do not exhaustively cover all edge cases.
- No `.env.example` is provided; environment setup is documented above.

## Security and submission notes

- Never commit `.env`, credentials, database dumps, virtual environments, caches, or private generated data.
- Keep MongoDB Atlas credentials out of documentation and command history where possible.
- `sample_seed.py` deletes records from the `employees` and `attendance_logs` collections before loading sample data. Use it only with disposable development data.
- The assignment excludes a Dockerfile; this project does not require one for local execution.
