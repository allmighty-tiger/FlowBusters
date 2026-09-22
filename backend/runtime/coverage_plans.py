"""Read generation labels from old run records; no built-in test plans."""


def receipt_probe_origin(receipt):
    source = receipt.get('source')
    if source in ('BACKEND_COVERAGE', 'VERIFICATION_PROBE'):
        return source
    # Older AI and template scripts shared this label; do not invent authorship.
    return 'UNKNOWN' if source == 'MUTATION_SCRIPT' else 'UNKNOWN'


def prepare_coverage_probes(*args, **kwargs):
    return []
