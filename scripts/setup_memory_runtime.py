"""Install the project-local CPU embedding runtime and public model files."""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
REPO = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skip-download', action='store_true')
    args = parser.parse_args()
    env_dir = ROOT / '.runtime' / 'memory-python'
    subprocess.run([sys.executable, '-m', 'venv', '--system-site-packages', str(env_dir)], check=True)
    python = env_dir / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    subprocess.run([str(python), '-m', 'pip', 'install', 'sentence-transformers==4.1.0',
                    'transformers==4.48.3', 'tokenizers==0.21.4', 'accelerate==1.3.0', 'peft==0.14.0'], check=True)
    if not args.skip_download:
        hf = Path(sys.executable).parent / ('Scripts/hf.exe' if sys.platform == 'win32' else 'hf')
        subprocess.run([str(hf), 'download', REPO, '--local-dir', str(ROOT / '.runtime' / 'embedding-model'),
                        '--revision', 'e8f8c211226b894fcb81acc59f3b34ba3efd5f42',
                        '--include', '*.json', 'model.safetensors', 'tokenizer*',
                        'sentencepiece.bpe.model', '1_Pooling/*'], check=True)


if __name__ == '__main__':
    main()
