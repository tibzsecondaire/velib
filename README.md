# velib

Analyse des données Vélib' Métropole.

## Data source

[Vélib' Métropole GBFS open data](https://www.velib-metropole.fr/donnees-open-data-gbfs-du-service-velib-metropole).
The auto-discovery feed
<https://velib-metropole-opendata.smovengo.cloud/opendata/Velib_Metropole/gbfs.json>
lists the available feeds:

- `system_information`: system metadata.
- `station_information`: static station data (location, capacity).
- `station_status`: real-time bike and dock availability per station.

## Getting started

### Prerequisites

- Python 3.12
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
- `pre-commit` installed as a uv tool:
  ```bash
  uv tool install pre-commit
  ```

### Setup

```bash
git clone <repo-url>
cd velib
uv sync --locked --all-groups
source .venv/bin/activate
pre-commit install -t pre-commit -t commit-msg
cp .env.example .env     # then fill in real values
```

### Running tests

```bash
make test                # full suite with coverage
make test-unit           # unit tests only
make test-integration    # integration tests only
```

### Common commands

| Command            | What it does                                |
| ------------------ | ------------------------------------------- |
| `make install`     | `uv sync --locked --all-groups`             |
| `make lock`        | Regenerate `uv.lock`                        |
| `make lint`        | Run all pre-commit hooks                    |
| `make format`      | Ruff format + autofix                       |
| `uv add <pkg>`     | Add a runtime dependency                    |
| `uv add --group dev <pkg>`  | Add a dev-only dependency          |

## Project structure

```
velib/
├── src/velib/              # package code
├── tests/
│   ├── unit/
│   └── integration/
├── conf/                   # hydra configs (populated later)
├── docs/                   # mkdocs sources
├── notebooks/              # exploratory notebooks
├── scripts/                # utility scripts
└── pyproject.toml
```

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md).
