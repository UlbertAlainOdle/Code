import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { parseCommand } from '../command.mjs'

const here = path.dirname(fileURLToPath(import.meta.url))
const catalogPath = path.join(here, '..', 'command_catalog.json')
const catalog = JSON.parse(fs.readFileSync(catalogPath, 'utf-8'))

// One representative input per worker verb; parseCommand must resolve it to
// exactly that verb, proving the catalog never promises an unparsed action.
const SAMPLES = {
  follow: 'follow Doctor',
  unfollow: 'unfollow',
  come: 'come Doctor',
  stop: 'stop',
  cancel_task: 'cancel task',
  status: 'status',
  auto_discard: 'auto discard on',
  say: 'say hello',
  raw: '/time set day',
  goto: 'goto 10 -64 32',
  jump: 'jump',
  swim: 'swim up',
  chop: 'chop 4',
  collect: 'collect iron_ore 3',
  mine_at: 'mine 10 64 32',
  mine_below: 'mine below',
  mine_nearby: 'mine nearby',
  inventory: 'inventory',
  equip: 'equip wooden_pickaxe',
  drop: 'drop stone 3',
  give: 'give bread 2 to Doctor',
  place: 'place crafting_table',
  pickup: 'pickup',
  eat: 'eat',
  craft: 'craft wooden_sword 1',
  smelt: 'smelt raw_iron iron_ingot 1 coal 1',
  brew: 'brew healing 1',
  cauldron: 'cauldron fill water',
  pillar: 'pillar 3',
  milk: 'milk 1',
  sleep: 'sleep',
  attack: 'attack zombie',
  chest_find: 'chest find',
  chest_deposit: 'deposit iron_ingot all',
  chest_withdraw: 'withdraw bread 3',
}

test('every catalog command maps to a verb parseCommand really produces', () => {
  assert.ok(catalog.commands.length >= 20, 'catalog unexpectedly small')
  for (const entry of catalog.commands) {
    const verb = entry.worker_verb
    const sample = SAMPLES[verb]
    assert.ok(sample, `no sample for verb ${verb}`)
    const parsed = parseCommand(sample)
    assert.equal(parsed.verb, verb, `${sample} should parse as ${verb}`)
  }
})

test('worker verb list matches catalog worker_verbs', () => {
  const verbs = [...new Set(catalog.commands.map(entry => entry.worker_verb))].sort()
  assert.deepEqual(verbs.sort(), [...catalog.worker_verbs].sort())
})

test('experimental entries are flagged', () => {
  const experimental = catalog.commands.filter(entry => entry.experimental)
  assert.ok(experimental.length >= 1)
  for (const entry of experimental) {
    assert.match(entry.label, /实验/)
  }
})

test('doctor-only verbs are never LLM-visible', () => {
  for (const entry of catalog.commands) {
    if (entry.doctor_only || entry.name === 'say') {
      assert.equal(entry.supports_llm, false, entry.name)
    }
  }
})
