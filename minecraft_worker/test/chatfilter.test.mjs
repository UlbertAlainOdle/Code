import assert from 'node:assert/strict'
import test from 'node:test'

import { isPlayerChatPosition, resolveChatSender, stripChatDecoration } from '../chatfilter.mjs'

const PLAYERS = {
  Kurosawa_Jin: { username: 'Kurosawa_Jin', UUID: '11111111-2222-3333-4444-555555555555' },
  Saya: { username: 'Saya', uuid: '99999999-8888-7777-6666-555555555555' },
}

test('only real player-chat positions pass the gate', () => {
  assert.equal(isPlayerChatPosition('chat'), true)
  // systemChat packets: command feedback, join/leave, death messages.
  assert.equal(isPlayerChatPosition('system'), false)
  assert.equal(isPlayerChatPosition('game_info'), false)
  assert.equal(isPlayerChatPosition(undefined), false)
})

test('sender UUID resolves to an online player (either UUID key form)', () => {
  assert.equal(resolveChatSender(PLAYERS, '11111111-2222-3333-4444-555555555555')?.username, 'Kurosawa_Jin')
  assert.equal(resolveChatSender(PLAYERS, '99999999-8888-7777-6666-555555555555')?.username, 'Saya')
  assert.equal(resolveChatSender(PLAYERS, null), null)
  assert.equal(resolveChatSender(PLAYERS, 'not-a-known-uuid'), null)
  assert.equal(resolveChatSender(null, '11111111-2222-3333-4444-555555555555'), null)
})

test('chat decoration is stripped, command feedback is not reinterpreted', () => {
  assert.equal(stripChatDecoration('<Kurosawa_Jin> 我需要猪肉', 'Kurosawa_Jin'), '我需要猪肉')
  assert.equal(stripChatDecoration('Kurosawa_Jin> 我需要猪肉', 'Kurosawa_Jin'), '我需要猪肉')
  // Even if a system line DID slip through with the owner's name in it, the
  // '[name] 已...' form is NOT stripped here - the position gate upstream is
  // what keeps it out; this just documents the decoration contract.
  assert.equal(stripChatDecoration('[Kurosawa_Jin] 已传送回家', 'Kurosawa_Jin'), '[Kurosawa_Jin] 已传送回家')
})
