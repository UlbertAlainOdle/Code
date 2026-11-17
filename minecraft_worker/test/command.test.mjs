import assert from 'node:assert/strict'
import test from 'node:test'

import { matchesBlock, matchesItem, parseCommand } from '../command.mjs'

test('persistent state commands', () => {
  assert.deepEqual(parseCommand('follow Doctor'), { verb: 'follow', args: 'Doctor', raw: 'follow Doctor' })
  assert.equal(parseCommand('unfollow').verb, 'unfollow')
  assert.equal(parseCommand('stop').verb, 'stop')
})

test('task commands and aliases', () => {
  assert.equal(parseCommand('chop 4').args, 4)
  assert.deepEqual(parseCommand('collect iron 3').args, { block: 'iron_ore', count: 3 })
  assert.deepEqual(parseCommand('mine -2 64 9').args, [-2, 64, 9])
  assert.equal(parseCommand('mine nearby').verb, 'mine_nearby')
  assert.equal(parseCommand('mine below').verb, 'mine_below')
  assert.deepEqual(parseCommand('craft oak_planks 8').args, { item: 'oak_planks', count: 8 })
  assert.deepEqual(parseCommand('craft 木板 4').args, { item: 'planks', count: 4 })
  assert.deepEqual(parseCommand('kill zombie 3').args, { target: 'zombie', count: 3 })
  assert.deepEqual(parseCommand('attack nearest').args, { target: 'nearest', count: 1 })
  assert.deepEqual(parseCommand('attack #17275').args, { target: '#17275', count: 1 })
  assert.deepEqual(parseCommand('smelt raw_iron iron_ingot 3 coal 1').args, {
    input: 'raw_iron', output: 'iron_ingot', count: 3, fuel: 'coal', fuelCount: 1,
  })
  assert.deepEqual(parseCommand('brew healing 3').args, { potion: 'healing', count: 3 })
  assert.deepEqual(parseCommand('brew 治疗药水 2').args, { potion: 'healing', count: 2 })
  assert.deepEqual(parseCommand('酿造治疗药水').args, { potion: 'healing', count: 1 })
  assert.deepEqual(parseCommand('请帮我酿造两瓶治疗药水吧').args, { potion: 'healing', count: 2 })
  assert.deepEqual(parseCommand('cauldron fill water').args, { action: 'fill', fluid: 'water' })
  assert.deepEqual(parseCommand('cauldron collect lava').args, { action: 'collect', fluid: 'lava' })
  assert.equal(parseCommand('cancel task').verb, 'cancel_task')
})

test('block matching supports logs and deepslate ores', () => {
  assert.equal(matchesBlock('acacia_log', '_log'), true)
  assert.equal(matchesBlock('deepslate_iron_ore', 'iron_ore'), true)
  assert.equal(matchesBlock('stone', 'iron_ore'), false)
})

test('stone matching is exact (cobblestone goals never grab red_sandstone)', () => {
  assert.equal(matchesBlock('stone', 'stone'), true)
  assert.equal(matchesBlock('red_sandstone', 'stone'), false)
  assert.equal(matchesBlock('smooth_stone', 'stone'), false)
})

test('inventory verbs: inventory / equip / place', () => {
  assert.equal(parseCommand('inventory').verb, 'inventory')
  assert.equal(parseCommand('inv').verb, 'inventory')
  assert.equal(parseCommand('equip wooden_pickaxe').verb, 'equip')
  assert.equal(parseCommand('equip 木镐').args, 'wooden_pickaxe')
  assert.equal(parseCommand('place crafting_table').verb, 'place')
  assert.equal(parseCommand('place 工作台').args, 'crafting_table')
})

test('persistent automation toggle: auto discard on/off', () => {
  assert.deepEqual(parseCommand('auto discard on'), {
    verb: 'auto_discard', args: 'on', raw: 'auto discard on',
  })
  assert.deepEqual(parseCommand('auto_discard off'), {
    verb: 'auto_discard', args: 'off', raw: 'auto_discard off',
  })
})

test('drop by item and count', () => {
  assert.deepEqual(parseCommand('drop stone 3').args, { item: 'stone', count: 3 })
  assert.deepEqual(parseCommand('drop 石头 3').args, { item: 'cobblestone', count: 3 })
  assert.deepEqual(parseCommand('throw dirt all').args, { item: 'dirt', count: 'all' })
  assert.deepEqual(parseCommand('drop rotten_flesh').args, { item: 'rotten_flesh', count: 1 })
  // legacy forms stay unchanged
  assert.equal(parseCommand('drop one').args, 'one')
  assert.equal(parseCommand('drop stack').args, 'stack')
  assert.equal(parseCommand('drop').args, 'one')
})

test('give parses item, count and recipient', () => {
  assert.deepEqual(
    parseCommand('give wooden_sword 1 to Doctor').args,
    { item: 'wooden_sword', count: 1, target: 'Doctor' },
  )
  assert.deepEqual(
    parseCommand('give cobblestone 8').args,
    { item: 'cobblestone', count: 8, target: '' },
  )
  assert.deepEqual(
    parseCommand('give 木剑 to Kurosawa_Jin').args,
    { item: 'wooden_sword', count: 1, target: 'Kurosawa_Jin' },
  )
})

test('matchesItem families', () => {
  assert.equal(matchesItem('oak_log', '_log'), true)
  assert.equal(matchesItem('spruce_planks', 'planks'), true)
  assert.equal(matchesItem('bread', '_log'), false)
})
