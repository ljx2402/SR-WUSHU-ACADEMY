"""Helpers for admin tampering tests: start from the real rendered admin form,
then change whatever an attacker would change before POSTing it back."""

from html.parser import HTMLParser


class _FormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.data = {}
        self._select = None
        self._textarea = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        name = attrs.get("name")
        if tag == "input" and name:
            kind = attrs.get("type", "text")
            if kind in ("submit", "button", "image", "file"):
                return
            if kind in ("checkbox", "radio") and "checked" not in attrs:
                return
            self.data[name] = attrs.get("value", "on" if kind == "checkbox" else "")
        elif tag == "select" and name:
            self._select = name
            self.data.setdefault(name, "")
        elif tag == "option" and self._select and "selected" in attrs:
            self.data[self._select] = attrs.get("value", "")
        elif tag == "textarea" and name:
            self._textarea = name
            self.data[name] = ""

    def handle_endtag(self, tag):
        if tag == "select":
            self._select = None
        elif tag == "textarea":
            self._textarea = None

    def handle_data(self, data):
        if self._textarea:
            self.data[self._textarea] += data


def form_data(response):
    """All submittable values of the admin form on a rendered page."""
    parser = _FormParser()
    parser.feed(response.content.decode())
    parser.data.pop("csrfmiddlewaretoken", None)
    return parser.data
