"""Legacy report cleanup; FlowBusters has no built-in product requirements."""
from pathlib import Path
import re

RUN_ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$')


class BusinessRequirementError(ValueError):
    pass


def load_business_requirements(*args, **kwargs):
    return []


def trusted_report_run_id(report_path):
    path = Path(report_path).resolve(strict=False)
    run_dir = path.parent.parent.parent
    if (path.name != 'findings.json' or path.parent.parent.name != 'reports'
            or run_dir.parent.name != 'runs' or path.parent.name != run_dir.name
            or not RUN_ID_RE.fullmatch(run_dir.name)):
        return None
    return run_dir.name


def apply_backend_requirements(report, *args, **kwargs):
    """Discard old or AI-supplied claims of approved product rules."""
    if isinstance(report, dict):
        report.pop('application_identity', None)
        for collection in ('findings', 'results'):
            for record in report.get(collection, []) or []:
                if isinstance(record, dict):
                    record.pop('_backend_requirement_valid', None)
                    record.pop('backend_requirement', None)
    return report
