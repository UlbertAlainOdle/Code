"""Stop an owned WSL process group; escalate only verified surviving members."""
import argparse
import os
import signal
import time
from pathlib import Path

def group_members(group):
    members = {}
    for path in Path('/proc').glob('[0-9]*/stat'):
        try:
            fields = path.read_text().rsplit(')', 1)[1].split()
            if int(fields[2]) == group and fields[0] != 'Z':
                members[int(path.parent.name)] = fields[19]
        except (OSError, ValueError, IndexError):
            pass
    return members


def stop(root, token, timeout=8):
    path = root / ('.runtime/qwen-omni-' + token + '.pid')
    try:
        pid = int(path.read_text())
        environment = (Path("/proc") / str(pid) / "environ").read_bytes().split(b"\0")
        if ('SAYA_OMNI_TOKEN=' + token).encode() not in environment or os.getpgid(pid) != pid:
            return
        original = group_members(pid)
        os.killpg(pid, signal.SIGTERM)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = group_members(pid)
            if not any(current.get(member) == birth for member, birth in original.items()):
                break
            time.sleep(0.1)
        else:
            for member, birth in original.items():
                current = group_members(pid)
                if current.get(member) == birth:
                    try:
                        os.kill(member, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
    except (FileNotFoundError, ProcessLookupError):
        pass
    finally:
        path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--token', required=True)
    args = parser.parse_args()
    stop(args.root, args.token)


if __name__ == '__main__':
    main()
