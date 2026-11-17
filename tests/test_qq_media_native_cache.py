"""Regressions for real Windows/NapCat media representations, without QQ sends."""
import asyncio
from unittest.mock import patch

import pytest

from saya_app.services.qq_media import QQMedia,MediaUnavailable,_image_tokens,_native_cache_roots
from test_qq_media import lab,configure,CREDS


def test_real_napcat_punctuation_and_unicode_image_basenames(lab):
    _,media,msg,client,_=lab;configure(media)
    name=']]K`AZZ}{LO%@$中文图片.png'
    client.raw['message'][-1]['data']['file']=name
    result=asyncio.run(media.prepare(msg,CREDS,'api'))
    assert result['attachments']
    assert client.calls[-1]==('get_image',{'file':name})


@pytest.mark.parametrize('name',['http://example/image','file:///C:/image','C:\\image.png',
    '../image','..\\image','base64:AAAA','image\x00.png','image\n.png','//server/image'])
def test_image_basename_compatibility_still_blocks_paths_and_schemes(name):
    with pytest.raises(MediaUnavailable):_image_tokens([{'type':'image','data':{'file':name}}])


def test_redirected_documents_only_trusts_bot_media_and_napcat_temp(lab,tmp_path):
    bridge,_,_,_,_=lab
    documents=tmp_path/'OneDrive'/'文档'
    install={'bot_id':bridge.settings.bot_id,'profile_dir':tmp_path/'install-profile','install_dir':tmp_path/'install'}
    with patch('saya_app.services.qq_media._documents_directory',return_value=documents), \
            patch('saya_app.services.qq_napcat.load_install',return_value=install):
        media=QQMedia(bridge,tmp_path)
        roots=_native_cache_roots(bridge.settings.bot_id)
        for directory in roots:
            directory.mkdir(parents=True,exist_ok=True)
            f=directory/'sample.bin';f.write_bytes(b'actual protocol download')
            assert media._read_bytes({'file':str(f)},100)==b'actual protocol download'
        for relative in ['personal.txt','Tencent Files/other/nt_qq/nt_data/Pic/private.png',
                'Tencent Files/'+bridge.settings.bot_id+'/nt_qq/nt_data/log/private.txt',
                'Tencent Files/'+bridge.settings.bot_id+'/nt_qq/nt_data/Emoji/personal_emoji/private.png',
                'Tencent Files/other/nt_qq/nt_data/Emoji/emoji-recv/private.png']:
            f=documents/relative;f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(b'secret')
            with pytest.raises(MediaUnavailable,match='cache_path_outside'):
                media._read_bytes({'file':str(f)},100)
        f=roots[0]/'large.bin';f.write_bytes(b'x'*101)
        with pytest.raises(MediaUnavailable,match='attachment_too_large'):media._read_bytes({'file':str(f)},100)


def test_protocol_image_name_mismatch_is_not_decoded(lab):
    _,media,_,_,path=lab
    with pytest.raises(MediaUnavailable,match='image_source_mismatch'):
        media._read_image({'file':str(path),'file_name':'different.png'},media.state()['values'],'expected.png')


def test_image_sent_as_document_gets_specific_recovery_hint(lab):
    _,media,msg,client,_=lab;configure(media,documents_enabled=True)
    from dataclasses import replace
    client.raw['message']=[{'type':'file','data':{'file':'image.png','file_id':'FILE_UUID'}}]
    result=asyncio.run(media.prepare(replace(msg,media_types=('file',)),CREDS,'api'))
    assert 'QQ图片消息' in result['status'] and not result['documents']
    assert len(client.calls)==1


def test_native_cache_roots_cannot_select_invalid_account(tmp_path):
    with patch('saya_app.services.qq_media._documents_directory',return_value=tmp_path):
        assert not _native_cache_roots('../another')
        assert not _native_cache_roots('')


def test_native_cache_junction_cannot_escape_allowed_root(lab,tmp_path):
    bridge,_,_,_,_=lab;documents=tmp_path/'Documents';outside=tmp_path/'outside';outside.mkdir()
    secret=outside/'secret.bin';secret.write_bytes(b'secret')
    with patch('saya_app.services.qq_media._documents_directory',return_value=documents), \
            patch('saya_app.services.qq_napcat.load_install',return_value={'bot_id':bridge.settings.bot_id,'profile_dir':tmp_path/'profile','install_dir':tmp_path/'install'}):
        root=_native_cache_roots(bridge.settings.bot_id)[0];root.mkdir(parents=True)
        link=root/'escape'
        # A directory junction needs no Windows symlink privilege.
        import os
        if os.name=='nt':
            try:link.symlink_to(outside,target_is_directory=True)
            except OSError:
                with pytest.raises(MediaUnavailable,match='cache_path_outside'):
                    QQMedia(bridge,tmp_path)._read_bytes({'file':str(root/os.path.relpath(secret,root))},100)
                return
        else:link.symlink_to(outside,target_is_directory=True)
        with pytest.raises(MediaUnavailable,match='cache_path_outside'):
            QQMedia(bridge,tmp_path)._read_bytes({'file':str(link/'secret.bin')},100)
