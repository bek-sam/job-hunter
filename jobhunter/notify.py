"""Notifications: macOS banner always; phone push via ntfy.sh if NTFY_TOPIC is set in .env (free, no account)."""
import os, subprocess, urllib.request
import config  # noqa: F401


def push(msg, title='Job Hunter'):
    try:
        subprocess.run(['osascript', '-e', f'display notification {json_str(msg)} with title {json_str(title)} sound name "Glass"'],
                       timeout=10, capture_output=True)
    except Exception:
        pass
    topic = os.environ.get('NTFY_TOPIC')
    if topic:
        try:
            urllib.request.urlopen(urllib.request.Request(f'https://ntfy.sh/{topic}', data=msg.encode(), headers={'Title': title}), timeout=15)
        except Exception:
            pass
    print('NOTIFY:', msg)


def json_str(s):
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'
