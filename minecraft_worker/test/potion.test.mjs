import test from 'node:test'
import assert from 'node:assert/strict'

import {
  installVanillaPotionRegistry,
  loadPotionRegistryPacket,
  potionIdentity,
  potionMatches,
  VANILLA_POTIONS_1_21_5,
} from '../potion.mjs'

test('1.21.5 fallback follows the Java registry declaration order only', () => {
  const names = new Map([[99, 'stale']])
  assert.equal(installVanillaPotionRegistry('1.21.5', names), true)
  assert.equal(names.size, 46)
  assert.equal(names.get(0), 'water')
  assert.equal(names.get(3), 'awkward')
  assert.equal(names.get(24), 'healing')
  assert.equal(names.get(45), 'infested')
  assert.deepEqual([...names.values()], VANILLA_POTIONS_1_21_5)

  const unsupported = new Map([[7, 'keep_me']])
  assert.equal(installVanillaPotionRegistry('1.21.4', unsupported), false)
  assert.equal(unsupported.get(7), 'keep_me')
})

test('dynamic potion registry maps protocol ids to stable names', () => {
  const names = loadPotionRegistryPacket({
    id: 'minecraft:potion',
    entries: [{ key: 'minecraft:water' }, { key: 'minecraft:awkward' }, { key: 'minecraft:healing' }],
  })
  assert.equal(names.get(2), 'healing')
  const item = {
    name: 'potion',
    components: [{ type: 'potion_contents', data: { potionId: 2, customEffects: [] } }],
  }
  assert.deepEqual(potionIdentity(item, names), {
    container: 'potion', potion: 'healing', custom_effects: 0,
  })
  assert.equal(potionMatches(item, 'minecraft:healing', names), true)
})

test('plain component-less drinkable potion is conservatively a water bottle', () => {
  assert.equal(potionIdentity({ name: 'potion', components: [] }).potion, 'water')
  assert.equal(potionIdentity({ name: 'glass_bottle' }), null)
})

test('legacy Potion NBT remains readable', () => {
  const item = {
    name: 'splash_potion',
    nbt: { value: { Potion: { value: 'minecraft:strong_healing' } } },
  }
  assert.equal(potionIdentity(item).potion, 'strong_healing')
})
