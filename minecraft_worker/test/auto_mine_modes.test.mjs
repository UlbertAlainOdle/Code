import assert from 'node:assert/strict'
import test from 'node:test'

import { parseAutoMineOreModes, ORE_MINE_DEFAULT_RANGE } from '../command.mjs'

test('per-ore modes parse with enable flag and clamped distance', () => {
  const modes = parseAutoMineOreModes({
    auto_mine_ore_modes: {
      iron: { enabled: true, distance: 10 },
      diamond: { enabled: true, distance: 999 },   // clamped to 64
      coal: { enabled: false, distance: 2 },       // clamped to 4
      nope: { enabled: true, distance: 8 },        // unknown group dropped
      gold: 'junk',                                // malformed dropped
    },
  })
  assert.deepEqual(modes, {
    iron: { enabled: true, distance: 10 },
    diamond: { enabled: true, distance: 64 },
    coal: { enabled: false, distance: 4 },
  })
})

test('a mode without its own distance falls back to the configured default', () => {
  const modes = parseAutoMineOreModes({
    auto_mine_range: 48,
    auto_mine_ore_modes: {
      diamond: { enabled: true },
      iron: { enabled: true, distance: 12 },
    },
  })
  assert.deepEqual(modes, {
    diamond: { enabled: true, distance: 48 },
    iron: { enabled: true, distance: 12 },
  })
})

test('missing config falls back to the built-in default range', () => {
  const modes = parseAutoMineOreModes({
    auto_mine_ore_modes: { diamond: { enabled: true } },
  })
  assert.deepEqual(modes, {
    diamond: { enabled: true, distance: ORE_MINE_DEFAULT_RANGE },
  })
})

test('legacy flat target list enables groups at the default range', () => {
  const modes = parseAutoMineOreModes({ auto_mine_targets: ['diamond', 'junk', 42] })
  assert.deepEqual(modes, {
    diamond: { enabled: true, distance: ORE_MINE_DEFAULT_RANGE },
  })
})

test('legacy targets merge into modes without overriding explicit entries', () => {
  const modes = parseAutoMineOreModes({
    auto_mine_range: 32,
    auto_mine_ore_modes: { iron: { enabled: false, distance: 8 } },
    auto_mine_targets: ['iron', 'coal'],
  })
  assert.deepEqual(modes, {
    iron: { enabled: false, distance: 8 },
    coal: { enabled: true, distance: 32 },
  })
})

test('absent or malformed payloads produce no modes', () => {
  assert.deepEqual(parseAutoMineOreModes({}), {})
  assert.deepEqual(parseAutoMineOreModes({ auto_mine_ore_modes: 'junk' }), {})
  assert.deepEqual(parseAutoMineOreModes({ auto_mine_ore_modes: ['iron'] }), {})
  assert.deepEqual(parseAutoMineOreModes(null), {})
})
