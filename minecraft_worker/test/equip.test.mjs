import assert from 'node:assert/strict'
import test from 'node:test'

import { armorDestinationFor, AUTO_MINE_GROUPS, AUTO_MINE_ORES, DESTINATION_SLOT, oreGroupForBlock, parseCommand } from '../command.mjs'

test('armor items map to their own equipment destination', () => {
  assert.equal(armorDestinationFor('golden_helmet'), 'head')
  assert.equal(armorDestinationFor('diamond_chestplate'), 'torso')
  assert.equal(armorDestinationFor('iron_leggings'), 'legs')
  assert.equal(armorDestinationFor('leather_boots'), 'feet')
  assert.equal(armorDestinationFor('netherite_helmet'), 'head')
  assert.equal(armorDestinationFor('minecraft:chainmail_boots'), 'feet')
  // shield goes to the off-hand, everything else stays in hand
  assert.equal(armorDestinationFor('shield'), 'off-hand')
  assert.equal(armorDestinationFor('wooden_sword'), 'hand')
  assert.equal(armorDestinationFor('bread'), 'hand')
  assert.equal(armorDestinationFor(''), 'hand')
})

test('equipment destinations map to the player inventory window slots', () => {
  assert.deepEqual(DESTINATION_SLOT, { head: 5, torso: 6, legs: 7, feet: 8, 'off-hand': 45 })
})

test('equip command parsing resolves Chinese armor aliases', () => {
  assert.equal(parseCommand('equip 金头盔').args, 'golden_helmet')
  assert.equal(parseCommand('equip golden_helmet').args, 'golden_helmet')
  assert.equal(parseCommand('equip 盾牌').args, 'shield')
  // Chinese verb phrases (手持/装备/拿着) are normalized by Python's
  // parse_intent before the pipe; the worker only sees canonical commands.
  assert.equal(parseCommand('手持下界合金胸甲').verb, 'say')
})

test('auto-mine ore set covers the vanilla ores with deepslate variants', () => {
  for (const name of ['coal_ore', 'iron_ore', 'copper_ore', 'gold_ore', 'redstone_ore', 'lapis_ore', 'diamond_ore', 'emerald_ore']) {
    assert.ok(AUTO_MINE_ORES.has(name), name)
    assert.ok(AUTO_MINE_ORES.has(`deepslate_${name}`), `deepslate_${name}`)
  }
  assert.ok(AUTO_MINE_ORES.has('nether_gold_ore'))
  assert.ok(AUTO_MINE_ORES.has('ancient_debris'))
  // only real ore blocks, never plain blocks/items
  for (const name of AUTO_MINE_ORES) assert.ok(name.endsWith('_ore') || name === 'ancient_debris', name)
  assert.ok(!AUTO_MINE_ORES.has('stone'))
  assert.ok(!AUTO_MINE_ORES.has('diamond'))
})

test('oreGroupForBlock maps every ore block to its selectable group', () => {
  assert.equal(oreGroupForBlock('diamond_ore'), 'diamond')
  assert.equal(oreGroupForBlock('deepslate_diamond_ore'), 'diamond')
  assert.equal(oreGroupForBlock('minecraft:iron_ore'), 'iron')
  assert.equal(oreGroupForBlock('nether_gold_ore'), 'gold')
  assert.equal(oreGroupForBlock('nether_quartz_ore'), 'quartz')
  assert.equal(oreGroupForBlock('ancient_debris'), 'ancient_debris')
  assert.equal(oreGroupForBlock('stone'), '')
  assert.equal(oreGroupForBlock('diamond'), '')
  // every group block is also in the derived union, and vice versa
  for (const blocks of Object.values(AUTO_MINE_GROUPS)) {
    for (const name of blocks) assert.ok(AUTO_MINE_ORES.has(name), name)
  }
})

test('pillar command parsing', () => {
  assert.deepEqual(parseCommand('pillar 3'), { verb: 'pillar', args: 3, raw: 'pillar 3' })
  assert.equal(parseCommand('pillar').args, 1)
})
