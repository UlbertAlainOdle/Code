import pytest

from saya_app.services.qq_presentation import project_answer, wants_sources, warning_note, reply_guidance
from saya_app.services.qq_chat import channel_identity_prompt
from saya_app.services.qq_presentation import clean_control_output, clean_assistant_history


@pytest.mark.parametrize('tag', ['[MOD_SAYA: affectionate]', '[MODAL_SAYA: affectionate]', '[MOOD_SAYA: happy]',
    '［modal_saya ： affectionate］', '[MODEL_SAYA: affectionate]', '[MODAL_SAYA: affectionate',
    '[mood_user : tired]', '［MOTION：happy］', '[ MC : follow Doctor ]',
    '[MOD_SAYA: affectionate', '[MOOD_SAYA: happy'])
def test_control_variants_are_removed_even_when_sources_requested(tag):
    text = tag + '\n你好，正文[99]。'
    for show in [False, True]:
        assert project_answer(text, {}, set(), show_sources=show) == '你好，正文[99]。'


def test_control_filter_keeps_literal_protocol_examples_and_normal_brackets():
    text = '示例 `[MOD_SAYA: affectionate]`，$x[1]$。\n```text\n[MOTION: happy]\n<think>literal</think>\n```\n[普通说明]'
    assert clean_control_output(text) == text
    assert clean_control_output('你好[MOD_SAYA: affectionate]，再见。') == '你好，再见。'


def test_accidental_thinking_in_final_text_is_hidden_including_nested_code():
    text = '<think>PRIVATE\n```python\nSECRET\n```\nreason</think>可见正文。'
    assert clean_control_output(text) == '可见正文。'
    assert clean_control_output('正文。<THINK>unfinished secret') == '正文。'
    assert clean_control_output(clean_control_output(text)) == '可见正文。'


def test_old_assistant_protocol_is_removed_only_from_ephemeral_history():
    history=['Doctor: 请解释 [MOD_SAYA: affectionate]', 'Saya: [MOD_SAYA: affectionate]旧回答']
    original=list(history)
    assert clean_assistant_history(history)==[history[0],'Saya: 旧回答']
    assert history==original


@pytest.mark.parametrize('text,expected', [
    ('今天有什么大新闻', False), ('/search DeepSeek Search Fetch 官方文档', False),
    ('给我来源', True), ('新闻，附上原文链接', True), ('出处呢', True), ('来源？', True),
    ('链接发我', True), ('出处在哪里？', True),
    ('Please include sources', True), ('不要显示来源', False), ('不用附链接', False),
    ('新闻不要来源，给我官网网址', True), ('这个消息的来源不可靠', False),
])
def test_sources_are_explicit_and_can_be_declined(text, expected):
    assert wants_sources(text) is expected


def test_qq_style_preserves_persona_and_identity_permissions():
    owner = channel_identity_prompt({'is_owner': True, 'shared': True})
    group = channel_identity_prompt({'is_owner': True, 'shared': False, 'group_id': '123'})
    assert 'existing character, personality, relationship' in owner
    assert '1-3 short sentences' in owner and '80-200' in owner
    assert 'explicitly requests details' in owner
    assert 'IS your configured owner' in owner
    assert 'owner\'s private memories stay unavailable' in group
    assert 'This conversation is isolated' in group
    assert 'user requested sources' in reply_guidance(True)


@pytest.mark.parametrize('code', ['search_safe_search_unavailable', 'search_region_hint_unavailable', 'context_limited'])
def test_informational_backend_notes_are_hidden(code):
    assert warning_note({'code': code, 'message': 'Backend capability text'}) == ''


@pytest.mark.parametrize('code', ['search_failed', 'search_auth_error', 'search_rate_limited', 'search_insufficient_balance'])
def test_actual_search_failures_remain_visible_and_short(code):
    note = warning_note({'code': code, 'message': 'Provider-specific diagnostic'})
    assert '联网查询没成功' in note and len(note) < 40
    assert 'Provider' not in note


def test_unknown_warning_and_strict_search_failure_are_not_silently_swallowed():
    assert warning_note({'code': 'unknown', 'message': '资料尚未核实'}) == '资料尚未核实'
    assert '没有发起检索' in warning_note({'code': 'search_unsupported_safe_search'})


def test_rendering_hides_known_sources_without_touching_canonical_evidence():
    sources = {'https://example.org/evidence': 'Official page'}
    original = '今天发生了一件事[1]。\n\n参考来源：\nOfficial page\nhttps://example.org/evidence'
    assert project_answer(original, sources, {1}) == '今天发生了一件事。'
    assert '[1]' in original and sources['https://example.org/evidence'] == 'Official page'
    assert project_answer(original, sources, {1}, show_sources=True) == original


def test_literal_code_math_arrays_unknown_ids_and_user_urls_survive_projection():
    url = 'https://example.org/evidence'
    text = '事实[1]。未知[99]。数组 [1]，arr[1]，`a[1]`，$x[1]$。\n```python\na=[1]\nprint("' + url + '")\n```'
    result = project_answer(text, {url: 'Source'}, {1})
    assert '事实。' in result and '未知[99]' in result
    for literal in ['数组 [1]', 'arr[1]', '`a[1]`', '$x[1]$', 'a=[1]', url]:
        assert literal in result
    assert url in project_answer('你给的网址是 ' + url, {url: 'Source'}, {1}, user_input='读 ' + url)
    assert 'https://unrelated.example/' in project_answer('打开 https://unrelated.example/', {url: 'Source'}, {1})


def test_source_word_in_news_body_is_not_removed_as_a_footer():
    text = 'DeepSeek发布了新功能。\n来源\n消息来源仍有分歧，需要进一步核实。'
    assert project_answer(text, {}, {1}) == text


def test_known_citation_groups_are_hidden_but_unrelated_ranges_are_preserved():
    assert project_answer('说明[1-3]，细节[1，2]。未知[4-9]。', {}, {1, 2, 3}) == '说明，细节。未知[4-9]。'
    assert project_answer('很大的区间[1-999999]。', {}, {1}) == '很大的区间[1-999999]。'



def test_paired_mood_instruction_tail_is_hidden_but_literal_examples_survive():
    tail='\n\n→ affectionate\n\nUse one label:'
    assert clean_control_output('两项分开核对。'+tail)=='两项分开核对。'
    for literal in ['```text'+tail+'\n```','~~~text'+tail+'\n~~~','“'+tail+'”','说明：Use one label:', '→ affectionate']:
        assert clean_control_output(literal)==literal
