// Controlled reproduction: does a knockback hit make mineflayer emit a
// non-finite move packet (the vanilla "收到包含非法玩家移动的数据包" kick)?
// Usage:
//   node repro_move_kick.mjs victim   (joins, instruments, stands still)
//   node repro_move_kick.mjs attacker (joins, punches the victim 5 times)
import { createRequire } from 'node:module'
const require = createRequire(import.meta.url)
const mineflayer = require('mineflayer')

const role = process.argv[2] || 'victim'
const HOST = '127.0.0.1'
const PORT = 1111
const NAME = role === 'victim' ? 'SayaDebug1' : 'SayaDebug2'

const bot = mineflayer.createBot({
  host: HOST,
  port: PORT,
  username: NAME,
  version: '1.21.5',
  auth: 'offline',
  hideErrors: false,
})

function finiteCheck(v) {
  return Number.isFinite(v)
}

bot.once('spawn', () => {
  console.log(`[${NAME}] spawned at`, String(bot.entity.position))

  // Intercept EVERY outgoing write; capture a stack when a move packet
  // carries a non-finite coordinate, and log incoming self-velocity.
  const client = bot._client
  const origWrite = client.write.bind(client)
  client.write = (name, data) => {
    try {
      if (name === 'position' || name === 'position_look') {
        const bad = ['x', 'y', 'z'].filter(k => !finiteCheck(data[k]))
        const badLook = name === 'position_look' && ['yaw', 'pitch'].filter(k => !finiteCheck(data[k]))
        if (bad.length || (badLook && badLook.length)) {
          console.log(`[${NAME}] !!! NON-FINITE OUTGOING ${name}:`, JSON.stringify(data))
          console.log(new Error('write stack').stack)
        }
      }
    } catch {}
    return origWrite(name, data)
  }
  const origEmit = client.emit.bind(client)
  client.emit = (event, ...args) => {
    if (event === 'entity_velocity') {
      const p = args[0]
      if (p && p.entityId === bot.entity.id) {
        const v = p.velocity || {}
        const all = [v.x, v.y, v.z].every(Number.isFinite)
        if (!all || Math.abs(v.x) > 10000 || Math.abs(v.y) > 10000) {
          console.log(`[${NAME}] !!! suspicious self velocity:`, JSON.stringify(p))
        } else {
          console.log(`[${NAME}] self velocity (finite):`, JSON.stringify(v))
        }
      }
    }
    return origEmit(event, ...args)
  }

  // Pinpoint the FIRST non-finite assignment inside the physics simulation:
  // simulatePlayer mutates its own state.pos/state.vel clones in place, so
  // trap those at simulation entry.
  const physicsMod = require('prismarine-physics')
  const physicsInstance = bot.physics || null
  const target = physicsInstance || null
  function trapVector(vec, label) {
    if (!vec) return
    for (const key of ['x', 'y', 'z']) {
      let v = vec[key]
      Object.defineProperty(vec, key, {
        get() { return v },
        set(nv) {
          if (!Number.isFinite(nv) && Number.isFinite(v)) {
            console.log(`[${NAME}] !!! FIRST ${label}.${key} <- ${nv}; assignment stack:`)
            console.log(new Error().stack)
          }
          v = nv
        },
        configurable: true,
      })
    }
  }
  if (target && target.simulatePlayer) {
    const origSimulate = target.simulatePlayer.bind(target)
    target.simulatePlayer = (state, world) => {
      trapVector(state.pos, 'state.pos')
      trapVector(state.vel, 'state.vel')
      return origSimulate(state, world)
    }
    console.log(`[${NAME}] physics simulator instrumented`)
  } else {
    console.log(`[${NAME}] physics instance not found; keys:`, Object.keys(physicsMod))
  }

  bot.on('physicsTick', () => {
    const e = bot.entity
    const bad = {
      pos: ![e.position.x, e.position.y, e.position.z].every(finiteCheck),
      vel: ![e.velocity.x, e.velocity.y, e.velocity.z].every(finiteCheck),
      yaw: !finiteCheck(e.yaw),
      pitch: !finiteCheck(e.pitch),
    }
    if (bad.pos || bad.vel || bad.yaw || bad.pitch) {
      console.log(`[${NAME}] !!! NaN entity state:`, bad,
        'pos=', String(e.position), 'vel=', String(e.velocity), 'yaw=', e.yaw, 'pitch=', e.pitch)
    }
  })
})

bot.on('health', () => {
  console.log(`[${NAME}] health ->`, bot.health)
})

bot.on('kicked', reason => console.log(`[${NAME}] KICKED:`, JSON.stringify(reason)))
bot.on('error', err => console.log(`[${NAME}] error:`, err.message))
bot.on('end', reason => {
  console.log(`[${NAME}] END:`, JSON.stringify(reason))
  process.exit(0)
})

if (role === 'attacker') {
  bot.once('spawn', async () => {
    await require('node:timers/promises').setTimeout(2500)
    for (let i = 0; i < 6; i++) {
      const target = Object.values(bot.entities).find(e => e.username === 'SayaDebug1')
      if (!target) { console.log('[SayaDebug2] victim not visible'); await new Promise(r => setTimeout(r, 800)); continue }
      try {
        await bot.lookAt(target.position.offset(0, 1.4, 0), true)
        await bot.attack(target)
        console.log('[SayaDebug2] punched SayaDebug1')
      } catch (e) {
        console.log('[SayaDebug2] attack failed:', e.message)
      }
      await new Promise(r => setTimeout(r, 1200))
    }
    console.log('[SayaDebug2] done, quitting')
    bot.quit()
    setTimeout(() => process.exit(0), 1500)
  })
}
