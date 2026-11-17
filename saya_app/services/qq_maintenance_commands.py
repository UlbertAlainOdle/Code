"""Explicit QQ management for reversible R5 joint source views."""
import re
import sqlite3

from .memory_use import MemoryUseChanged
from .memory_write_operations import fingerprint
from .qq_delivery import clean, semantic_parts
from .qq_memory_access import MemoryAccessDenied
from .qq_responses import ControlReceipt


ACTIONS = {'link', 'link-confirm', 'link-show', 'link-result', 'link-undo'}
ARCHIVE_ACTIONS = {'archive', 'archive-confirm', 'archive-show', 'archive-result', 'archive-undo'}
_RELATIONS = {
    'parallel_review': '并列核对，保留各条陈述',
    'duplicate_evidence': '共同原始来源按一份计数',
    'duplicate_claim': '相近陈述，分别保留出处',
    'complementary': '互补陈述，分别保留条件',
}
_STATUS = {
    'active': '联合视图已确认。',
    'pending': '联合视图等待确认。',
    'preview_not_delivered': '完整预览未核实送达，请重新发起联合视图预览。',
    'undone': '已撤销联合视图。',
    'cancelled': '已取消待确认预览。',
    'expired': '预览已过期，请重新预览。',
    'invalidated': '关联或预览已失效，请重新核对来源。',
    'snapshot_changed': '目标不可用、受保护或版本变化，请重新读取。',
    'source_capacity_exceeded': '联合来源超过上限，请缩小范围。',
    'member_already_linked': '已有原编号处于其他有效联合视图，请先查看或撤销。',
    'separate_review_required': '这两个原编号需要分别核对。',
    'operation_capacity_exceeded': '联合视图操作账本已满。',
    'unavailable': '当前范围没有可用的联合视图或操作。',
}


class MaintenanceSourceReceipt(ControlReceipt):
    """Send revalidated quotes once and keep only transport receipt IDs."""

    def __new__(cls, body, parts, valid, delivered):
        receipt = super().__new__(cls, body)
        receipt.parts = parts
        receipt.valid = valid
        receipt.delivered = delivered
        return receipt


def _body(result, prefix):
    pending = result['status'] == 'pending'
    lines = ['联合视图预览（待确认）' if pending else '联合视图',
             '整理方式：' + _RELATIONS[result['relation']],
             '原编号：' + '、'.join(result['members']),
             f"当前有效来源 {result['source_count']} 份。"]
    for index, copies in enumerate(result['sources'], start=1):
        source = copies[0]['source']
        ids = '、'.join(dict.fromkeys(copy['record_id'] for copy in copies))
        lines.extend((f'来源 {index} · 发言者 QQ {source.author} · {source.mode or "原始记录"} · {ids}',
                      source.text))
    if pending:
        lines.append(f"核对完整来源后，十分钟内直接发送：{prefix} link-confirm {result['id']}")
    else:
        lines.append(f"操作编号：{result['id']}")
    return '\n'.join(lines)


def _parts(body, settings):
    rendered = str(ControlReceipt(body))
    if clean(rendered) != rendered:
        raise ValueError('maintenance_source_not_renderable')
    target = min(1500, settings.chunk_chars) if settings.delivery_mode == 'legacy' else 1500
    if (settings.delivery_mode == 'legacy' and len(rendered) > settings.max_reply_chars):
        raise ValueError('maintenance_receipt_capacity')
    parts = semantic_parts(rendered, target)
    if not parts or len(parts) > settings.reply_max_messages:
        raise ValueError('maintenance_receipt_capacity')
    if re.sub(r'\s+', '', ''.join(parts)) != re.sub(r'\s+', '', rendered):
        raise ValueError('maintenance_source_not_renderable')
    return tuple(parts)


def _source_receipt(links, result, prefix, settings):
    body = _body(result, prefix)
    parts = _parts(body, settings)
    def valid():
        try:
            current = links.result(result['id'])
        except (MemoryAccessDenied, PermissionError, MemoryUseChanged):
            return False
        return current['status'] == result['status'] and current.get('members') == result['members']
    delivered = (lambda: links.mark_preview_delivered(result['id'])) if result['status'] == 'pending' else None
    return MaintenanceSourceReceipt(body, parts, valid, delivered)


def command(links, message, args, *, prefix, settings):
    action, *params = args
    expected = 2 if action == 'link' else 1
    if len(params) != expected:
        return ControlReceipt(f'用法：{prefix} link 原编号 另一个原编号；{prefix} link-show 原编号；'
                              f'{prefix} link-confirm / link-result / link-undo 操作编号。')
    if (message.reply_to or message.forward_present or message.media_types or
            message.kind != 'message' or getattr(message, 'quote_context', None)):
        return ControlReceipt('请直接输入联合视图命令。引用、转发和附件不作为操作授权。')
    try:
        if action == 'link':
            links.authorize(True, tuple(params))
            first, second = map(links.load, params)
            if not first or not second:
                return ControlReceipt(_STATUS['snapshot_changed'])
            operation = fingerprint(['r5_qq_link', message.key])[:32]
            result = links.prepare(operation, *params, anchor_version=first.version,
                                   candidate_version=second.version, comparison=True,
                                   check_preview=lambda preview: _parts(_body(preview, prefix), settings))
            if result['status'] == 'pending':
                return _source_receipt(links, result, prefix, settings)
        elif action == 'link-show':
            result = links.for_member(params[0])
            if result['status'] == 'active':
                return _source_receipt(links, result, prefix, settings)
        else:
            handler = {'link-confirm': links.confirm, 'link-result': links.result, 'link-undo': links.undo}[action]
            result = handler(params[0])
        text = _STATUS.get(result['status'], _STATUS['unavailable'])
        if result.get('id'):
            text += f" 操作编号：{result['id']}。"
        if result['status'] == 'active':
            text += f" 可用 {prefix} link-show 原编号 查看当前来源。"
        return ControlReceipt(text)
    except (MemoryAccessDenied, PermissionError):
        return ControlReceipt('当前身份、管理开关或范围不允许这项联合视图操作。')
    except MemoryUseChanged:
        return ControlReceipt('来源状态已变化，请重新核对原编号。')
    except (OSError, sqlite3.Error):
        if action in {'link-confirm', 'link-undo'}:
            return ControlReceipt(f'操作结果待核对，请用 {prefix} link-result {params[0]} 查询原编号。')
        return ControlReceipt('联合视图账本暂不可读取，请稍后核对原编号。')
    except ValueError as error:
        if str(error) in {'maintenance_receipt_capacity', 'maintenance_source_not_renderable', 'unbreakable_token'}:
            return ControlReceipt('当前QQ投递不能完整显示这份来源，请分别查看原条目。')
        return ControlReceipt('联合视图参数无效或来源已变化，请核对原编号。')


def _archive_body(result):
    pending = result['status'] == 'pending'
    lines = ['重复记忆归档预览（待确认）' if pending else '重复记忆归档来源',
             f"保留编号：{result['keep']}。归档编号：{result['archive']}。"]
    sources = [copies[0]['source'] for copies in result['sources']] if pending else result['sources']
    for source in sources:
        lines.extend((f'原始来源 · 发言者 QQ {source.author} · {source.mode or "原始记录"}', source.text))
    if pending:
        lines.extend(('只归档重复副本，保留两个原编号。来源与权限仍有效时可撤销。',
                      f"核对完整预览后，十分钟内直接发送：/memory archive-confirm {result['id']}"))
    else:
        lines.append(f"操作编号：{result['id']}。")
    return '\n'.join(lines)


def archive_command(archive, message, args, *, settings):
    from .qq_memory_management import QQMemoryReceipt

    action, *params = args
    if len(params) != (2 if action == 'archive' else 1):
        return ControlReceipt('用法：/memory archive 保留编号 归档编号；archive-confirm、archive-result、archive-undo 使用操作编号；archive-show 使用原编号。')
    if (message.reply_to or message.forward_present or message.media_types or
            message.kind != 'message' or getattr(message, 'quote_context', None)):
        return ControlReceipt('请直接输入归档命令。引用、转发和附件不作为操作授权。')
    try:
        if action == 'archive':
            operation = fingerprint(['r5_qq_archive', message.key])[:32]
            result = archive.prepare(operation, *params, check_preview=lambda p: _parts(_archive_body(p), settings))
            if result['status'] == 'pending':
                body = _archive_body(result)
                def valid():
                    try:
                        return archive.pending(operation).get('plan_version') == result['plan_version']
                    except (MemoryAccessDenied, MemoryUseChanged):
                        return False
                return MaintenanceSourceReceipt(body, _parts(body, settings), valid,
                                                lambda: archive.mark_delivered(operation))
        elif action == 'archive-show':
            result = archive.for_member(params[0])
            if result['status'] == 'archived':
                body = _archive_body(result)
                def valid():
                    try:
                        current = archive.for_member(params[0])
                        return (current.get('id'), current.get('version')) == (result['id'], result['version'])
                    except (MemoryAccessDenied, MemoryUseChanged):
                        return False
                return MaintenanceSourceReceipt(body, _parts(body, settings), valid, None)
        else:
            handler = {'archive-confirm': archive.confirm, 'archive-result': archive.result, 'archive-undo': archive.undo}[action]
            result = handler(params[0])
        text = {
            'archived': '已归档重复记忆。可按两个原编号用 /memory archive-show 查看当前来源。',
            'undone': '已撤销归档，原编号已恢复使用。',
            'pending': '归档等待确认。', 'preview_not_delivered': '完整归档预览未核实送达。',
            'unknown': '归档结果待核对，请用 /memory archive-result 查询原操作编号。',
            'invalidated': '来源或权限已变化，本次未恢复。',
            'archive_capacity_exceeded': '目标层已达容量上限，本次未修改。',
            'member_already_linked': '其中一个编号已属于另一项归档，请先核对原操作。',
            'separate_review_required': '两条记录需要分别核对，未归档。',
            'unavailable': '当前范围没有可用的归档视图或操作。',
        }.get(result['status'], _STATUS.get(result['status'], _STATUS['snapshot_changed']))
        return QQMemoryReceipt(text + (f" 操作编号：{result['id']}。" if 'id' in result else ''))
    except (MemoryAccessDenied, PermissionError):
        return ControlReceipt('当前身份、管理开关或范围不允许这项归档操作。')
    except MemoryUseChanged:
        return ControlReceipt('来源状态已变化，请重新核对原编号。')
    except (OSError, sqlite3.Error):
        if action in {'archive-confirm', 'archive-undo'}:
            return QQMemoryReceipt(f'归档结果待核对，请用 /memory archive-result {params[0]} 查询原操作编号。')
        return ControlReceipt('归档账本暂不可读取，请稍后核对原编号。')
    except ValueError as error:
        if str(error) in {'maintenance_receipt_capacity', 'maintenance_source_not_renderable', 'unbreakable_token'}:
            return ControlReceipt('当前QQ投递不能完整显示归档来源，请分别查看原条目。')
        return ControlReceipt('归档参数无效或来源已变化，请核对原编号。')
