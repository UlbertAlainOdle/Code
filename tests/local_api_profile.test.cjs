const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {mergeDraft,syncBrowser,restoreBrowser,parse,apiFields,searchProviderView}=require('../static/js/local_api_profile.js');
const api='saya_api_settings_v1',other='saya_other_api_settings_v1';
function storage(){const data=new Map([[api,JSON.stringify({api_key:'browser-secret',custom:'keep'})],[other,JSON.stringify({tts_voice_id:'browser-voice',custom:'keep-other'})]]);return {data,getItem:k=>data.get(k)??null,setItem:(k,v)=>data.set(k,v),removeItem:k=>data.delete(k)};}
test('runtime local preference leaves browser draft intact and explicit blank blocks stale cache',()=>{
 const draft={api_key:'browser-secret',brave_search_api_key:'stale',custom:'keep'};
 const result=mergeDraft(draft,{prefer_local:true,settings:{api_key:'local-secret',brave_search_api_key:''}},apiFields);
 assert.equal(result.api_key,'local-secret');assert.equal(result.brave_search_api_key,'');assert.equal(draft.api_key,'browser-secret');
 assert.equal(mergeDraft(draft,{prefer_local:false,settings:{api_key:'local'}},apiFields).api_key,'browser-secret');
});
test('unified file mode never fills missing API fields from stale browser drafts',()=>{
 const draft={api_key:'STALE_KEY',api_url:'https://stale.invalid',multimodal_model_name:'stale-vision'};
 const profile={prefer_local:true,settings:{api_key:'LOCAL_KEY',api_url:'https://local.invalid',api_model_name:'local'}};
 const result=mergeDraft(draft,profile,apiFields,true);
 assert.equal(result.api_key,'LOCAL_KEY');assert.equal(result.multimodal_model_name,undefined);assert.equal(draft.multimodal_model_name,'stale-vision');
 assert.deepEqual(mergeDraft(draft,{prefer_local:true,settings:{}},apiFields,true),{});
 assert.equal(mergeDraft(draft,{prefer_local:false,settings:{}},apiFields,true).api_key,'STALE_KEY');
});
test('one selected provider controls readiness; a saved other key cannot mask the selected missing key',()=>{
 assert.deepEqual(searchProviderView({search_provider:'deepseek',configured:{brave_search_api_key:true}}),{provider:'deepseek',label:'DeepSeek',configured:false});
 assert.equal(searchProviderView({search_provider:'deepseek',settings:{deepseek_search_api_key:'fixture'}}).configured,true);
 assert.equal(searchProviderView({configured:{brave_search_api_key:true}}).provider,'brave');
});
test('manual pull backs up exact original strings before merging, preserves unrelated browser fields',()=>{
 const s=storage(),before=s.getItem(api),beforeOther=s.getItem(other);
 const backup=syncBrowser(s,{api_key:'local-secret',tts_voice_id:'local-voice'},123);
 assert.deepEqual(JSON.parse(s.getItem(backup)),{api:before,other:beforeOther,created:123});
 assert.equal(JSON.parse(s.getItem(api)).custom,'keep');assert.equal(JSON.parse(s.getItem(other)).custom,'keep-other');
 assert.equal(JSON.parse(s.getItem(api)).api_key,'local-secret');
});
test('backup failure and invalid caches never touch original browser settings',()=>{
 const s=storage(),before=s.getItem(api);s.setItem=()=>{throw new Error('quota');};
 assert.throws(()=>syncBrowser(s,{api_key:'local'}));assert.equal(s.getItem(api),before);
 for(const raw of ['[]','null','"text"'])assert.throws(()=>parse(raw));
});
test('partial cache write failure restores both originals and retains backup',()=>{
 const s=storage(),apiBefore=s.getItem(api),otherBefore=s.getItem(other),write=s.setItem;let failed=false;
 s.setItem=(k,v)=>{if(k===other&&!failed){failed=true;throw new Error('quota');}write(k,v);};
 assert.throws(()=>syncBrowser(s,{api_key:'new',tts_voice_id:'new'}));
 assert.equal(s.getItem(api),apiBefore);assert.equal(s.getItem(other),otherBefore);
 assert.ok([...s.data.keys()].some(k=>k.startsWith('saya_api_cache_backup_')));
});
test('explicit restore brings back exact original cache and backs up the current one',()=>{
 const s=storage(),before=s.getItem(api),beforeOther=s.getItem(other);
 const backup=syncBrowser(s,{api_key:'local-secret',tts_voice_id:'local-voice'},123),changed=s.getItem(api);
 const undo=restoreBrowser(s,backup,124);
 assert.equal(s.getItem(api),before);assert.equal(s.getItem(other),beforeOther);
 assert.equal(JSON.parse(s.getItem(undo)).api,changed);
});
test('provider badges reflect effective local credentials even when browser input fields are empty',()=>{
 const source=fs.readFileSync('static/js/app.js','utf8');
 const start=source.indexOf('    function refreshProviderStatusBadges()');
 const end=source.indexOf('\n    document.addEventListener',start);
 assert.ok(start>=0&&end>start);
 for(const configured of [true,false]) {
  const badges={translation:{},tts:{}};
  const settings=configured?{translation_secret_id:'file-id',translation_secret_key:'file-key',tts_api_key:'file-tts'}:{};
  vm.runInNewContext(source.slice(start,end)+'\nrefreshProviderStatusBadges();',{
   getSavedOtherApiSettings:()=>settings,
   document:{querySelector:selector=>badges[selector.includes('translation')?'translation':'tts'],getElementById:()=>({value:''})}
  });
  assert.equal(badges.translation.textContent,configured?'Configured':'Not configured');
  assert.equal(badges.tts.textContent,configured?'Configured':'Not configured');
 }
});
