import logging

from intake_bot.__main__ import RedactSecrets

TOKEN = "123456789:FAKEtokenFAKEtokenFAKEtokenFAKE0000"  # made-up example, not a real token


def test_token_is_masked_in_message_and_traceback():
    f = RedactSecrets([TOKEN])
    try:
        raise RuntimeError(f"The token `{TOKEN}` was rejected by the server.")
    except RuntimeError:
        import sys
        rec = logging.LogRecord("t", logging.ERROR, __file__, 1, "url https://api.telegram.org/bot%s/getMe", (TOKEN,), sys.exc_info())
    assert f.filter(rec)
    out = logging.Formatter("%(message)s").format(rec)
    assert TOKEN not in out
    assert out.count("<redacted>") == 2
