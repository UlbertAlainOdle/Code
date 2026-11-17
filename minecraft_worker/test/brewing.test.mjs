import assert from 'node:assert/strict'
import test from 'node:test'

import { brewingPath, normalizePotionName } from '../brewing.mjs'

test('healing is a verified water -> awkward -> healing chain', () => {
  assert.deepEqual(brewingPath('healing'), [
    { input: 'water', output: 'awkward', ingredient: 'nether_wart' },
    { input: 'awkward', output: 'healing', ingredient: 'glistering_melon_slice' },
  ])
})

test('extended and corrupted potion chains remain ordered', () => {
  assert.deepEqual(brewingPath('long_invisibility').map(step => step.output), [
    'awkward', 'night_vision', 'invisibility', 'long_invisibility',
  ])
  assert.deepEqual(brewingPath('strong_harming').map(step => step.output), [
    'awkward', 'healing', 'harming', 'strong_harming',
  ])
})

test('Chinese potion aliases are stable and unsupported potions fail', () => {
  assert.equal(normalizePotionName('治疗药水'), 'healing')
  assert.equal(normalizePotionName('Potion of Strength'), 'strength')
  assert.throws(() => brewingPath('luck'), /暂不支持/)
})

