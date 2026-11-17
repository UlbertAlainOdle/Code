"""Independent Windows guardian: remove only this backend's owned WSL server."""
import argparse
import subprocess
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--token', required=True)
    for name in ('owner-pid', 'owner-birth', 'worker-pid', 'worker-birth'):
        parser.add_argument('--' + name, type=int, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.root))
    from saya_app.services.process_watch import watch_owned_processes
    from saya_app.services.omni_tts import linux_path
    marker = args.root / ('.runtime/qwen-omni-' + args.token + '.guardian')

    def cleanup():
        subprocess.run(['wsl.exe', '-d', 'Ubuntu', '--', 'python3',
                        linux_path(args.root / 'scripts/stop_qwen_omni.py'),
                        '--root', linux_path(args.root), '--token', args.token],
                       timeout=20, check=True, creationflags=subprocess.CREATE_NO_WINDOW)

    try:
        watch_owned_processes(args.owner_pid, args.owner_birth, args.worker_pid,
                              args.worker_birth, cleanup,
                              lambda: marker.write_text(str(args.owner_pid)), time.sleep)
    finally:
        marker.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
