"""Coverage for api()'s `-f` field encoding, in particular the list -> array
field fix: `gh api -f key=value` always sends a plain string, and GitHub's REST
API rejects that for array-typed properties (e.g. `labels` on issue creation
returns HTTP 422 "... is not an array" -- confirmed empirically against a real
repository). `gh api --help` documents the fix: repeat `-f key[]=item` once per
array element.
"""
import readme_forge as rf


class FakeResult:
    def __init__(self, returncode=0, stdout="{}", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _capture(monkeypatch):
    captured = {}

    def fake_sh(cmd):
        captured["cmd"] = cmd
        return FakeResult()

    monkeypatch.setattr(rf, "sh", fake_sh)
    return captured


def test_api_sends_scalar_fields_as_plain_raw_fields(monkeypatch):
    captured = _capture(monkeypatch)
    rf.api("repos/acme/widget/issues", "POST", {"title": "hi", "body": "there"})
    cmd = captured["cmd"]
    assert "-f" in cmd
    assert "title=hi" in cmd
    assert "body=there" in cmd
    # no bracket syntax leaks in for a plain scalar
    assert not any("[]" in a for a in cmd)


def test_api_sends_a_list_field_as_one_bracketed_raw_field_per_item(monkeypatch):
    captured = _capture(monkeypatch)
    rf.api("repos/acme/widget/issues", "POST", {"labels": ["forge-report"]})
    cmd = captured["cmd"]
    assert "labels[]=forge-report" in cmd
    assert "labels=forge-report" not in cmd
    assert cmd.count("-f") == 1


def test_api_sends_a_multi_item_list_as_one_bracketed_field_per_element(monkeypatch):
    captured = _capture(monkeypatch)
    rf.api("repos/acme/widget/issues", "POST", {"labels": ["a", "b"]})
    cmd = captured["cmd"]
    assert "labels[]=a" in cmd
    assert "labels[]=b" in cmd
    assert cmd.count("-f") == 2


def test_api_sends_an_empty_list_as_a_bare_bracketed_field(monkeypatch):
    """Per `gh api --help`: "To pass an empty array, use `key[]` without a
    value."""
    captured = _capture(monkeypatch)
    rf.api("repos/acme/widget/issues", "POST", {"labels": []})
    cmd = captured["cmd"]
    assert "labels[]" in cmd
    assert not any(a.startswith("labels[]=") for a in cmd)


def test_api_mixes_scalar_and_list_fields_in_one_call(monkeypatch):
    captured = _capture(monkeypatch)
    rf.api("repos/acme/widget/issues", "POST",
           {"title": "hi", "labels": ["forge-report"]})
    cmd = captured["cmd"]
    assert "title=hi" in cmd
    assert "labels[]=forge-report" in cmd
