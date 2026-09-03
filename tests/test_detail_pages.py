"""A task and a community are pages, not dialogs.

They were dialogs over the dashboard. The problem was not that a dialog looked
wrong -- it was that a dialog has no address. You cannot link to it, cannot
return to it, cannot go back from it, and on a phone it covers a page you can
no longer see. Everything below is about the address existing.
"""
from tests.test_work_mode import _community, _members, _join, _user, owner, rahul  # noqa: F401


def _task(client, community_id, assignee_ids, title="Write the lab manual"):
    r = client.post(
        f"/api/work/communities/{community_id}/tasks",
        json={"title": title, "assignee_ids": assignee_ids},
    )
    assert r.status_code == 201, r.text
    return r.json()


# --------------------------------------------------------------------------
# The pages exist and are their own URL
# --------------------------------------------------------------------------
def test_a_task_has_its_own_page(owner, rahul):
    c = _community(owner)
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    ids = _members(owner, c["id"])
    t = _task(owner, c["id"], [ids["Rahul"]])

    page = owner.get(f"/work/task/{t['id']}")
    assert page.status_code == 200
    body = page.text
    # The shell the renderer fills, and the id it reads to know what to fetch.
    assert 'id="wk-taskdetail-body"' in body
    assert f'data-detail-id="{t["id"]}"' in body
    assert 'data-detail-kind="task"' in body


def test_a_community_has_its_own_page(owner):
    c = _community(owner)
    page = owner.get(f"/work/community/{c['id']}")
    assert page.status_code == 200
    assert 'id="wk-detail-body"' in page.text
    assert f'data-detail-id="{c["id"]}"' in page.text
    assert 'data-detail-kind="community"' in page.text


def test_every_detail_page_offers_a_way_back(owner):
    """The whole point of a page over a dialog. A dialog has a close button
    because there is something behind it; a page needs to say where back is."""
    c = _community(owner)
    page = owner.get(f"/work/community/{c['id']}")
    assert 'id="wk-back"' in page.text


# --------------------------------------------------------------------------
# The dashboard links to them rather than opening them over itself
# --------------------------------------------------------------------------
def test_the_dashboard_no_longer_carries_the_detail_dialogs(owner):
    """A regression guard with teeth: if these come back, something has been
    reverted, and the two renderers would then have two containers to fill."""
    page = owner.get("/work").text
    assert 'id="wk-taskdetail-modal"' not in page
    assert 'id="wk-detail-modal"' not in page


def test_the_dialogs_work_still_needs_are_present(owner):
    """Not everything became a page. A confirmation and a short form are
    answered in one action and genuinely interrupt -- those stay dialogs.

    Each now sits on the page whose button opens it, which is why the create
    form is checked on /work/communities rather than on the overview: a dialog
    on a page with nothing to open it is markup nobody can reach.
    """
    overview = owner.get("/work").text
    for shared in ("wk-profile-modal", "wk-task-modal", "wk-delete-modal"):
        assert f'id="{shared}"' in overview, shared

    communities = owner.get("/work/communities").text
    assert 'id="wk-community-modal"' in communities
    assert 'id="wk-new-community"' in communities


# --------------------------------------------------------------------------
# Signed out, and pointed at by notifications
# --------------------------------------------------------------------------
def test_a_detail_page_is_not_served_to_a_stranger(client):
    r = client.get("/work/task/whatever", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/login"

    r = client.get("/work/community/whatever", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/login"


def test_an_unknown_id_still_renders_the_shell(owner):
    """The page is a shell; the API behind it is what checks access. Guessing
    an id gets an empty frame and a failed fetch, never someone else's task."""
    page = owner.get("/work/task/00000000-0000-0000-0000-000000000000")
    assert page.status_code == 200
    assert 'id="wk-taskdetail-body"' in page.text


def test_a_push_notification_links_to_the_task_itself(owner, rahul, db_session, monkeypatch):
    """This used to be /work?task=<id>, and nothing ever read that query
    parameter -- the notification opened the dashboard and dropped the id."""
    import json as _json

    from tests.test_work_mode import _fake_push, _register_device
    from app.services.work_notify import deliver_pending_pushes

    sent = _fake_push(monkeypatch)
    _register_device(rahul)

    c = _community(owner)
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    ids = _members(owner, c["id"])
    _task(owner, c["id"], [ids["Rahul"]], title="Mark the practicals")

    deliver_pending_pushes(db_session)
    assert sent, "the assignee's phone should have been pushed"

    url = _json.loads(sent[-1]["data"])["url"]
    assert url.startswith("/work/task/"), url
    # And that URL must be a page that actually exists.
    assert rahul.get(url).status_code == 200
