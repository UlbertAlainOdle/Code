import assert from 'node:assert/strict'
import test from 'node:test'

import { potionIdentity, potionMatches, potionDescriptorMatches, parseVariantItem, selectPotionStacks } from '../potion.mjs'
import { parseCommand } from '../command.mjs'

function potionItem(name, potionId, { count = 1, customEffects = 0, slot = 0 } = {}) {
  const data = { potionId }
  if (customEffects) data.customEffects = Array.from({ length: customEffects }, () => ({}))
  return {
    name,
    displayName: name,
    count,
    slot,
    components: [{ type: 'potion_contents', data }],
  }
}

test('potionIdentity resolves container, effect and custom count', () => {
  assert.deepEqual(potionIdentity(potionItem('potion', 'healing')), {
    container: 'potion', potion: 'healing', custom_effects: 0,
  })
  assert.deepEqual(potionIdentity(potionItem('splash_potion', 'strong_healing')), {
    container: 'splash_potion', potion: 'strong_healing', custom_effects: 0,
  })
  assert.equal(potionIdentity({ name: 'bread' }), null)
})

test('legacy potionMatches ignores the container (documented gap)', () => {
  assert.equal(potionMatches(potionItem('splash_potion', 'healing'), 'healing'), true)
})

test('potionDescriptorMatches requires the container and vanilla purity', () => {
  const wanted = { container: 'potion', potion: 'healing' }
  assert.equal(potionDescriptorMatches(potionItem('potion', 'healing'), wanted), true)
  assert.equal(potionDescriptorMatches(potionItem('splash_potion', 'healing'), wanted), false)
  assert.equal(potionDescriptorMatches(potionItem('potion', 'night_vision'), wanted), false)
  assert.equal(potionDescriptorMatches(potionItem('potion', 'strong_healing'), wanted), false)
  // Phase 1: custom-effect potions are refused for every automatic action.
  assert.equal(potionDescriptorMatches(potionItem('potion', 'healing', { customEffects: 1 }), wanted), false)
})

test('parseVariantItem understands potion/healing style keys only', () => {
  assert.deepEqual(parseVariantItem('potion/healing'), { base: 'potion', potion: 'healing' })
  assert.deepEqual(parseVariantItem('splash_potion/healing'), { base: 'splash_potion', potion: 'healing' })
  assert.equal(parseVariantItem('potion'), null)
  assert.equal(parseVariantItem('wooden_sword'), null)
  assert.equal(parseVariantItem('potion/'), null)
})

test('selectPotionStacks picks exact stacks and reports availability', () => {
  const items = [
    potionItem('potion', 'night_vision', { slot: 1 }),
    potionItem('potion', 'healing', { slot: 2 }),
    potionItem('potion', 'healing', { slot: 3 }),
    potionItem('splash_potion', 'healing', { slot: 4 }),
  ]
  const picked = selectPotionStacks(items, { base: 'potion', potion: 'healing', count: 2 })
  assert.deepEqual(picked.map(entry => entry.slot), [2, 3])
  const short = selectPotionStacks(items, { base: 'potion', potion: 'healing', count: 5 })
  assert.deepEqual(short.map(entry => entry.slot), [2, 3])
  const splash = selectPotionStacks(items, { base: 'splash_potion', potion: 'healing', count: 1 })
  assert.deepEqual(splash.map(entry => entry.slot), [4])
})

test('give/drop command parsing keeps variant keys intact', () => {
  assert.deepEqual(parseCommand('give potion/healing 1 to Doctor').args, {
    item: 'potion/healing', count: 1, target: 'Doctor',
  })
  assert.deepEqual(parseCommand('drop potion/healing 2').args, {
    item: 'potion/healing', count: 2,
  })
  assert.deepEqual(parseCommand('give splash_potion/healing 1').args, {
    item: 'splash_potion/healing', count: 1, target: '',
  })
})
