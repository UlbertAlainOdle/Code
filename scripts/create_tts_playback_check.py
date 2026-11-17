"""Generate an ignored local page for actual PCM and chat-delta playback checks."""

import re
from pathlib import Path
root = Path(__file__).resolve().parents[1]
html = (root / 'static/Chat4o5-Modern.html').read_text(encoding='utf-8')
form = re.search(r'<section class="settings-pane" data-pane="voice" hidden>(.*?)</section>', html, re.S).group(1)
page = '''<!doctype html><meta charset="utf-8"><title>真实语音连续播放检查</title>
<style>body{font:16px system-ui;max-width:1100px;margin:40px auto;background:#f4f7fa;color:#223}button{padding:12px;margin:8px}textarea{width:98%;height:120px}pre{background:white;padding:20px}details{margin:20px}.voice-grid{display:grid;grid-template-columns:1fr 1fr;gap:15px}label{display:block}</style>
<h1>Saya · 真实语音连续播放检查</h1><p>使用项目原有播放器、真实流式接口与保存的克隆音色。</p>
<textarea id="test-text"></textarea><button id="test-start">开始长文本朗读</button><button id="test-chat">检查逐字输出提前朗读</button><button id="test-cancel">中止朗读</button><pre id="result">等待检查</pre>
<details><summary>项目语音设置</summary>''' + form + '''</details>
<script>
if(location.search==='?chat=1'){const original=window.fetch;window.fetch=async(...args)=>{const r=await original(...args);if(args[0]==='/api/tts/settings'&&!args[1]?.method)return new Response(JSON.stringify({...await r.json(),auto_read:true}),{headers:r.headers});return r;};}
</script><script src="/static/js/pcm_voice_player.js"></script><script src="/static/js/voice_settings.js"></script>
<script>
const paragraph='博士，先坐下来休息一会儿吧。今天的任务可以分成几个小步骤，不需要一次全部完成。我们先整理最重要的事情，再确认每一步需要哪些材料和工具。遇到不确定的地方，就停下来检查，慢慢把问题弄清楚。你已经付出了很多努力，剩下的事情我们一起安排。等忙完以后，可以喝一杯温水，看看窗外，给自己留一点安静的时间。明天还有新的机会，今天先把这一件事做好就足够了。';
document.getElementById('test-text').value=paragraph.repeat(3);
let started=0;
document.getElementById('test-start').onclick=async()=>{started=performance.now();document.getElementById('result').textContent='正在播放';try{await SayaSpeech.speak(document.getElementById('test-text').value);document.getElementById('result').textContent=JSON.stringify({elapsedSeconds:(performance.now()-started)/1000,...SayaSpeech.lastMetrics},null,2);}catch(e){document.getElementById('result').textContent='失败：'+e.message;}};
document.getElementById('test-cancel').onclick=()=>{SayaSpeech.stop();document.getElementById('result').textContent='已中止朗读';};
document.getElementById('test-chat').onclick=async()=>{SayaSpeech.stop();const start=performance.now();document.getElementById('result').textContent='正在播放逐字文本';try{const session=await SayaVoice.beginChat();if(!session)throw new Error('需要 ?chat=1 测试配置');const text=paragraph.repeat(2);for(let i=0;i<text.length;i+=6){session.push(text.slice(i,i+6));await new Promise(r=>setTimeout(r,50));}const textDoneSeconds=(performance.now()-start)/1000;await session.finish(text);document.getElementById('result').textContent=JSON.stringify({textDoneSeconds,elapsedSeconds:(performance.now()-start)/1000,...SayaSpeech.lastMetrics},null,2);}catch(e){document.getElementById('result').textContent='失败：'+e.message;}};
</script>'''
(root / 'static/_tts_diagnostic.html').write_text(page, encoding='utf-8')
print('static/_tts_diagnostic.html')
