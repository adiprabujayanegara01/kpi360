# KPI360
### Integrated KPI and Employee Performance Management Platform

KPI360 is a web application designed to manage the employee performance cycle in one place from KPI setup and target assignment to realization submission, supervisor verification, performance analysis, and reporting. It combines a structured approval workflow with audit trails and optional AI-assisted insights to support more consistent, transparent, and data-informed performance management.

> **Project status:** Development project. Review the security notes and complete environment-specific testing before using real employee data or deploying the application to production.

## Contents

- [Highlights](#highlights)
- [How the workflow works](#how-the-workflow-works)
- [Technology stack](#technology-stack)
- [Project structure](#project-structure)
- [Requirements](#requirements)
- [Run locally](#run-locally)
- [Run with Docker Compose](#run-with-docker-compose)
- [Environment configuration](#environment-configuration)
- [Tests](#tests)
- [Security and privacy](#security-and-privacy)
- [Known limitations](#known-limitations)
- [Contributing](#contributing)
- [License](#license)

## Highlights

### KPI and performance-cycle management
- Maintain KPI categories, templates, targets, weights, and employee assignments.
- Manage performance cycles through defined lifecycle states.
- Track employee KPI realizations and supporting evidence.

### Verification and accountability
- Support submission, review, approval, rejection, revision, and authorized adjustments.
- Apply role-based access controls for administrators/HR, supervisors, and employees.
- Record important actions through an audit trail.

### Consistent KPI calculations
- Calculate KPI results using a dedicated calculation engine and decimal arithmetic.
- Support `Higher`, `Lower`, `Exact`, and `Boolean` KPI measurement types.
- Keep calculation logic separate from the web interface and workflow services.

### Analytics and reporting
- Provide role-oriented dashboards and performance summaries.
- Generate reports in PDF, CSV, and Excel formats.
- Support application notifications and optional email configuration.

### Optional AI-assisted features
- Generate performance insights and early warnings.
- Provide prediction and chat capabilities where configured.
- Keep a rules-based fallback available for supported functions when an external LLM is not configured.
- Allow an LLM provider to be disabled; the default configuration is `LLM_PROVIDER=none`.

### API and maintainability
- Expose REST API routes under `/api/v1`.
- Use database migrations to manage schema changes.
- Include automated tests for KPI calculations, workflow behavior, AI paths, and related application logic.

## How the workflow works

A typical performance cycle follows this general sequence:

1. **Configure** KPI categories, templates, targets, weights, and assignments.
2. **Publish** the performance cycle for the relevant employees.
3. **Record** KPI realizations and attach supporting evidence.
4. **Verify** submissions through the authorized supervisor or HR workflow.
5. **Calculate and review** results using the KPI calculation engine and analytics.
6. **Report and improve** using summaries, exports, and—when enabled—AI-assisted insights.

The exact actions available to each user depend on their assigned role and the cycle's current state.

## Technology stack

| Component | Technology |
|---|---|
| Application framework | Python, Flask |
| Authentication and forms | Flask-Login, Flask-WTF |
| ORM and schema migrations | Flask-SQLAlchemy, SQLAlchemy, Flask-Migrate, Alembic |
| Development database | SQLite |
| Container deployment option | Docker, Docker Compose |
| Database option for containers | PostgreSQL 16 |
| Analytics and machine learning | pandas, NumPy, scikit-learn |
| Spreadsheet and PDF exports | openpyxl, ReportLab |
| Production WSGI server dependency | Gunicorn |
| Testing | pytest, pytest-cov |

## Project structure

```text
kpi360/
├── app/
│   ├── ai/              # AI insights, prediction, early warning, and LLM client
│   ├── analytics/       # Performance analytics and queries
│   ├── blueprints/      # Web routes and REST API
│   ├── kpi_engine/      # KPI calculation logic
│   ├── security/        # Access control, audit, rate limiting, upload validation
│   ├── services/        # Workflow, reporting, email, and background jobs
│   ├── static/          # CSS and JavaScript assets
│   ├── templates/       # HTML templates
│   ├── config.py        # Application configuration
│   └── models.py        # Database models
├── migrations/          # Alembic database migrations
├── scripts/             # Development and demo-data scripts
├── tests/               # Automated tests
├── .env.example         # Environment-variable template
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── run.py
└── wsgi.py
```

## Requirements

For local development:

- Python 3.12 recommended
- `pip` and Python virtual-environment support
- Git (optional for local execution, required for Git-based contribution)

For Docker-based execution:

- Docker Engine or Docker Desktop
- Docker Compose v2

Use the Python version supported by the project's dependencies and verify the setup in a clean environment before deployment.

## Run locally

### 1. Get the source code

Clone your repository after pushing the project to GitHub:

```bash
git clone https://github.com/adiprabujayanegara01/kpi360.git
cd kpi360
```

### 2. Create and activate a virtual environment

**Windows PowerShell**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

**Linux/macOS**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Configure environment variables

Copy `.env.example` to `.env`.

**Windows PowerShell**

```powershell
Copy-Item .env.example .env
```

**Linux/macOS**

```bash
cp .env.example .env
```

Open `.env` and replace the placeholder `SECRET_KEY` with a strong, randomly generated secret. Keep `.env` local and never commit it to Git.

### 5. Initialize demo data (optional)

For local evaluation with fictitious data, use the project's demo-data script:

```bash
python scripts/seed_data.py
```

Only run seed scripts against a local development database that contains no real employee or organizational data. Review the script before running it, especially if the database already contains information.

### 6. Start the application

```bash
python run.py
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000) in your browser.

The repository also includes `start.bat` for Windows and `start.sh` for Linux/macOS. Review these scripts before use and ensure the environment is configured correctly.

## Run with Docker Compose

Docker Compose starts the web application and PostgreSQL services.

1. Create a local `.env` file from `.env.example`.
2. Set a strong, unique `SECRET_KEY`.
3. Set a strong, unique `POSTGRES_PASSWORD` in your local environment or `.env`.
4. For a non-demo environment, set `SEED_DEMO=0` and establish a secure administrator bootstrap process.
5. Review the Docker configuration, then start the services:

   ```bash
   docker compose up --build
   ```

6. Open [http://localhost:8000](http://localhost:8000).

Stop the services with:

```bash
docker compose down
```

Persistent data is stored in Docker volumes. Removing volumes can permanently delete database or application data, so understand the impact before using any volume-removal command.

> **Important:** the current Compose file contains a fallback PostgreSQL password for convenience. Do not use that fallback in a real environment. Replace it with a strong secret and update the Compose configuration to fail safely when the secret is missing before deploying.

## Environment configuration

The project reads settings from environment variables, typically provided through `.env` for local development.

| Variable | Purpose | Guidance |
|---|---|---|
| `SECRET_KEY` | Flask session and security key | Use a strong, unique secret; never commit it. |
| `DATABASE_URL` | Database connection string | SQLite is used by default; validate PostgreSQL configuration separately. |
| `SESSION_COOKIE_SECURE` | Restricts session cookies to HTTPS | Set to `1` when served over HTTPS. |
| `REGISTRATION_ENABLED` | Enables public account registration | Keep disabled unless registration is explicitly required. |
| `LLM_PROVIDER` | AI provider selection | Defaults to `none`; configure an external provider only after privacy review. |
| `LLM_MODEL` | Model identifier for the selected provider | Verify that the selected provider supports the configured model. |
| `ANTHROPIC_API_KEY` | Anthropic API credential | Required only if the Anthropic provider is used. |
| `OPENAI_BASE_URL` | OpenAI-compatible API endpoint | May point to a compatible local or hosted endpoint. |
| `OPENAI_API_KEY` | Credential for the configured compatible endpoint | Supply only when required by that endpoint. |
| `LLM_ANONYMIZE` | Enables ID substitution before LLM requests | Helpful but does not guarantee full anonymization. |
| `LLM_TIMEOUT` | LLM request timeout | Tune according to the selected provider and environment. |
| `MAIL_SERVER`, `MAIL_PORT` | Optional email server configuration | Configure only if email delivery is required. |
| `MAIL_USERNAME`, `MAIL_PASSWORD` | Optional email credentials | Store securely and never commit secrets. |
| `POSTGRES_PASSWORD` | PostgreSQL password used by Compose | Set a strong value; do not rely on the current default fallback. |
| `SEED_DEMO` | Controls demo-data seeding in Compose | Use `0` for non-demo environments. |

Check `.env.example`, `app/config.py`, and `docker-compose.yml` for the exact behavior of each setting. Available options and defaults may change as the project evolves.

## Tests

Install the project dependencies in the active virtual environment, then run:

```bash
pytest -q
```

To run tests with coverage:

```bash
pytest --cov=app --cov-report=term-missing
```

A passing test suite is useful evidence, but it does not establish that the application is secure or production-ready. Test the intended database, migrations, integrations, deployment configuration, and user roles in the target environment.

## Security and privacy

KPI360 includes security-related components such as password hashing, CSRF support, role-based access control, audit logging, request limits, upload validation, and security headers. Their presence does not by itself guarantee that every deployment is secure.

Before using the application with real employee data:

- Replace development secrets and remove fixed/default passwords from deployment configuration.
- Disable or remove demo accounts and demo data.
- Enforce HTTPS and secure session-cookie settings.
- Review authorization checks on all web and API routes, including object-level access.
- Review file-upload processing, report exports, CSV formula injection risks, rate limiting, and audit-log handling.
- Use dependency and secret scanning; review the entire Git history before making the repository public.
- Keep database backups protected and test restoration procedures.
- Assess privacy, legal basis, notices, retention, and provider terms before sending any employee-related information to an external AI service.
- Obtain the necessary authorization before publishing code or configuration originating from an organization.

See [`SECURITY_REVIEW.md`](SECURITY_REVIEW.md) for the initial repository review and recommended remediation steps.

## Known limitations

- External LLM integrations must be tested with the actual provider and credentials; fallback behavior does not prove that a live integration works.
- Docker Compose and PostgreSQL migrations should be validated in the intended deployment environment.
- In-memory rate limiting does not share counters across multiple application workers or instances.
- Thread-based background jobs are not a substitute for a durable job queue in a production workload.
- Demo credentials embedded in source code must be treated as public and must never be reused for real accounts.
- AI-generated insights should support, not replace, accountable human review of employee performance.

## Contributing

Contributions, bug reports, and improvement proposals are welcome when the repository owner has enabled them.

Before submitting a change:

1. Create a branch for your work.
2. Keep changes focused and document behavior that affects users or configuration.
3. Add or update tests where appropriate.
4. Run the test suite.
5. Never include secrets, personal data, production database files, or confidential screenshots in commits or issues.

## License

No license has been specified yet. Until the repository owner adds a license, do not assume that the code may be reused, redistributed, or modified by others. Choose and add an appropriate `LICENSE` file only after confirming the project's ownership and distribution rights.
