import assert from 'node:assert/strict'
import test from 'node:test'

import { parseCommand } from '../command.mjs'

test('chest find parses in its phrase variants', () => {
  for (const text of ['chest find', 'chest', 'chest list', 'chest nearby']) {
    const parsed = parseCommand(text)
    assert.equal(parsed.verb, 'chest_find', text)
  }
  assert.equal(parseCommand('chest junk'), null)
})

test('deposit defaults to all and accepts a bounded count', () => {
  assert.deepEqual(parseCommand('deposit iron_ingot').args,
    { item: 'iron_ingot', count: 'all' })
  assert.deepEqual(parseCommand('deposit iron_ingot all').args,
    { item: 'iron_ingot', count: 'all' })
  assert.deepEqual(parseCommand('deposit raw_iron 12').args,
    { item: 'raw_iron', count: 12 })
  // count over the ceiling clamps instead of vanishing
  assert.equal(parseCommand('deposit dirt 99999').args.count, 640)
  assert.equal(parseCommand('deposit'), null)
})

test('withdraw defaults to one item and supports all', () => {
  assert.deepEqual(parseCommand('withdraw bread').args,
    { item: 'bread', count: 1 })
  assert.deepEqual(parseCommand('withdraw bread 5').args,
    { item: 'bread', count: 5 })
  assert.deepEqual(parseCommand('withdraw bread all').args,
    { item: 'bread', count: 'all' })
  assert.equal(parseCommand('withdraw'), null)
})

test('store aliases onto deposit', () => {
  assert.equal(parseCommand('store cobblestone 8').verb, 'chest_deposit')
  assert.deepEqual(parseCommand('store cobblestone 8').args,
    { item: 'cobblestone', count: 8 })
})

test('item names normalize through the same pipeline as collect', () => {
  assert.equal(parseCommand('deposit minecraft:iron_ingot').args.item, 'iron_ingot')
})
