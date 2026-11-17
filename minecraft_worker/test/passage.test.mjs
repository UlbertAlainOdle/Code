import assert from 'node:assert/strict'
import test from 'node:test'

import { enhanceMovementsForPassages, isPassageBlockName } from '../passage.mjs'
import { createRequire } from 'node:module'

const require = createRequire(import.meta.url)
const { Movements } = require('mineflayer-pathfinder').default || require('mineflayer-pathfinder')
const mcData = require('minecraft-data')('1.21.5')
// prismarine-block attaches collision shapes onto the shared registry blocks
// (mineflayer normally does this at startup); load it before building fakes.
require('prismarine-block')('1.21.5')

// --- fake world built from the real 1.21.5 registry -------------------------
function makeBlock(name, properties, shapes) {
  const entry = mcData.blocksByName[name]
  assert.ok(entry, `registry is missing ${name}`)
  return {
    name,
    type: entry.id,
    boundingBox: (shapes && shapes.length) ? 'block' : 'empty',
    shapes: shapes || [],
    properties: properties || {},
    position: null,
  }
}

const SHAPES = {
  air: [],
  doorClosed: [[0, 0, 0, 1, 1, 0.1875]],
  doorOpen: [[0, 0, 0, 0.1875, 1, 1]],
  trapdoorClosed: [[0, 0, 0, 1, 0.1875, 1]],
  trapdoorOpen: [[0, 0, 0, 1, 0.1875, 1]], // open panel against the wall
  gateClosed: [[0, 0, 0.375, 1, 1.5, 0.625]],
  gateOpen: [[0, 0, 0.375, 1, 1.5, 0.625]],
}

// x = column index; each column holds one block at y=64 (the tested spot).
// The real Movements calls bot.blockAt(Vec3, false).
function makeFakeBot(columns) {
  const blockAt = (vec) => {
    const y = Math.floor(vec.y)
    const x = Math.floor(vec.x)
    if (y !== 64) return makeBlock('air', {}, SHAPES.air)
    return columns[x] || makeBlock('air', {}, SHAPES.air)
  }
  return {
    registry: mcData,
    blockAt,
  }
}

function createTestMovement(fakeBot) {
  const movements = new Movements(fakeBot)
  return enhanceMovementsForPassages(fakeBot, movements)
}

const fakeBot = makeFakeBot({
  1: makeBlock('oak_door', { open: false, half: 'lower', facing: 'north', hinge: 'left' }, SHAPES.doorClosed),
  2: makeBlock('oak_door', { open: false, half: 'upper', facing: 'north', hinge: 'left' }, SHAPES.doorClosed),
  3: makeBlock('oak_door', { open: true, half: 'lower', facing: 'north', hinge: 'left' }, SHAPES.doorOpen),
  4: makeBlock('oak_door', { open: true, half: 'upper', facing: 'north', hinge: 'left' }, SHAPES.doorOpen),
  5: makeBlock('iron_door', { open: false, half: 'lower', facing: 'north', hinge: 'left' }, SHAPES.doorClosed),
  6: makeBlock('oak_trapdoor', { open: false, half: 'bottom', facing: 'north' }, SHAPES.trapdoorClosed),
  7: makeBlock('oak_trapdoor', { open: true, half: 'bottom', facing: 'north' }, SHAPES.trapdoorOpen),
  8: makeBlock('iron_trapdoor', { open: false, half: 'bottom', facing: 'north' }, SHAPES.trapdoorClosed),
  9: makeBlock('oak_fence_gate', { open: false, facing: 'north', in_wall: false }, SHAPES.gateClosed),
  10: makeBlock('oak_fence_gate', { open: true, facing: 'north', in_wall: false }, SHAPES.gateOpen),
  11: makeBlock('stone', {}, [[0, 0, 0, 1, 1, 1]]),
})

const movements = createTestMovement(fakeBot)

function blockAt(x, dy = 0) {
  return movements.getBlock({ x, y: 64, z: 0 }, 0, dy, 0)
}

test('closed wooden door: openable, solid (the useOne flow opens it)', () => {
  const lower = blockAt(1)
  assert.equal(lower.openable, true)
  assert.equal(lower.physical, true)
  assert.equal(lower.safe, false)
})

test('closed upper door half: free space, never broken', () => {
  const upper = blockAt(2)
  assert.equal(upper.physical, false)
  assert.equal(upper.safe, true)
})

test('open wooden door: walkable, not re-activated (would close it)', () => {
  const lower = blockAt(3)
  assert.equal(lower.physical, false)
  assert.equal(lower.safe, true)
  assert.equal(lower.openable, false)
  assert.equal(lower.height, 64) // no phantom step-up from the panel
  const upper = blockAt(4)
  assert.equal(upper.physical, false)
  assert.equal(upper.safe, true)
})

test('iron door: stays solid and never openable', () => {
  const lower = blockAt(5)
  assert.equal(lower.physical, true)
  assert.equal(lower.openable, false)
  assert.equal(lower.safe, false)
})

test('closed trapdoor: openable; open trapdoor: walkable', () => {
  const closed = blockAt(6)
  assert.equal(closed.openable, true)
  assert.equal(closed.physical, true)
  const open = blockAt(7)
  assert.equal(open.physical, false)
  assert.equal(open.safe, true)
})

test('iron trapdoor: stays solid', () => {
  const block = blockAt(8)
  assert.equal(block.physical, true)
  assert.equal(block.openable, false)
})

test('fence gates: openable closed, walkable open', () => {
  const closed = blockAt(9)
  assert.equal(closed.openable, true)
  const open = blockAt(10)
  assert.equal(open.physical, false)
  assert.equal(open.safe, true)
})

test('regular solid block is untouched', () => {
  const stone = blockAt(11)
  assert.equal(stone.physical, true)
  assert.equal(stone.openable, false)
})

test('isPassageBlockName coverage', () => {
  assert.equal(isPassageBlockName('oak_door'), true)
  assert.equal(isPassageBlockName('spruce_trapdoor'), true)
  assert.equal(isPassageBlockName('oak_fence_gate'), true)
  assert.equal(isPassageBlockName('iron_door'), false)
  assert.equal(isPassageBlockName('iron_trapdoor'), false)
  assert.equal(isPassageBlockName('stone'), false)
  assert.equal(isPassageBlockName('air'), false)
  assert.equal(isPassageBlockName(undefined), false)
})
