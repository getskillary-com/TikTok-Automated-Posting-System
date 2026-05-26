# Workflow Database

This directory contains workflow runner code, JSON schemas, and local workflow
profile conventions for Android/TikTok automation.

Live device profiles are intentionally not stored in Git. Files under
`workflow_database/devices/` and `workflow_database/index.json` can contain
operational metadata such as phone identifiers, ADB serials, calibrated
coordinates, and account-specific verification notes.

For a deployment machine, create local profiles under `workflow_database/devices/`
and register them in `workflow_database/index.json`. Keep those files backed up
through a private operational backup process, not a public source repository.

Tracked code in this directory:

- `scripts/run_showcase_workflow.py`: TikTok app showcase publishing runner.
- `scripts/run_marketing_workflow.py`: TikTok Studio marketing publishing runner.
- `scripts/marketing_calibration*.py`: calibration helpers for new devices.
- `schemas/*.json`: profile/schema contracts used by the workflow database.

Operational rule: profiles should be calibrated on one device first, then copied
or scaled only after the workflow has reached a stable single-device baseline.
