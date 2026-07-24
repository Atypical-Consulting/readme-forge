import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import readme_forge as rf


@pytest.fixture
def cfg(tmp_path):
    c = dict(rf.DEFAULTS)
    c["workdir"] = str(tmp_path)
    return c


@pytest.fixture
def rec():
    """A scan record for a repo missing everything the sweeps can fix."""
    return {
        "owner": "acme", "name": "widget", "fork": False, "archived": False,
        "empty": False, "lang": "C#", "default_branch": "main",
        "license_key": "mit", "created_at": "2020-01-01T00:00:00Z", "topics": [],
        "badges": 0, "toc": False, "tech_stack": False, "install": False,
        "usage": False, "getting_started": False, "roadmap": False,
        "contributing": False, "license_sec": False, "banner_logo": False,
        "features_sec": False, "code_blocks": 0,
    }


@pytest.fixture
def bare_readme():
    return "# Widget\n\nA small thing that does a job.\n"
