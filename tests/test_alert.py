"""wa-reel-alert glue: queueing rules and backlog seeding."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import time



def test_alert_queueing_and_backlog(tools, sandbox, check):
    walib, dl, auth, q = tools.walib, tools.dl, tools.auth, tools.queue
    T = str(sandbox.tools_dir)

    def mk(name, size=20000):
        p = os.path.join(walib.REELS_DIR, name); os.makedirs(walib.REELS_DIR, exist_ok=True); open(p, 'wb').write(b'0' * size); return p
    print('--- alert daemon glue ---')
    spec = importlib.util.spec_from_file_location('alert', T + '/wa-reel-alert.py'); al = importlib.util.module_from_spec(spec); spec.loader.exec_module(al)
    subs = []; al.prefetcher.submit = lambda rid, url, fs=None: subs.append((rid, url, fs)) or True
    al.is_fast_network = lambda: True
    class A: autoplay = False; download = False
    with contextlib.redirect_stdout(io.StringIO()):
        al.notify_reel({'reel_id': 'FRIEND000001', 'sender': 'Friend', 'title': '', 'url': 'u1', 'first_seen_at': 1700000000000}, A())
        al.notify_reel({'reel_id': 'ME00000000001', 'sender': 'You', 'title': '', 'url': 'u2', 'first_seen_at': 1700000001000}, A())
    time.sleep(0.3)
    check('friend\'s reel is queued with its first_seen (ms -> s)', subs == [('FRIEND000001', 'u1', 1700000000.0)], str(subs))
    check('your own sent reel is NOT prefetched', all(s[0] != 'ME00000000001' for s in subs))
    subs.clear()
    with walib.get_db() as c:
        c.execute("delete from reels")
        base = int(time.time() * 1000)
        for i in range(14): c.execute("insert into reels(reel_id,sender,url,first_seen_at,is_likely_live,is_opened) values(?,?,?,?,1,0)", (f'SEED{i:08d}', 'Friend', f'u{i}', base - i * 60000))
        c.execute("insert into reels(reel_id,sender,url,first_seen_at,is_likely_live,is_opened) values('OLDSEED00001','Friend','u',?,1,0)", (base - 30 * 3600 * 1000,))
        c.execute("insert into reels(reel_id,sender,url,first_seen_at,is_likely_live,is_opened) values('OPENED000001','Friend','u',?,1,1)", (base,))
        c.execute("insert into reels(reel_id,sender,url,first_seen_at,is_likely_live,is_opened) values('MINE00000001','You','u',?,1,0)", (base,))
        c.execute("insert into reels(reel_id,sender,url,first_seen_at,is_likely_live,is_opened) values('HISTORIC0001','Friend','u',?,0,0)", (base,))
    mk('SEED00000001.mp4')   # already cached -> must be skipped
    with contextlib.redirect_stdout(io.StringIO()): al.seed_backlog(limit=10, hours=24)
    ids = [s[0] for s in subs]
    check('backlog seeding: max 10, newest first, skips cached', len(ids) == 10 and ids[0] == 'SEED00000000' and 'SEED00000001' not in ids, str(ids))
    check('backlog seeding skips: >24 h old, already opened, your own, non-live', not ({'OLDSEED00001', 'OPENED000001', 'MINE00000001', 'HISTORIC0001'} & set(ids)))

    check.assert_all()
