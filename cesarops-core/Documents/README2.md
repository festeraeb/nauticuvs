# CESAROps Sandbox

This is a clean, isolated workspace for the CESAROps project. All code, data, and configuration here are specific to CESAROps and do not depend on or include any other projects (wreckhunter2000, sonarsniffer, nauticuvs, etc.).

## Structure
- `src/cesarops/` — Main package code
- `tests/` — Unit and integration tests
- `data/`, `logs/`, `models/`, `outputs/` — Data and output folders
- `reference/` — Reference scripts and legacy code

## Usage
- Develop and run CESAROps here without cross-project contamination.
- If you need to use external tools (e.g., nauticuvs), install them from official sources (e.g., crates.io), not from local code.

## Archiving
Once you confirm this workspace is clean, archive or delete the old mixed codebase as needed.
