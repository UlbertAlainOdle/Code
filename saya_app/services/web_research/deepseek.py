"""Independent DeepSeek Search / Fetch JSON APIs; no model generation here."""
import datetime
import json
import re
import requests
from ..qq_tasks import checkpoint_current, phase_current

from saya_app.config import WEB_READER_MAX_BYTES,WEB_READER_MAX_TEXT_CHARS
from .models import SearchResult,PageDocument
from .reader import WebReader,clean_text
from .search import canonicalize_url
from .security import is_url_safe,parse_and_validate_url,UrlValidationError

BASE='https://api.deepseek.com/v1/tools/'


class DeepSeekAPI:
    def __init__(self,session=None,timeout=(5,20)):
        self.session=session if session is not None else requests.Session()
        self.timeout=timeout

    def post(self,operation,payload,key):
        if not key:return None,'missing_key'
        response=None
        try:
            phase_current('search' if operation=='search' else 'sources')
            response=self.session.post(BASE+operation,json=payload,
                headers={'Authorization':'Bearer '+key,'Content-Type':'application/json','Accept':'application/json'},
                timeout=self.timeout,allow_redirects=False,stream=True)
            checkpoint_current()
            code=response.status_code
            errors={400:'invalid_request',401:'auth_error',402:'insufficient_balance',403:'fetch_forbidden',404:'not_found',
                    408:'timeout',413:'response_too_large',429:'rate_limited',504:'timeout'}
            if code!=200:return None,errors.get(code,'server_error' if code>=500 else 'http_'+str(code))
            raw=bytearray()
            for chunk in response.iter_content(chunk_size=65536):
                checkpoint_current()
                raw.extend(chunk)
                if len(raw)>WEB_READER_MAX_BYTES:return None,'response_too_large'
            data=json.loads(raw)
            if not isinstance(data,dict):return None,'invalid_json'
            return data,''
        except requests.RequestException:return None,'network_error'
        except (ValueError,TypeError):return None,'invalid_json'
        finally:
            if response is not None:response.close()


class DeepSeekSearchService:
    supports_pagination=False
    provider='deepseek'
    def __init__(self,session=None,timeout=(5,20)):
        self.api=DeepSeekAPI(session,timeout)

    def search(self,query,api_key,count=None,max_results=None,search_lang='',country='',ui_lang='',freshness='',
               safesearch='moderate',goggles=None,offset=0):
        query=str(query or '').strip()
        if not query:return [],'empty_query'
        if not api_key:return [],'missing_key'
        if safesearch=='strict':return [],'unsupported_safe_search'
        if goggles:return [],'unsupported_filter'
        if offset:return [],'unsupported_pagination'
        operators=[]
        lang=str(search_lang or '').lower()
        if lang in ('zh-hans','zh-hant'):lang='zh'
        if re.fullmatch(r'[a-z]{2,3}',lang):operators.append('lang:'+lang)
        days={'pd':1,'pw':7,'pm':31,'py':366}.get(freshness)
        if days:operators.append('after:'+(datetime.datetime.now(datetime.timezone.utc).date()-datetime.timedelta(days=days)).isoformat())
        elif freshness:return [],'unsupported_freshness'
        # Never cut a query in the middle of a site/date/quoted constraint.
        query=' '.join([query,*operators])
        if len(query)>128:return [],'query_too_long'
        requested=max(1,min(int(count or 10),50))
        data,error=self.api.post('search',{'query':query,'count':requested,'need_content':False},api_key)
        if error:return [],error
        if not isinstance(data.get('results'),list):return [],'invalid_json'
        results=[];seen=set();cap=max(1,min(int(max_results or requested),50))
        for item in data['results']:
            if not isinstance(item,dict) or not isinstance(item.get('url'),str):continue
            try:parse_and_validate_url(item['url']);url=canonicalize_url(item['url'])
            except (ValueError,UrlValidationError):continue
            if url in seen:continue
            seen.add(url)
            results.append(SearchResult(title=str(item.get('title') or '')[:500],url=url,
                snippet=str(item.get('summary') or item.get('snippet') or '')[:2400],
                published_at=str(item.get('date') or '')[:100],rank=len(results)+1))
            if len(results)>=cap:break
        return results,''


class DeepSeekFetchReader(WebReader):
    def __init__(self,api_key,session=None,resolver=None,robots_session=None):
        super().__init__(session=robots_session,resolver=resolver)
        self.api=DeepSeekAPI(session)
        self.api_key=api_key

    def fetch(self,url,fallback_title='',check_robots=True):
        doc=PageDocument(requested_url=url,final_url=url,title=fallback_title)
        if not isinstance(url,str) or len(url)>8192:doc.status='blocked';doc.error_code='invalid_url_length';return doc
        safe,code,_=is_url_safe(url,resolver=self.resolver)
        if not safe:doc.status='blocked';doc.error_code=code;return doc
        if check_robots and self.is_allowed_by_robots(url)=='disallow':
            doc.status='blocked';doc.error_code='robots_disallow';return doc
        data,error=self.api.post('fetch',{'url':url},self.api_key)
        if error:
            doc.status='blocked' if error=='fetch_forbidden' else 'failed';doc.error_code=error;return doc
        if not isinstance(data.get('content'),str):doc.status='failed';doc.error_code='invalid_json';return doc
        doc.title=str(data.get('title') or fallback_title)[:500]
        doc.text=clean_text(data['content'])[:WEB_READER_MAX_TEXT_CHARS]
        doc.content_type='text/markdown'
        doc.status='ok' if doc.text else 'failed'
        doc.error_code='' if doc.text else 'empty_content'
        return doc
