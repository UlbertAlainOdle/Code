import json
from pathlib import Path

import pytest

from saya_app.services.speech_text import clean_speech, prepare_speech, speech_language
from saya_app.services.local_tts import VoiceSettings


CASES = json.loads(Path(__file__).with_name('speech_text_cases.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('case', CASES)
def test_normalized_text_and_chinese_language_routing(case):
    clean = clean_speech(case['raw'])
    assert clean == case['clean']
    assert speech_language(clean, 'Chinese') == case['language']


def test_no_empty_speech_and_no_persisted_setting_mutation():
    settings = VoiceSettings(language='Chinese')
    text, applied = prepare_speech('你好😊，hello world。', settings)
    assert text == '你好，hello world。'
    assert applied.language == 'Auto'
    assert settings.language == 'Chinese'
    with pytest.raises(ValueError, match='没有可朗读'):
        prepare_speech('😊 [微笑] !!!', settings)


@pytest.mark.parametrize('language', ['Auto', 'English', 'Japanese', 'German'])
def test_other_explicit_languages_are_preserved(language):
    assert speech_language('中文 English', language) == language
