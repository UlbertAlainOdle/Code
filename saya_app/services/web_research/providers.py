"""One persisted provider choice, with request-scoped adapters and credentials."""
from urllib.parse import urlsplit

PROVIDERS=('brave','deepseek')


def official_deepseek_url(value):
    try:
        p=urlsplit(str(value or ''))
        return (p.scheme=='https' and p.hostname=='api.deepseek.com' and p.port in (None,443)
                and not p.username and not p.password and not p.query and not p.fragment and p.path.rstrip('/') in ('','/v1'))
    except ValueError:return False


def selected_search(credentials):
    provider=credentials.get('search_provider','brave')
    if provider not in PROVIDERS:return provider,''
    field='deepseek_search_api_key' if provider=='deepseek' else 'brave_search_api_key'
    return provider,str(credentials.get(field) or '')


def research_for_provider(provider,api_key,legacy):
    if provider=='brave':return legacy
    from .orchestrator import WebResearchOrchestrator
    from .deepseek import DeepSeekSearchService,DeepSeekFetchReader
    if provider!='deepseek':raise ValueError('unsupported search provider')
    return WebResearchOrchestrator(search_service=DeepSeekSearchService(),reader=DeepSeekFetchReader(api_key=api_key))
