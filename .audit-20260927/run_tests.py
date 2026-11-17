import os, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.dont_write_bytecode=True
DATA=ROOT/'.audit-20260927'/'runtime'
DATA.mkdir(parents=True,exist_ok=True)
os.environ['SAYA_GAME_MEMORY']='0'
os.environ['SAYA_SLU_CORPUS']='0'
os.environ['SAYA_DEFAULT_MODEL']='api'
import saya_app.config as config
original=config.KNOWLEDGE_BASE_DIR
for name,value in list(vars(config).items()):
    if isinstance(value,Path) and value.is_relative_to(original):
        setattr(config,name,DATA/'knowledge_base'/value.relative_to(original))
config.CHAT_MEMORY_DIR=DATA/'chats'
config.UPLOADED_FILES_DIR=DATA/'uploaded_files'
for folder in (config.KNOWLEDGE_BASE_DIR,config.CHAT_MEMORY_DIR,config.UPLOADED_FILES_DIR):
    folder.mkdir(parents=True,exist_ok=True)
if __name__=='__main__':
    import pytest
    raise SystemExit(pytest.main(['-q','tests','--basetemp='+str(DATA/'pytest'),'-p','no:cacheprovider','--tb=short']))
