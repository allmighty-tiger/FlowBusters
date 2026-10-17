"""Compatibility helpers for old reports.

Live assessments do not identify products or select product-specific rules.
Saved identity files are retained on disk but confer no authority.
"""


class ApplicationIdentityError(ValueError):
    pass


def load_applications(*args, **kwargs):
    return {}


def issue_identity(*args, **kwargs):
    return None


def validate_identity(*args, **kwargs):
    return None


async def discover_application_identity(*args, **kwargs):
    return None
