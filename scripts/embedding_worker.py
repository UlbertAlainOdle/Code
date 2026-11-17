"""Private local JSON-lines embedding worker; stdout contains only replies."""
import contextlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from saya_app.services.embedding_provider import SentenceTransformerEmbeddingProvider


def main():
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        stream.reconfigure(encoding='utf-8')
    with contextlib.redirect_stdout(sys.stderr):
        import torch
        torch.set_num_threads(4)
        provider = SentenceTransformerEmbeddingProvider(sys.argv[1], sys.argv[2], int(sys.argv[3]))
    for line in sys.stdin:
        try:
            request = json.loads(line)
            with contextlib.redirect_stdout(sys.stderr):
                vectors = provider.embed(request['texts'])
            reply = {'vectors': vectors}
        except Exception as exc:
            reply = {'error': f'{type(exc).__name__}: {exc}'}
        print(json.dumps(reply), flush=True)


if __name__ == '__main__':
    main()
