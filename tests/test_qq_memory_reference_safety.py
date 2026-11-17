import json
import pytest
from core_services import KnowledgeBase
from saya_app.services.qq_action_proposals import QQActionProposals
from saya_app.services.memory_reference import unresolved_reference,save_reference
from saya_app.services.memory_v4.write_pipeline import WriteIntentGate
from test_qq_memory_management import lab

@pytest.mark.parametrize('content',['你上面说的记忆','刚才那条','你刚才的解释','上面那段话','that previous memory'])
@pytest.mark.parametrize('v4',[True,False])
def test_reference_never_becomes_persistent_content(tmp_path,content,v4):
    kb=KnowledgeBase(str(tmp_path));kb.settings['memory_v4_write_enabled']=v4
    before=[x['id'] for x in kb.memories]
    assert kb.capture_explicit_memory('记住'+content) is None
    assert [x['id'] for x in kb.memories]==before

def test_structured_action_cannot_override_unresolved_chat_instruction():
    decision=WriteIntentGate().detect('记住你上面说的记忆',{'intent':'explicit_save','content':'凭空编造的正文'})
    assert decision.intent=='none' and decision.reason_code=='unresolved_reference'

@pytest.mark.parametrize('content',['我喝咖啡不加糖','你刚才推荐的书是《海风》','小说标题叫你上面说的记忆'])
def test_complete_fact_not_rejected_by_reference_words(content):
    assert not unresolved_reference(content)
    assert WriteIntentGate().detect('记住：'+content).intent=='explicit_save'

def setup_proposal(lab):
    b,k,s,actor,_=lab;scope='10000:private:20000:shared';sid='session_qq_reference.jsonl'
    b.store.save_conversation(scope,[],sid)
    b.action_proposals=QQActionProposals(b.store)
    candidate=k.add_memory_candidate('用户认为读书需要慢慢来。',title='阅读观点')
    payload,preview=s.candidate_proposal(actor,candidate['id'])
    question='要保存阅读观点吗？';revision=b.controls()['revision']
    b.action_proposals.ask(scope,sid,'save_candidate',payload,question,revision)
    b.action_proposals.delivered(scope,sid,question)
    return b,k,s,actor,scope,sid,revision,candidate

def test_readonly_list_then_bare_remember_confirms_original_candidate(lab):
    b,k,s,a,scope,sid,revision,c=setup_proposal(lab)
    assert b.action_proposals.reply(scope,sid,'/events self',revision,True) is None
    decision=b.action_proposals.reply(scope,sid,'记住',revision,True)
    assert decision['status']=='approved' and decision['payload']['candidate_id']==c['id']
    result=s.confirm_candidate(a,c['id'],decision['payload']['candidate_version'],sid)
    assert '已确认并保存' in result
    assert any(x['content']=='用户认为读书需要慢慢来。' for x in k.memories)

def test_ambiguous_reference_clarifies_then_explicit_candidate_gets_delivered_preview(lab):
    b,k,s,a,scope,sid,revision,c=setup_proposal(lab);before=len(k.memories)
    assert b.action_proposals.reply(scope,sid,'记住你上面说的记忆',revision,True) is None
    clarification=s.natural_response(a,'记住你上面说的记忆',sid)
    assert '候选记忆' in clarification and '尚未保存' in clarification
    assert b.action_proposals.reply(scope,sid,'记住',revision,True) is None
    preview=s.natural_response(a,'记住刚才那条候选',sid)
    assert '用户认为读书需要慢慢来。' in str(preview) and len(k.memories)==before
    assert b.action_proposals.reply(scope,sid,'记住',revision,True) is None
    b.action_proposals.delivered(scope,sid,str(preview))
    assert b.action_proposals.reply(scope,sid,'记住',revision,True)['status']=='approved'

def test_expiry_authorization_and_new_topic_still_prevent_confirmation(lab):
    b,k,s,a,scope,sid,revision,c=setup_proposal(lab)
    b.action_proposals.reply(scope,sid,'换个话题说说电影',revision,True)
    assert b.action_proposals.reply(scope,sid,'记住',revision,True) is None
    b,k,s,a,scope,sid,revision,c=setup_proposal(lab)
    assert b.action_proposals.reply(scope,sid,'记住',revision,False)['status']=='stale'
