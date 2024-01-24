import yaml

from landscape.client import snap_http
from landscape.client.snap_http import SnapdHttpException


def parse_assertion(assertion: str):
    """Parse an assertion into key-value pairs."""
    headers, signature = assertion.split("\n\n")
    parsed = {}
    parsed.update(yaml.safe_load(headers))
    parsed["signature"] = signature
    return parsed


def get_assertions(assertion_type: str):
    """Get and parse assertions."""
    try:
        response = snap_http.get_assertions(assertion_type)
    except SnapdHttpException:
        return

    # the snapd API returns multiple assertions as a stream of
    # bytes separated by double newlines, something like this:
    # <assertion1-headers>: <value>
    # <assertion1-headers>: <value>
    #
    # signature
    #
    # <assertion2-headers>: <value>
    # <assertion2-headers>: <value>
    #
    # signature

    # extract the assertion headers + their signatures as separate assertions
    sections = response.result.decode().split("\n\n")
    raw_assertions = [
        "\n\n".join(sections[i : i + 2]) for i in range(0, len(sections), 2)
    ]
    return [
        parse_assertion(assertion)
        for assertion in raw_assertions
        if len(assertion) > 0
    ]
