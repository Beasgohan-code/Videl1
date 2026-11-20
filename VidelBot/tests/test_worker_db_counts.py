"""Offline regressions for WorkerDB bounded count helpers."""
from tests.harness import load_all, run

load_all()

from filestore.database.worker_db import WorkerDB  # noqa: E402


def test_worker_db_counts_match_list_lengths():
    wdb = WorkerDB(4242)
    run(wdb.add_user(1))
    run(wdb.add_user(2))
    run(wdb.add_admin(10))
    run(wdb.add_admin(11))
    run(wdb.add_ban_user(20))
    run(wdb.add_channel(-1001, "off"))
    run(wdb.add_channel(-1002, "on"))
    assert run(wdb.total_users()) == len(run(wdb.full_userbase())) == 2
    assert run(wdb.total_admins()) == len(run(wdb.get_all_admins())) == 2
    assert run(wdb.total_banned()) == len(run(wdb.get_ban_users())) == 1
    assert run(wdb.total_channels()) == len(run(wdb.show_channels())) == 2
