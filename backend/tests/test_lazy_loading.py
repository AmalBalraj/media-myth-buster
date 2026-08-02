"""Guards against MissingGreenlet.

Under asyncio an implicit lazy load raises
`MissingGreenlet: greenlet_spawn has not been called` — but only at runtime, and
only once real IO is attempted, so it ships looking like working code. It took
down the ingest stage in production.

Two defences, both asserted here: the relationships the pipeline touches are
mapped `raise_on_sql` so an implicit load fails loudly everywhere, and the runner
is checked not to reach through them at all.

(The schema uses Postgres ARRAY/JSONB/vector columns that SQLite cannot render,
so these assert the mapper configuration rather than spinning up a database.)
"""

import inspect

import pytest
from sqlalchemy import inspect as sa_inspect

from app.models import Media, Report

PIPELINE_RELATIONSHIPS = [(Media, "creator"), (Report, "media")]


@pytest.mark.parametrize("model,attr", PIPELINE_RELATIONSHIPS)
def test_pipeline_relationships_refuse_implicit_loads(model, attr):
    rel = sa_inspect(model).relationships[attr]
    assert rel.lazy == "raise_on_sql", (
        f"{model.__name__}.{attr} would lazy-load. In async code that is a "
        "MissingGreenlet crash at runtime; keep it raise_on_sql and eager-load "
        "or use the foreign key column."
    )


@pytest.mark.parametrize("model,attr", PIPELINE_RELATIONSHIPS)
def test_raise_on_sql_still_permits_eager_loading(model, attr):
    """The report API depends on selectinload continuing to work."""
    rel = sa_inspect(model).relationships[attr]
    assert rel.lazy != "noload"
    assert not rel.viewonly


def test_runner_does_not_reach_through_those_relationships():
    """The runner must use the Creator object it already has and the media path
    it captured — not media.creator or report.media."""
    from app.pipeline import runner

    body = "\n".join(
        line
        for line in inspect.getsource(runner).splitlines()
        if not line.strip().startswith("#")
    )
    assert "media.creator." not in body, "reaches through Media.creator"
    assert "report.media." not in body, "reaches through Report.media"


def test_report_api_eager_loads_everything_it_renders():
    """The read path renders media, creator, claims and evidence, so all four
    must be in the query's options or serialisation will explode."""
    from app.api import routes_reports

    src = inspect.getsource(routes_reports.get_report)
    for target in ("Report.media", "Media.creator", "Report.claims", "Claim.evidence"):
        assert target in src, f"{target} is rendered but never eager-loaded"
