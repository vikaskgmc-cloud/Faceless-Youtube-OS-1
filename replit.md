# Faceless YouTube OS

## Run and verify
- Workflow: `Start application`, command `python3 main.py`, port 5000.
- Python 3.11+ standard library only. External executable dependencies: ffmpeg/ffprobe, libx264, libass (present in this workspace).
- Tests: `python3 -m unittest discover -s tests -v`. These exercise fake providers, actual HD/4K renders, caption burning, music ducking, approvals, verification and HTTP behavior; no paid network calls.
- Start with `README.md` for operational instructions and `API.md` for dashboard routes.

## Product constraints
- The channel promise is “the world behind the headlines.”
- NEVER add uploading or publishing, including automatic YouTube APIs. A human uploads manually and records a URL.
- Human gates must remain code-enforced. Autopilot cannot confirm scores, approve, select topics, edit existing claims or publish.
- Keep Python's standard-library pipeline and readable per-project JSON folders. No database migration or JS workspace restructure.
- Keep secrets in the environment only. Provider error responses must never expose credentials or exception bodies. Prompt files are editable under prompts/.
- This dashboard is a private, single-operator workspace tool, not a public multi-user service. No authentication is currently installed. Do not recommend public deployment before access control and durable media storage are added.
- Media directories, uploads and generated test output must remain outside Git. Do not seed fictional test projects into the real dashboard.

## Provider access
Live adapters use direct HTTP and the environment variable names documented in README. Existing account connections do not automatically populate those variables. Missing providers must fail explicitly; fake providers exist only in tests.
