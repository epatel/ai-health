"""SQLite scan history at `<repo>/.aihealth/health.db`."""

from aihealth.store.migrations import MIGRATIONS, SchemaTooNewError, migrate, schema_version
from aihealth.store.scans import (
    GitInfo,
    connect,
    default_db_path,
    file_trend,
    get_file,
    get_scan,
    git_info,
    latest_scan_id,
    list_scans,
    project_trend,
    save_scan,
    smell_summary,
)

__all__ = [
    "MIGRATIONS",
    "GitInfo",
    "SchemaTooNewError",
    "connect",
    "default_db_path",
    "file_trend",
    "get_file",
    "get_scan",
    "git_info",
    "latest_scan_id",
    "list_scans",
    "migrate",
    "project_trend",
    "save_scan",
    "schema_version",
    "smell_summary",
]
