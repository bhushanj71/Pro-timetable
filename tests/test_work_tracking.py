"""Member task tracking: attachments, the owner's overview, and history.

The permission tests are the ones that matter most here. A member's own work is
theirs to see; another member's is a supervisor's question. Everything else in
this module is arithmetic, and arithmetic that leaks is still a leak.
"""
import io

import pytest

from tests.test_work_mode import _community, _join, _members, _user, owner, rahul, amit  # noqa: F401

PDF = b"%PDF-1.4\n%probe\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def _task(client, community_id, assignee_ids, **over):
    body = {"title": "Prepare Event Documentation", "assignee_ids": assignee_ids}
    body.update(over)
    r = client.post(f"/api/work/communities/{community_id}/tasks", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _upload(client, task_id, name="Event_Report.pdf", data=PDF, ctype="application/pdf"):
    return client.post(
        f"/api/work/tasks/{task_id}/attachments",
        files={"file": (name, io.BytesIO(data), ctype)},
    )


@pytest.fixture
def team(owner, rahul):
    c = _community(owner)
    _join(owner, rahul, c["id"], "w_rahul@example.com")
    ids = _members(owner, c["id"])
    return {"community": c["id"], "ids": ids, "owner": owner, "rahul": rahul}


# --------------------------------------------------------------------------
# Attachments
# --------------------------------------------------------------------------
def test_an_assignee_can_attach_evidence(team):
    t = _task(team["owner"], team["community"], [team["ids"]["Rahul"]])
    team["rahul"].post(f"/api/work/tasks/{t['id']}/respond", json={"accept": True})

    r = _upload(team["rahul"], t["id"])
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["file_name"] == "Event_Report.pdf"
    assert body["size_bytes"] == len(PDF)
    assert body["uploaded_by"]["name"] == "Rahul"
    assert body["icon"] == "📄"


def test_the_owner_can_attach_a_brief(team):
    t = _task(team["owner"], team["community"], [team["ids"]["Rahul"]])
    assert _upload(team["owner"], t["id"], "Event_Poster.png", PNG, "image/png").status_code == 201


def test_someone_with_no_stake_in_the_task_cannot_attach(team, amit):
    """A community member who is neither assigned nor managing. A task is not
    a noticeboard."""
    _join(team["owner"], amit, team["community"], "w_amit@example.com")
    t = _task(team["owner"], team["community"], [team["ids"]["Rahul"]])
    assert _upload(amit, t["id"]).status_code == 403


def test_someone_who_declined_the_work_can_no_longer_attach_to_it(team):
    """A declined assignment is still a row on the task, so an "are they
    assigned" check let somebody who had turned the work down keep uploading
    to it. Declining is neither doing the work nor managing it."""
    t = _task(team["owner"], team["community"], [team["ids"]["Rahul"]])
    assert _upload(team["rahul"], t["id"]).status_code == 201     # still theirs

    team["rahul"].post(f"/api/work/tasks/{t['id']}/respond",
                       json={"accept": False, "reason": "No capacity"})
    assert _upload(team["rahul"], t["id"], "after.txt", b"x", "text/plain").status_code == 403


def test_a_stranger_to_the_community_cannot_even_see_the_files(team):
    """404, not 403, and deliberately so: telling someone a community exists
    but is closed to them is itself a disclosure. The rest of Work already
    answers this way and attachments must not be the one place that differs."""
    outsider = _user("w_outsider@example.com", "Outsider")
    t = _task(team["owner"], team["community"], [team["ids"]["Rahul"]])
    _upload(team["owner"], t["id"])
    assert outsider.get(f"/api/work/tasks/{t['id']}/attachments").status_code == 404


def test_a_file_comes_back_with_its_own_name_and_type(team):
    t = _task(team["owner"], team["community"], [team["ids"]["Rahul"]])
    a = _upload(team["owner"], t["id"]).json()

    r = team["owner"].get(f"/api/work/attachments/{a['id']}")
    assert r.status_code == 200
    assert r.content == PDF
    assert r.headers["content-type"].startswith("application/pdf")
    assert "Event_Report.pdf" in r.headers["content-disposition"]


def test_download_forces_an_attachment_disposition(team):
    t = _task(team["owner"], team["community"], [team["ids"]["Rahul"]])
    a = _upload(team["owner"], t["id"]).json()
    r = team["owner"].get(f"/api/work/attachments/{a['id']}?download=true")
    assert r.headers["content-disposition"].startswith("attachment")


@pytest.mark.parametrize("name,ctype", [
    ("notes.exe", "application/octet-stream"),
    ("script.svg", "image/svg+xml"),
    ("payload.html", "text/html"),
    ("noextension", "application/pdf"),
])
def test_unaccepted_file_types_are_refused(team, name, ctype):
    """An allow-list, not a deny-list. A deny-list is a list of the attacks
    somebody happened to think of."""
    t = _task(team["owner"], team["community"], [team["ids"]["Rahul"]])
    r = _upload(team["owner"], t["id"], name, b"x" * 32, ctype)
    assert r.status_code == 400, r.text


def test_a_type_that_disagrees_with_its_extension_is_refused(team):
    """The browser's content type is a claim. A .pdf arriving as something
    executable is refused rather than guessed at."""
    t = _task(team["owner"], team["community"], [team["ids"]["Rahul"]])
    r = _upload(team["owner"], t["id"], "report.pdf", b"MZ", "application/x-msdownload")
    assert r.status_code == 400


def test_an_empty_file_is_refused(team):
    t = _task(team["owner"], team["community"], [team["ids"]["Rahul"]])
    assert _upload(team["owner"], t["id"], "empty.txt", b"", "text/plain").status_code == 400


def test_an_oversized_file_is_refused_with_its_size_named(team):
    from app.services.attachments import MAX_BYTES

    t = _task(team["owner"], team["community"], [team["ids"]["Rahul"]])
    r = _upload(team["owner"], t["id"], "big.zip", b"0" * (MAX_BYTES + 1), "application/zip")
    assert r.status_code == 413
    assert "MB" in r.json()["detail"]


def test_a_path_in_the_filename_is_stripped(team):
    """The name is attacker-controlled text that ends up in a header. It is
    never used to open anything, but it should not travel intact either."""
    t = _task(team["owner"], team["community"], [team["ids"]["Rahul"]])
    a = _upload(team["owner"], t["id"], "../../etc/passwd.txt", b"hello", "text/plain").json()
    assert a["file_name"] == "passwd.txt"


def test_only_dangerous_types_are_forced_to_download(team):
    t = _task(team["owner"], team["community"], [team["ids"]["Rahul"]])
    pdf = _upload(team["owner"], t["id"], "a.pdf", PDF, "application/pdf").json()
    zipped = _upload(team["owner"], t["id"], "a.zip", b"PK\x03\x04zip", "application/zip").json()
    assert pdf["can_view_inline"] is True
    assert zipped["can_view_inline"] is False


def test_a_member_can_remove_their_own_file_but_not_the_owners(team):
    t = _task(team["owner"], team["community"], [team["ids"]["Rahul"]])
    team["rahul"].post(f"/api/work/tasks/{t['id']}/respond", json={"accept": True})

    mine = _upload(team["rahul"], t["id"], "mine.txt", b"mine", "text/plain").json()
    theirs = _upload(team["owner"], t["id"], "brief.txt", b"brief", "text/plain").json()

    assert team["rahul"].delete(f"/api/work/attachments/{mine['id']}").status_code == 204
    assert team["rahul"].delete(f"/api/work/attachments/{theirs['id']}").status_code == 403


def test_deleting_a_task_takes_its_files_with_it(team, db_session):
    from app.models import TaskAttachment

    t = _task(team["owner"], team["community"], [team["ids"]["Rahul"]])
    _upload(team["owner"], t["id"])
    assert db_session.query(TaskAttachment).filter_by(task_id=t["id"]).count() == 1

    team["owner"].delete(f"/api/work/tasks/{t['id']}")
    assert db_session.query(TaskAttachment).filter_by(task_id=t["id"]).count() == 0


def test_attachments_appear_on_the_task_itself(team):
    t = _task(team["owner"], team["community"], [team["ids"]["Rahul"]])
    _upload(team["owner"], t["id"])
    detail = team["owner"].get(f"/api/work/tasks/{t['id']}").json()
    assert [a["file_name"] for a in detail["attachments"]] == ["Event_Report.pdf"]


# --------------------------------------------------------------------------
# The timeline
# --------------------------------------------------------------------------
def test_an_upload_is_recorded_in_the_timeline(team):
    """Evidence arriving is an event. Without it the owner sees a file with no
    idea when it turned up."""
    t = _task(team["owner"], team["community"], [team["ids"]["Rahul"]])
    team["rahul"].post(f"/api/work/tasks/{t['id']}/respond", json={"accept": True})
    _upload(team["rahul"], t["id"])

    timeline = team["owner"].get(f"/api/work/tasks/{t['id']}").json()["timeline"]
    uploads = [e for e in timeline if e["kind"] == "attachment"]
    assert uploads and "Event_Report.pdf" in uploads[0]["note"]
    assert uploads[0]["user"]["name"] == "Rahul"


def test_the_timeline_carries_progress_comments_and_files_together(team):
    t = _task(team["owner"], team["community"], [team["ids"]["Rahul"]])
    team["rahul"].post(f"/api/work/tasks/{t['id']}/respond", json={"accept": True})
    team["rahul"].put(f"/api/work/tasks/{t['id']}/progress", json={"progress": 75})
    team["rahul"].post(f"/api/work/tasks/{t['id']}/comments", json={"body": "Draft is in."})
    _upload(team["rahul"], t["id"])

    kinds = {e["kind"] for e in team["owner"].get(f"/api/work/tasks/{t['id']}").json()["timeline"]}
    assert {"progress", "comment", "attachment", "assigned"} <= kinds


def test_the_timeline_is_newest_first(team):
    t = _task(team["owner"], team["community"], [team["ids"]["Rahul"]])
    team["rahul"].post(f"/api/work/tasks/{t['id']}/respond", json={"accept": True})
    team["rahul"].put(f"/api/work/tasks/{t['id']}/progress", json={"progress": 40})
    stamps = [e["at"] for e in team["owner"].get(f"/api/work/tasks/{t['id']}").json()["timeline"]]
    assert stamps == sorted(stamps, reverse=True)


# --------------------------------------------------------------------------
# The owner's overview
# --------------------------------------------------------------------------
def test_the_overview_counts_the_communitys_workload(team):
    a = _task(team["owner"], team["community"], [team["ids"]["Rahul"]], title="One")
    _task(team["owner"], team["community"], [team["ids"]["Rahul"]], title="Two")
    team["rahul"].post(f"/api/work/tasks/{a['id']}/respond", json={"accept": True})
    team["rahul"].put(f"/api/work/tasks/{a['id']}/progress", json={"progress": 100})

    totals = team["owner"].get(f"/api/work/communities/{team['community']}/overview").json()["totals"]
    assert totals["members"] == 2
    assert totals["tasks"] == 2
    assert totals["completed"] == 1
    assert totals["pending"] == 1


def test_the_performance_table_has_a_row_per_member(team):
    t = _task(team["owner"], team["community"], [team["ids"]["Rahul"]])
    team["rahul"].post(f"/api/work/tasks/{t['id']}/respond", json={"accept": True})
    team["rahul"].put(f"/api/work/tasks/{t['id']}/progress", json={"progress": 100})

    rows = team["owner"].get(f"/api/work/communities/{team['community']}/overview").json()["performance"]
    rahul = next(r for r in rows if r["user"]["name"] == "Rahul")
    assert rahul["assigned"] == 1
    assert rahul["completed"] == 1
    assert rahul["completion"] == 100


def test_a_member_with_nothing_assigned_still_has_a_row(team):
    rows = team["owner"].get(f"/api/work/communities/{team['community']}/overview").json()["performance"]
    assert {r["user"]["name"] for r in rows} == {"Bhushan", "Rahul"}
    assert all(r["assigned"] == 0 and r["completion"] == 0 for r in rows)


def test_declining_does_not_flatter_the_completion_figure(team):
    """Completion is out of everything held. Dropping declines would let
    somebody reach 100% by turning work down."""
    a = _task(team["owner"], team["community"], [team["ids"]["Rahul"]], title="Kept")
    b = _task(team["owner"], team["community"], [team["ids"]["Rahul"]], title="Refused")
    team["rahul"].post(f"/api/work/tasks/{a['id']}/respond", json={"accept": True})
    team["rahul"].put(f"/api/work/tasks/{a['id']}/progress", json={"progress": 100})
    team["rahul"].post(f"/api/work/tasks/{b['id']}/respond", json={"accept": False})

    rows = team["owner"].get(f"/api/work/communities/{team['community']}/overview").json()["performance"]
    rahul = next(r for r in rows if r["user"]["name"] == "Rahul")
    assert rahul["incomplete"] == 1
    assert rahul["completion"] == 50


# --------------------------------------------------------------------------
# One member's history
# --------------------------------------------------------------------------
def _history(client, community, member_id, **params):
    return client.get(
        f"/api/work/communities/{community}/members/{member_id}/tasks", params=params
    )


def test_the_owner_can_read_a_members_history(team):
    t = _task(team["owner"], team["community"], [team["ids"]["Rahul"]])
    r = _history(team["owner"], team["community"], team["ids"]["Rahul"])
    assert r.status_code == 200
    body = r.json()
    assert body["member"]["name"] == "Rahul"
    assert [x["id"] for x in body["tasks"]] == [t["id"]]
    assert body["tasks"][0]["assigned_by"]["name"] == "Bhushan"


def test_a_member_can_read_their_own_history(team):
    _task(team["owner"], team["community"], [team["ids"]["Rahul"]])
    assert _history(team["rahul"], team["community"], team["ids"]["Rahul"]).status_code == 200


def test_a_colleague_sees_a_members_finished_work_in_full(team, amit):
    """Finished work is the team's record. A colleague can read all of it --
    the task, its progress, its files -- because that is how you find out how
    something was done last time."""
    _join(team["owner"], amit, team["community"], "w_amit@example.com")
    ids = _members(team["owner"], team["community"])

    done = _task(team["owner"], team["community"], [ids["Rahul"]], title="Finished")
    team["rahul"].post(f"/api/work/tasks/{done['id']}/respond", json={"accept": True})
    team["rahul"].put(f"/api/work/tasks/{done['id']}/progress", json={"progress": 100})
    _upload(team["rahul"], done["id"])

    r = _history(amit, team["community"], ids["Rahul"])
    assert r.status_code == 200
    body = r.json()
    assert body["scope"] == "completed"
    assert [t["title"] for t in body["tasks"]] == ["Finished"]
    assert body["tasks"][0]["attachment_count"] == 1

    # And the task itself opens in full, timeline and files included.
    detail = amit.get(f"/api/work/tasks/{done['id']}").json()
    assert detail["attachments"][0]["file_name"] == "Event_Report.pdf"
    assert any(e["kind"] == "progress" for e in detail["timeline"])


def test_a_colleague_does_not_see_work_still_in_flight(team, amit):
    """Reading somebody's half-finished task is reading over their shoulder.
    The counts are not secret -- they are on the board's member table, which
    everyone can read -- but the detail stays between them and their owner."""
    _join(team["owner"], amit, team["community"], "w_amit@example.com")
    ids = _members(team["owner"], team["community"])

    _task(team["owner"], team["community"], [ids["Rahul"]], title="Still going")
    body = _history(amit, team["community"], ids["Rahul"]).json()

    assert [t["title"] for t in body["tasks"]] == []
    # The tally still counts it, matching the table above it on the page.
    assert body["tally"]["assigned"] == 1
    assert body["tally"]["pending"] == 1


def test_an_owner_still_sees_everything(team, amit):
    _join(team["owner"], amit, team["community"], "w_amit@example.com")
    ids = _members(team["owner"], team["community"])
    _task(team["owner"], team["community"], [ids["Rahul"]], title="Still going")

    body = _history(team["owner"], team["community"], ids["Rahul"]).json()
    assert body["scope"] == "all"
    assert [t["title"] for t in body["tasks"]] == ["Still going"]


def test_a_member_still_sees_all_of_their_own_work(team):
    _task(team["owner"], team["community"], [team["ids"]["Rahul"]], title="Mine, unfinished")
    body = _history(team["rahul"], team["community"], team["ids"]["Rahul"]).json()
    assert body["scope"] == "all"
    assert [t["title"] for t in body["tasks"]] == ["Mine, unfinished"]


def test_history_can_be_filtered_by_status(team):
    a = _task(team["owner"], team["community"], [team["ids"]["Rahul"]], title="Done")
    _task(team["owner"], team["community"], [team["ids"]["Rahul"]], title="Untouched")
    team["rahul"].post(f"/api/work/tasks/{a['id']}/respond", json={"accept": True})
    team["rahul"].put(f"/api/work/tasks/{a['id']}/progress", json={"progress": 100})

    done = _history(team["owner"], team["community"], team["ids"]["Rahul"], status="completed").json()
    assert [t["title"] for t in done["tasks"]] == ["Done"]
    # The tally describes the whole picture, not the filtered view -- a
    # completion figure that moved when a filter moved would be meaningless.
    assert done["tally"]["assigned"] == 2


def test_history_can_be_filtered_by_period(team):
    _task(team["owner"], team["community"], [team["ids"]["Rahul"]])
    today = _history(team["owner"], team["community"], team["ids"]["Rahul"], period="today").json()
    assert len(today["tasks"]) == 1
    assert today["period"] == "Today"

    old = _history(team["owner"], team["community"], team["ids"]["Rahul"],
                   period="custom", start="2020-01-01", end="2020-12-31").json()
    assert old["tasks"] == []


def test_an_outsider_cannot_reach_the_history_at_all(team):
    """404 again: the community's existence is not confirmed to a stranger."""
    outsider = _user("w_outsider2@example.com", "Outsider")
    assert _history(outsider, team["community"], team["ids"]["Rahul"]).status_code == 404


def test_asking_for_someone_outside_the_community_is_a_404(team, amit):
    ids_before = amit.get("/api/auth/me").json()["id"]
    assert _history(team["owner"], team["community"], ids_before).status_code == 404


# --------------------------------------------------------------------------
# Overdue and incomplete, which are derived
# --------------------------------------------------------------------------
def test_a_task_past_its_date_is_overdue(team):
    past = "2020-01-01T10:00:00Z"
    _task(team["owner"], team["community"], [team["ids"]["Rahul"]], due_date=past)
    totals = team["owner"].get(f"/api/work/communities/{team['community']}/overview").json()["totals"]
    assert totals["overdue"] == 1


def test_work_finished_late_is_completed_not_overdue(team):
    """Late and done is done. Flagging it red forever helps nobody."""
    t = _task(team["owner"], team["community"], [team["ids"]["Rahul"]],
              due_date="2020-01-01T10:00:00Z")
    team["rahul"].post(f"/api/work/tasks/{t['id']}/respond", json={"accept": True})
    team["rahul"].put(f"/api/work/tasks/{t['id']}/progress", json={"progress": 100})

    totals = team["owner"].get(f"/api/work/communities/{team['community']}/overview").json()["totals"]
    assert totals["completed"] == 1
    assert totals["overdue"] == 0


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------
def test_search_finds_a_task_by_title(team):
    _task(team["owner"], team["community"], [team["ids"]["Rahul"]], title="Website Documentation")
    hits = team["owner"].get(f"/api/work/communities/{team['community']}/search",
                             params={"q": "website"}).json()["results"]
    assert [h["title"] for h in hits] == ["Website Documentation"]


def test_search_finds_work_by_member_name(team):
    _task(team["owner"], team["community"], [team["ids"]["Rahul"]], title="Anything")
    hits = team["owner"].get(f"/api/work/communities/{team['community']}/search",
                             params={"q": "rahul"}).json()["results"]
    assert hits and hits[0]["member"]["name"] == "Rahul"


def test_search_finds_a_task_by_the_start_of_its_id(team):
    """Nobody types a whole uuid. They paste the front of one out of a URL."""
    t = _task(team["owner"], team["community"], [team["ids"]["Rahul"]])
    hits = team["owner"].get(f"/api/work/communities/{team['community']}/search",
                             params={"q": t["id"][:8]}).json()["results"]
    assert [h["id"] for h in hits] == [t["id"]]


def test_search_matches_what_the_history_shows(team, amit):
    """Search must be neither narrower nor wider than the page it searches:
    your own work, plus anybody's finished work."""
    _join(team["owner"], amit, team["community"], "w_amit@example.com")
    ids = _members(team["owner"], team["community"])

    live = _task(team["owner"], team["community"], [ids["Rahul"]], title="Ongoing job")
    done = _task(team["owner"], team["community"], [ids["Rahul"]], title="Finished job")
    team["rahul"].post(f"/api/work/tasks/{done['id']}/respond", json={"accept": True})
    team["rahul"].put(f"/api/work/tasks/{done['id']}/progress", json={"progress": 100})

    hits = amit.get(f"/api/work/communities/{team['community']}/search",
                    params={"q": "job"}).json()["results"]
    assert [h["title"] for h in hits] == ["Finished job"]
    assert live["id"] not in [h["id"] for h in hits]
