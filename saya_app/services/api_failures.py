"""Content-free transport diagnostics; provider messages never become QQ evidence."""
import re


def diagnostic(error, *, request_id='', attempt=0, partial=False):
    causes=[];seen=set()
    while error is not None and id(error) not in seen and len(causes)<6:
        seen.add(id(error))
        item={'type':type(error).__name__}
        errno=getattr(error,'errno',None)
        if isinstance(errno,int):item['errno']=errno
        causes.append(item)
        error=error.__cause__ or error.__context__
    return {'code':'provider_transport_failed','request_id':str(request_id)[:64],
        'attempt':int(attempt),'partial':bool(partial),'causes':causes}


def failed_event(name,data):
    return name=='stream_error' or name=='assistant_status' and data.get('phase')=='failed'


def operational_reply(text):
    # An entire historical application error, not a substring in a user's quote.
    return bool(re.fullmatch(r'API stream interrupted: (?:ConnectError: All connection attempts failed|'
        r'(?:ReadTimeout|ConnectTimeout|ReadError|RemoteProtocolError):[^\n]{0,240})',str(text).strip()))


def project_legacy_errors(store,scope,history):
    # Require a matching host delivery. Never remove arbitrary user text or a
    # legitimate answer merely mentioning an exception. Original history stays.
    with store.connect() as db:
        errors={r[0] for r in db.execute("select canonical from qq_deliveries where scope=? and kind='final' order by created desc limit 200",(scope,))
            if operational_reply(r[0])}
    return [line for line in history if not (line.startswith('Saya: ') and line[6:].strip() in errors)]
