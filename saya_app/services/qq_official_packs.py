"""A parallel catalogue of observed official QQ market-face pack elements.

The pinned OneBot has market-face read/send support but no installed-pack list
API. Never claim that observed elements enumerate a complete installed pack.
"""
import asyncio,hashlib,json,re,time
import httpx
from .qq_favorites import QQFavorites,FavoriteConflict


class QQOfficialPacks(QQFavorites):
    table='qq_official_pack_catalogue'

    @staticmethod
    def image_url(url):
        # Constructed from a verified official emoji ID, not a message URL.
        if not isinstance(url,str) or not re.fullmatch(r'https://gxh\.vip\.qq\.com/club/item/parcel/item/[a-f0-9]{2}/[a-f0-9]{32}/raw300\.gif',url):
            raise ValueError('官方表情图片地址无效。')
        return url

    def state(self):
        state=super().state();_,items=self._load();packages={}
        for item in items:
            pack=item['package_id'];packages.setdefault(pack,[]).append(item['id'])
        state.update(source='official',packages=[{'id':p,'label':'官方表情包 '+p,'item_ids':ids} for p,ids in packages.items()],
            inventory_complete=False,discovery='verified_owner_private_message_samples',
            limitation='当前协议不能列出已添加的全部官方包；这里只显示主人实际发来并核验过的包内表情。')
        for projected,item in zip(state['items'],items):projected['package_id']=item['package_id']
        return state

    @staticmethod
    def official_element(segment):
        if not isinstance(segment,dict) or segment.get('type') not in {'image','mface'}:return None
        data=segment.get('data')
        if not isinstance(data,dict):return None
        emoji=str(data.get('emoji_id') or '').lower();pack=str(data.get('emoji_package_id') or '')
        key=data.get('key')
        if not re.fullmatch(r'[a-f0-9]{32}',emoji) or not re.fullmatch(r'[1-9][0-9]{0,15}',pack):return None
        if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_+=/\-]{1,512}',key):return None
        return {'id':hashlib.sha256((pack+':'+emoji).encode()).hexdigest()[:32],'package_id':pack,
            'emoji_id':emoji,'key':key,'url':f'https://gxh.vip.qq.com/club/item/parcel/item/{emoji[:2]}/{emoji}/raw300.gif',
            'label':('官方包 '+pack+' · '+str(data.get('summary') or '[官方表情]'))[:120],
            'selected':False,'meaning':'','analyzed':False}

    async def refresh(self,expected_revision=None,*,owner_id=''):
        from .qq_bridge import QQMessage
        from .qq_quotes import _same_conversation
        if owner_id not in self.bridge.settings.owner_ids:raise PermissionError('owner_required')
        revision,items=self._load()
        if expected_revision is not None and revision!=expected_revision:raise FavoriteConflict('官方包清单已变化，请重新读取。')
        by_id={x['id']:x for x in items};candidates=[];seen=set()
        with self.store.connect() as db:
            rows=db.execute('select origin from qq_deliveries order by created desc limit 128').fetchall()
        for row in rows:
            try:origin=json.loads(row[0])
            except (ValueError,TypeError):continue
            if not isinstance(origin,dict):continue
            if origin.get('bot_id')!=self.bridge.settings.bot_id or origin.get('user_id')!=owner_id or origin.get('group_id'):continue
            mid=origin.get('message_id')
            if not isinstance(mid,str) or not re.fullmatch('-?[0-9]{1,20}',mid) or mid in seen:continue
            seen.add(mid);candidates.append(origin)
            if len(candidates)>=16:break
        call=getattr(self.bridge.client,'call_bounded',self.bridge.client.call)
        deadline=time.monotonic()+30;unavailable=0;checked=0
        for origin in candidates:
            remaining=deadline-time.monotonic()
            if remaining<=0:break
            try:data=await asyncio.wait_for(call('get_msg',{'message_id':origin['message_id']}),min(8,remaining))
            except (TimeoutError,httpx.HTTPError,RuntimeError):
                unavailable+=1;continue
            checked+=1
            scope=origin['bot_id']+':private:'+owner_id
            msg=QQMessage('scan',scope,origin['bot_id'],owner_id,'',origin['message_id'],'',False,reply_to=origin['message_id'])
            if not _same_conversation(data,msg,self.store) or str(data.get('message_id'))!=origin['message_id']:continue
            if str(data.get('user_id') or (data.get('sender') or {}).get('user_id'))!=owner_id:continue
            raw=data.get('message')
            if not isinstance(raw,list) or len(raw)>256:continue
            for segment in raw:
                item=self.official_element(segment)
                if item is None:continue
                previous=by_id.get(item['id'])
                if previous and previous['key']==item['key']:
                    item.update(selected=previous['selected'],meaning=previous['meaning'],analyzed=previous['analyzed'])
                if previous or len(by_id)<48:by_id[item['id']]=item
        # This is a partial observation, not full inventory: never remove an old
        # selection merely because it was absent from this small message sample.
        return self._commit(revision,sorted(by_id.values(),key=lambda x:(int(x['package_id']),x['id'])))|{
            'checked_messages':checked,'unavailable_messages':unavailable}
