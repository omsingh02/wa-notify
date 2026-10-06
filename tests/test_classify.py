"""yt-dlp / network error classification (strings copied from real logs)."""

from __future__ import annotations



def test_classify_errors(tools, sandbox, check):
    walib, dl, auth, q = tools.walib, tools.dl, tools.auth, tools.queue

    print('--- classify_error (strings copied from your real journal) ---')
    C = dl.classify_error
    check('rate-limit/login redirect -> blocked', C("ERROR: [Instagram] X: The webpage request was redirected to the login page. You have exceeded the rate-limit for accessing posts anonymously. Use --cookies-from-browser", 'https://www.instagram.com/reel/X')[0] == 'blocked')
    check('no video in this post -> no_video', C("ERROR: [Instagram] X: There is no video in this post", '')[0] == 'no_video')
    check('No video formats found on /p/ -> no_video (carousel)', C("ERROR: [Instagram] X: No video formats found!; please report this issue", 'https://www.instagram.com/p/X')[0] == 'no_video')
    check('No video formats found on /reel/ -> no_formats (retryable)', C("ERROR: [Instagram] X: No video formats found!; please report", 'https://www.instagram.com/reel/X')[0] == 'no_formats')
    check('empty media anonymously -> blocked', C("ERROR: [Instagram] X: Instagram sent an empty media response. Check if this post is accessible in your browser without being logged-in.", '', authed=False)[0] == 'blocked')
    check('empty media with session -> empty', C("ERROR: [Instagram] X: Instagram sent an empty media response.", '', authed=True)[0] == 'empty')
    check("isn't available to everyone -> unavailable", C("ERROR: [Instagram] X: This content isn't available to everyone: It can't be seen by certain audiences.", '')[0] == 'unavailable')
    check('follow-only -> unavailable', C("ERROR: [Instagram] X: This content is only available for registered users who follow this account.", '')[0] == 'unavailable')
    check('rename error -> error', C("ERROR: Unable to rename file: [Errno 2] No such file or directory", '')[0] == 'error')
    check('picks the ERROR line over a leading warning', C("WARNING: foo\nERROR: [Instagram] X: There is no video in this post", '')[1].startswith('ERROR'))

    print('--- network failures (strings from your real journal) ---')
    check('curl DNS timeout -> network', C("ERROR: [Instagram] DeB48_qJGqk: Video info extraction failed: Failed to perform, curl: (28) Resolving timed out after 15002 milliseconds. See https://curl.se/libcurl/c/libcurl-errors.html", '')[0] == 'network')
    check('curl connect timeout -> network', C("ERROR: [Instagram] DeBoc8vpI70: Video info extraction failed: Failed to perform, curl: (28) Connection timed out after 15001 milliseconds", '')[0] == 'network')
    check('urllib name resolution -> network', C("ERROR: Unable to download webpage: <urlopen error [Errno -3] Temporary failure in name resolution>", '')[0] == 'network')
    check('name or service not known -> network', C("ERROR: unable to download: <urlopen error [Errno -2] Name or service not known>", '')[0] == 'network')
    check('network words do not hijack a login block', C("ERROR: redirected to the login page. You have exceeded the rate-limit", '')[0] == 'blocked')
    check('network words do not hijack a photo post', C("ERROR: There is no video in this post", '')[0] == 'no_video')
    import urllib.error, socket
    E = dl._classify_exception
    check('image download: DNS failure -> network', E(urllib.error.URLError(socket.gaierror(-3, 'Temporary failure in name resolution'))) == 'network')
    check('image download: socket timeout -> network', E(socket.timeout('timed out')) == 'network')
    check('image download: connection refused -> network', E(ConnectionRefusedError()) == 'network')
    check('image download: HTTP 403 (expired signed url) -> error, not network', E(urllib.error.HTTPError('u', 403, 'Forbidden', {}, None)) == 'error')
    check('image download: too-small file -> error', E(OSError('image 2 is too small (12 bytes)')) == 'error')

    check.assert_all()
