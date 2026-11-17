import assert from 'node:assert/strict'
import test from 'node:test'

import { parseCommand } from '../command.mjs'
import { chooseWeapon, INJURED_HEALTH_THRESHOLD, isWieldableTool, WEAPON_DAMAGE, weaponCandidates } from '../weapons.mjs'

function weapon(name, { maxDurability = 250, durabilityUsed = 0, count = 1 } = {}) {
  return { name, maxDurability, durabilityUsed, count, type: 1, metadata: 0, slot: 0, nbt: null }
}

test('healthy economy mode: fewest hits, then cheapest material', () => {
  // Cow has 10 HP: wooden sword (4 dmg) needs 3 hits, iron sword (6) and
  // diamond sword (7) need 2 - cheapest 2-hit weapon is iron.
  const choice = chooseWeapon(
    [weapon('wooden_sword'), weapon('iron_sword'), weapon('diamond_sword')],
    { health: 20, targetHp: 10 },
  )
  assert.equal(choice.name, 'iron_sword')
})

test('healthy mode prefers cheap weapons for weak targets', () => {
  // Chicken has 4 HP: every sword one-shots it, so the cheapest material wins.
  const choice = chooseWeapon(
    [weapon('netherite_sword'), weapon('stone_sword'), weapon('diamond_sword')],
    { health: 20, targetHp: 4 },
  )
  assert.equal(choice.name, 'stone_sword')
})

test('injured mode: maximum damage regardless of durability cost', () => {
  const choice = chooseWeapon(
    [weapon('stone_sword'), weapon('diamond_sword'), weapon('iron_axe'), weapon('netherite_axe')],
    { health: 6, targetHp: 20 },
  )
  assert.equal(choice.name, 'netherite_axe')   // 10 damage beats everything
})

test('injured damage ties still prefer the cheaper material', () => {
  const choice = chooseWeapon(
    [weapon('iron_axe'), weapon('diamond_axe')],   // both 9 damage
    { health: 6, targetHp: 20 },
  )
  assert.equal(choice.name, 'iron_axe')
})

test('injured threshold matches the documented policy', () => {
  const weapons = [weapon('stone_sword'), weapon('diamond_sword')]
  assert.equal(chooseWeapon(weapons, { health: INJURED_HEALTH_THRESHOLD, targetHp: 20 }).name, 'diamond_sword')
  assert.equal(chooseWeapon(weapons, { health: INJURED_HEALTH_THRESHOLD + 1, targetHp: 4 }).name, 'stone_sword')
})

test('durability tie-breaking prefers the fresher copy', () => {
  const choice = chooseWeapon(
    [weapon('iron_sword', { durabilityUsed: 200 }), weapon('iron_sword', { durabilityUsed: 10 })],
    { health: 20, targetHp: 20 },
  )
  assert.equal(choice.durability, 240)
})

test('no weapons carried returns null; non-weapons ignored', () => {
  assert.equal(chooseWeapon([weapon('bread'), weapon('bucket')], { health: 20 }), null)
  assert.deepEqual(
    weaponCandidates([weapon('bread'), weapon('iron_sword')]).map(c => c.name),
    ['iron_sword'],
  )
})

test('every attack-relevant weapon has a damage value', () => {
  for (const name of ['wooden_sword', 'stone_sword', 'iron_sword', 'golden_sword', 'diamond_sword', 'netherite_sword', 'iron_axe', 'netherite_axe']) {
    assert.ok(WEAPON_DAMAGE[name] > 0, name)
  }
})

test('milk command parsing', () => {
  assert.equal(parseCommand('milk').verb, 'milk')
  assert.equal(parseCommand('milk 2').args, 2)
  // Chinese phrases are normalized by Python's parse_intent before the pipe;
  // the worker only sees canonical commands (same boundary as equip).
  assert.equal(parseCommand('挤牛奶').verb, 'say')
})

test('isWieldableTool: only weapons/tools are sheathed after combat/mining', () => {
  assert.equal(isWieldableTool('iron_sword'), true)
  assert.equal(isWieldableTool('diamond_pickaxe'), true)
  assert.equal(isWieldableTool('iron_axe'), true)          // tool AND weapon
  assert.equal(isWieldableTool('minecraft:trident'), true)
  assert.equal(isWieldableTool('mace'), true)
  assert.equal(isWieldableTool('bread'), false)
  assert.equal(isWieldableTool('bucket'), false)
  assert.equal(isWieldableTool('dirt'), false)
  assert.equal(isWieldableTool(''), false)
})
