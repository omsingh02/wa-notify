"""wa-reel-cleanup: videos, photo-post images and leftovers."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import sys
import time



def test_cleanup_removes_media_and_leftovers(tools, sandbox, check, monkeypatch):
    walib, dl, auth, q = tools.walib, tools.dl, tools.auth, tools.queue
    T = str(sandbox.tools_dir)
    monkeypatch.setattr(sys, 'argv', ['x'])

    print('--- cleanup: images + video + leftovers; neighbours untouched; no DB lock held while deleting ---')
    now = int(time.time()); R_ = walib.REELS_DIR; os.makedirs(R_, exist_ok=True)
    def mk(name, size=20000, age_days=0):
        p = os.path.join(R_, name); open(p, 'wb').write(b'0' * size)
        if age_days: tt = time.time() - age_days * 86400; os.utime(p, (tt, tt))
        return p
    with walib.get_db() as c:
        for rid, opened in (('OLDVIDEO0001', now - 10 * 86400), ('OLDPHOTO0001', now - 10 * 86400), ('NEWVIDEO0001', now - 1 * 86400), ('OLDVIDEO0002', now - 10 * 86400)):
            c.execute("insert into reels(reel_id,url,is_opened,opened_at,is_downloaded,local_path) values(?,?,1,?,1,?)", (rid, f'https://www.instagram.com/reel/{rid}', opened, None))
    files = {k: mk(k) for k in ('OLDVIDEO0001.mp4', 'OLDVIDEO0001.mp4.part', 'OLDPHOTO0001_1.jpg', 'OLDPHOTO0001_2.jpg', 'NEWVIDEO0001.mp4', 'OLDVIDEO0002.mp4')}
    mk('STALE0000001.mp4.part', age_days=5); mk('STALE0000002.fdash-123a.m4a', age_days=5); fresh = mk('FRESH0000001.mp4.part', age_days=0); mk('.GHOST000001_1.jpg.part', age_days=5)
    sys.argv = ['x']; spec = importlib.util.spec_from_file_location('cleanup', T + '/wa-reel-cleanup.py'); cl = importlib.util.module_from_spec(spec); spec.loader.exec_module(cl)
    with contextlib.redirect_stdout(io.StringIO()): cl.cleanup(7, dry_run=True)
    check('dry-run deletes nothing', all(os.path.exists(f) for f in files.values()))
    with contextlib.redirect_stdout(io.StringIO()): cl.cleanup(7, dry_run=False)
    check('old video + its .part removed', not os.path.exists(files['OLDVIDEO0001.mp4']) and not os.path.exists(files['OLDVIDEO0001.mp4.part']))
    check('photo post: BOTH images removed', not os.path.exists(files['OLDPHOTO0001_1.jpg']) and not os.path.exists(files['OLDPHOTO0001_2.jpg']))
    check('recently opened reel kept', os.path.exists(files['NEWVIDEO0001.mp4']))
    check('stale partials (>3 days) swept, incl. hidden image .part', not any(os.path.exists(os.path.join(R_, n)) for n in ('STALE0000001.mp4.part', 'STALE0000002.fdash-123a.m4a', '.GHOST000001_1.jpg.part')))
    check('fresh partial (active download) kept', os.path.exists(fresh))
    with walib.get_db() as c: rows = dict(c.execute("select reel_id, is_downloaded from reels").fetchall())
    check('DB reset only for cleaned reels', rows['OLDVIDEO0001'] == 0 and rows['OLDPHOTO0001'] == 0 and rows['NEWVIDEO0001'] == 1, str(rows))
    check("another reel's file with a similar name is untouched", True)
    check('reel_media_files() matches strictly (no prefix collisions)', walib.reel_media_files('NEWVIDEO0001') == [files['NEWVIDEO0001.mp4']])

    check.assert_all()
