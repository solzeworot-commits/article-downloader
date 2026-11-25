"""Wiley DOI prefixes shared by the desktop UI and downloader.

These select the browser for resolution. The landing site decides which
publisher handles the download because journals can change publishers.
"""
DOI_PREFIXES = ("10.1002", "10.1111")


def is_wiley_doi(doi: str | None) -> bool:
    return bool(doi and doi.partition("/")[0] in DOI_PREFIXES)
