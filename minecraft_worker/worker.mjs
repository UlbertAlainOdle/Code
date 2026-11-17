import { createRequire } from 'node:module'
import readline from 'node:readline'

import { armorDestinationFor, AUTO_MINE_GROUPS, matchesBlock, matchesItem, normalizeItemName, oreGroupForBlock, parseAutoMineOreModes, parseCommand } from './command.mjs'
import { isPlayerChatPosition, resolveChatSender, stripChatDecoration } from './chatfilter.mjs'
import { chooseWeapon, isWieldableTool } from './weapons.mjs'
import { brewingPath, normalizePotionName } from './brewing.mjs'
import { enhanceMovementsForPassages } from './passage.mjs'
import {
  installVanillaPotionRegistry,
  loadPotionRegistryPacket,
  parseVariantItem,
  potionDescriptorMatches,
  potionIdentity,
  potionMatches,
} from './potion.mjs'

const require = createRequire(import.meta.url)
const mineflayer = require('mineflayer')
const { pathfinder, Movements, goals } = require('mineflayer-pathfinder')
const { Vec3 } = require('vec3')
const collectBlockPlugin = require('mineflayer-collectblock').plugin
const toolPlugin = require('mineflayer-tool').plugin

const FOLLOW_DISTANCE = 2.8
const FOLLOW_REPLAN_DISTANCE = 2.5
// Replan cadence: 8s (was 4s) - each replan of the static goal runs a
// full A* that hitches movement; targetMoved still triggers instantly.
const FOLLOW_REPLAN_INTERVAL_MS = 8000
const FOLLOW_STALL_CHECKS = 6
const STATUS_INTERVAL_MS = 1000
const TASK_TIMEOUT_MS = 120_000
const LOG_LIMIT = 40
const SUFFOCATION_MAX_LAYERS = 6
const HEALING_FOODS = ['enchanted_golden_apple', 'golden_apple', 'golden_carrot', 'honey_bottle']
const DANGEROUS_FLUIDS = new Set(['lava', 'flowing_lava'])
const FALLING_BLOCKS = new Set(['sand', 'red_sand', 'gravel', 'concrete_powder'])
const HOSTILE_MOB_RE = /zombie|skeleton|creeper|spider|enderman|witch|slime|phantom|drowned|husk|stray|pillager|vindicator/i
const HUNTABLE_ANIMALS = new Set([
  'cow', 'pig', 'sheep', 'chicken', 'rabbit', 'goat', 'mooshroom',
])
// Station scans sweep 32 blocks for 4 container types synchronously;
// 15s (was 5s) keeps them fresh enough while removing the periodic hitch.
const STATION_CACHE_MS = 15000
const FURNACE_BLOCK_NAMES = new Set(['furnace', 'blast_furnace', 'smoker'])
const BLAST_FURNACE_INPUTS = new Set(['raw_iron', 'raw_gold', 'raw_copper'])
const SMOKER_INPUTS = new Set([
  'beef', 'porkchop', 'chicken', 'mutton', 'rabbit', 'cod', 'salmon', 'potato', 'kelp',
])
const FUEL_CAPACITY = {
  coal_block: 80, dried_kelp_block: 20, blaze_rod: 12,
  coal: 8, charcoal: 8,
  oak_log: 1.5, spruce_log: 1.5, birch_log: 1.5, jungle_log: 1.5,
  acacia_log: 1.5, dark_oak_log: 1.5, mangrove_log: 1.5, cherry_log: 1.5,
  oak_planks: 1.5, spruce_planks: 1.5, birch_planks: 1.5, jungle_planks: 1.5,
  acacia_planks: 1.5, dark_oak_planks: 1.5, mangrove_planks: 1.5, cherry_planks: 1.5,
  bamboo_planks: 1.5, crimson_planks: 1.5, warped_planks: 1.5,
  stick: 0.5,
}

let config = null
let bot = null
let stopping = false
let statusTimer = null
let followMovements = null
let taskMovements = null
let autoEating = false
let damageReportAt = 0
let recentAggressor = { entity: null, at: 0 }
const confirmedEntityDeaths = new Map()
const potionNamesById = new Map()
let potionRegistrySource = 'none'
let taskSequence = 0
// True while the Python plan executor is running a multi-step plan.  Between
// two plan steps foreground.command is briefly empty, and follow/combat
// automation used to read that gap as "idle" and re-engage, fighting the
// next step for the pathfinder.  Python re-asserts this on every step and
// clears it on every terminal state.
let planActive = false
const stationCache = { at: 0, tables: [], furnaces: [], brewingStands: [], cauldrons: [] }

const follow = {
  enabled: false,
  target: '',
  active: false,
  lastVisibleAt: 0,
  lastReportAt: 0,
  lastPosition: null,
  lastDistance: null,
  stalls: 0,
  goalPosition: null,
  lastPlanAt: 0,
  recoveries: 0,
  cooldownUntil: 0,
  pitEscape: {
    busy: false,
    lastAt: 0,
  },
}

const foreground = {
  token: 0,
  command: '',
  verb: '',
  startedAt: 0,
}

// Highest priority survival state machine. Priority order:
//   emergency > stop > new foreground task > current task > follow > idle
const survival = {
  active: false,
  reason: '',
  since: 0,
  busy: false,
  caution: false,
  lastHealth: null,
  actions: [],
}

// Persistent low-priority automations. They may pause follow, but emergencies
// and explicit Doctor tasks always take priority over them.
const automation = {
  autoDiscardEnabled: false,
  autoDiscardBusy: false,
  autoDiscardToken: 0,
  pending: [],
  current: null,
  taskHistory: [],
  taskSeq: 0,
  lastAction: null,
  lastError: '',
  autoAttackHostiles: false,
  autoHuntAnimals: false,
  autoCounterattack: false,
  counterattackOwner: false,
  autoMineOres: false,
  autoMineOreModes: {},
  nearestOre: null,
  lastOreMineAt: 0,
  lastOreScanAt: 0,
  combatBusy: false,
  combatToken: 0,
  combatMode: 'idle',
  combatTarget: '',
  combatLastAction: null,
  combatLastError: '',
}

const lifecycle = {
  stage: 'idle',          // idle|connecting|playing|dead|respawning|disconnected|kicked
  lastEvent: '',
  lastEventAt: 0,
  death: null,            // { at, position, cause, last_task }
  respawn: null,          // { at, position }
  disconnect: null,       // { at, reason, stage }
  kick: null,             // { at, reason }
}

const state = {
  engine: 'mineflayer',
  running: true,
  connected: false,
  username: null,
  last_error: '',
  last_chat: '',
  last_status: '',
  follow_enabled: false,
  follow_target: '',
  follow_mode: 'off',
  follow_watchdog: {},
  movement: {},
  activity: {},
  activity_history: [],
  environment: {},
  last_damage: {},
  last_craft: null,   // { at, name, count } for "刚做的" references
  lifecycle: lifecycle,
  survival: {
    mode: 'normal',
    reason: '',
    since: 0,
    actions: [],
    oxygen_level: null,
    health: null,
  },
  automation: {
    auto_discard: {
      enabled: false,
      busy: false,
      pending: [],
      last_action: null,
      last_error: '',
    },
    combat: {
      auto_attack_hostiles: false,
      auto_hunt_animals: false,
      auto_counterattack: false,
      counterattack_owner: false,
      busy: false,
      mode: 'idle',
      target: '',
      last_action: null,
      last_error: '',
    },
  },
  runtime: { position: null, players: [], entities: [], inventory: [] },
  log: [],
}

function output(payload) {
  try {
    process.stdout.write(`${JSON.stringify(payload)}\n`)
  } catch (error) {
    process.stderr.write(`worker output error: ${error.message}\n`)
  }
}

function log(text, level = 'info') {
  const entry = { ts: Date.now() / 1000, text: String(text).slice(0, 400), level }
  state.log.push(entry)
  state.log = state.log.slice(-LOG_LIMIT)
  output({ type: 'log', ...entry })
}

function emitState() {
  refreshRuntime()
  state.heartbeat = { ts: Date.now() / 1000, pid: process.pid, uptime_s: Math.round(process.uptime()) }
  output({ type: 'state', state })
}

function clone(value) {
  return value == null ? value : JSON.parse(JSON.stringify(value))
}

function activityStart(command, kind, taskId = null) {
  const now = Date.now() / 1000
  state.activity = {
    // Task envelope: Python generates the id so replies, status, cancel and
    // logs can reference one lifecycle across the pipe.  Falls back to a
    // local id for legacy callers that dispatch without one.
    id: String(taskId || `${Date.now().toString(36)}-${(++taskSequence).toString(36)}`),
    command: String(command).slice(0, 160),
    kind,
    state: 'running',
    phase: 'starting',
    progress: 0,
    message: '任务已开始',
    started_at: now,
    updated_at: now,
  }
  log(`Task started [${state.activity.id}]: ${command}`)
}

function activityProgress(phase, progress, message, extra = {}) {
  if (state.activity?.state !== 'running') return
  state.activity = {
    ...state.activity,
    phase,
    progress: Math.max(0, Math.min(99, Math.round(progress))),
    message: String(message).slice(0, 240),
    updated_at: Date.now() / 1000,
    ...extra,
  }
}

function activityFinish(resultState, message) {
  if (!state.activity?.id || state.activity.state !== 'running') return
  const finished = {
    ...state.activity,
    state: resultState,
    phase: 'finished',
    progress: resultState === 'completed' ? 100 : state.activity.progress,
    message: String(message).slice(0, 240),
    ended_at: Date.now() / 1000,
  }
  state.activity = finished
  state.activity_history.push(clone(finished))
  state.activity_history = state.activity_history.slice(-12)
  log(`Task ${resultState} [${finished.id}]: ${message}`)
}

function publicPlayerName(value) {
  const raw = String(value || '').trim()
  if (!raw || raw.toLowerCase() === 'doctor') return config?.owner_username || 'Doctor'
  return raw
}

function distanceTo(entity) {
  if (!bot?.entity || !entity?.position) return null
  return bot.entity.position.distanceTo(entity.position)
}

function taskView(entry) {
  if (!entry) return null
  return {
    id: entry.id,
    name: entry.name,
    count: entry.count,
    state: entry.state || 'pending',
    attempts: Number(entry.attempts || 0),
    last_error: entry.lastError || '',
    note: entry.note || '',
    queued_at: entry.queuedAt || 0,
    completed_at: entry.completedAt || 0,
    label: `把 ${entry.name} ×${entry.count} 交给主人`,
  }
}

function finishAutoDiscardTask(entry) {
  entry.completedAt = Date.now() / 1000
  automation.taskHistory.push(taskView(entry))
  automation.taskHistory = automation.taskHistory.slice(-12)
}

function syncAutomationState() {
  state.automation = state.automation || {}
  state.automation.auto_discard = {
    enabled: automation.autoDiscardEnabled,
    busy: automation.autoDiscardBusy,
    current: taskView(automation.current),
    pending: automation.pending.map(taskView),
    history: automation.taskHistory.slice(-12),
    last_action: automation.lastAction,
    last_error: automation.lastError,
  }
  state.automation.combat = {
    auto_attack_hostiles: automation.autoAttackHostiles,
    auto_hunt_animals: automation.autoHuntAnimals,
    auto_counterattack: automation.autoCounterattack,
    counterattack_owner: automation.counterattackOwner,
    busy: automation.combatBusy,
    mode: automation.combatMode,
    target: automation.combatTarget,
    last_action: automation.combatLastAction,
    last_error: automation.combatLastError,
  }
  state.automation.mining = {
    auto_mine_ores: automation.autoMineOres,
    auto_mine_ore_modes: Object.fromEntries(
      Object.entries(automation.autoMineOreModes).map(([group, mode]) => [group, { ...mode }])),
    // Derived enabled-group list, kept for status consumers of the old key.
    auto_mine_targets: Object.entries(automation.autoMineOreModes)
      .filter(([, mode]) => mode.enabled)
      .map(([group]) => group)
      .sort(),
    nearest: automation.nearestOre,
    last_mine_at: automation.lastOreMineAt ? automation.lastOreMineAt / 1000 : null,
  }
}

function loadCombatAutomationConfig(next = {}) {
  automation.autoAttackHostiles = Boolean(next.auto_attack_hostiles)
  automation.autoHuntAnimals = Boolean(next.auto_hunt_animals)
  automation.autoCounterattack = Boolean(next.auto_counterattack)
  automation.counterattackOwner = Boolean(next.counterattack_owner)
  automation.autoMineOres = Boolean(next.auto_mine_ores)
  automation.autoMineOreModes = parseAutoMineOreModes(next)
  const modeDisabled =
    (automation.combatMode === 'counterattack' && !automation.autoCounterattack) ||
    (automation.combatMode === 'hostile' && !automation.autoAttackHostiles) ||
    (automation.combatMode === 'hunt' && !automation.autoHuntAnimals)
  if (automation.combatBusy && modeDisabled) cancelAutoCombat('对应自动战斗开关已关闭')
  syncAutomationState()
}

function setAutoDiscardEnabled(enabled) {
  automation.autoDiscardEnabled = Boolean(enabled)
  automation.autoDiscardToken += 1
  if (!automation.autoDiscardEnabled) {
    for (const entry of [...automation.pending, automation.current].filter(Boolean)) {
      entry.state = 'cancelled'
      finishAutoDiscardTask(entry)
    }
    automation.pending = []
    automation.current = null
    automation.lastError = ''
  }
  if (config) config.auto_discard_unplanned = automation.autoDiscardEnabled
  syncAutomationState()
  emitState()
  if (automation.autoDiscardEnabled) void processAutoDiscardQueue()
}

function pickupBelongsToCurrentTask() {
  if (survival.active) return true
  return new Set(['collect', 'chop', 'pickup', 'mine_at', 'mine_below', 'mine_nearby']).has(foreground.verb)
}

function droppedItemDetails(entity) {
  try {
    const item = entity?.getDroppedItem?.()
    if (item?.name && Number(item.count) > 0) {
      return { name: String(item.name), count: Number(item.count) }
    }
  } catch (error) {
    log(`Could not inspect collected item: ${error.message}`, 'warn')
  }
  return null
}

function queueUnexpectedPickup(item) {
  if (!automation.autoDiscardEnabled || !item?.name || Number(item.count) <= 0) return
  const existing = automation.pending.find(entry => entry.name === item.name)
  if (existing) existing.count += Number(item.count)
  else automation.pending.push({
    id: `task-${++automation.taskSeq}`,
    name: String(item.name),
    count: Number(item.count),
    state: 'pending',
    attempts: 0,
    lastError: '',
    queuedAt: Date.now() / 1000,
  })
  automation.lastError = ''
  syncAutomationState()
  log(`Unexpected pickup queued for owner delivery: ${item.name} x${item.count}`)
  void processAutoDiscardQueue()
}

function pauseAutoDiscard() {
  if (!automation.autoDiscardBusy) return
  automation.autoDiscardToken += 1
  stopPathfinder()
  clearControls()
}

const AUTO_DISCARD_FIRST_TIMEOUT_MS = 12_000
const AUTO_DISCARD_RETRY_TIMEOUT_MS = 30_000
// Patient mode: after a few failed approaches only chase when the owner is
// reasonably close (persistent follow brings Saya in); otherwise wait.
const AUTO_DISCARD_PATIENT_RANGE = 24
const AUTO_DISCARD_RETRY_COOLDOWN_S = 5

async function processAutoDiscardQueue() {
  if (!automation.autoDiscardEnabled || automation.autoDiscardBusy || !automation.pending.length) return
  if (!bot?.entity || !state.connected || survival.active || foreground.command || automation.combatBusy) return
  const ownerName = publicPlayerName('Doctor')
  const owner = bot.players?.[ownerName]?.entity
  if (!owner) {
    automation.lastError = `等待 ${ownerName} 出现在视野中`
    syncAutomationState()
    return
  }

  const entry = automation.pending[0]
  if (Number(entry.attempts || 0) >= 3) {
    const distance = distanceTo(owner)
    if (distance == null || distance > AUTO_DISCARD_PATIENT_RANGE) {
      automation.lastError = `${entry.name}：等跟随靠近后再交付（${distance == null ? '?' : Math.round(distance)} 格）`
      syncAutomationState()
      return
    }
    if (entry.nextTryAt && Date.now() / 1000 < entry.nextTryAt) return
  }

  automation.pending.shift()
  const token = ++automation.autoDiscardToken
  automation.autoDiscardBusy = true
  automation.current = entry
  entry.state = 'running'
  if (!entry.startedAt) entry.startedAt = Date.now() / 1000
  automation.lastError = ''
  syncAutomationState()
  let delivered = 0
  try {
    if (follow.active) stopPathfinder()
    clearControls()
    setTaskMovements()
    if (taskMovements) taskMovements.canDig = false
    if (distanceTo(owner) > 3.2) {
      state.movement = {
        state: 'auto_discard_approaching_owner',
        reason: `auto_discard:${entry.name}`,
        method: 'mineflayer_pathfinder',
        distance: round(distanceTo(owner)),
      }
      const approachTimeout = Number(entry.attempts || 0) === 0
        ? AUTO_DISCARD_FIRST_TIMEOUT_MS
        : AUTO_DISCARD_RETRY_TIMEOUT_MS
      await timeout(
        bot.pathfinder.goto(new goals.GoalNear(owner.position.x, owner.position.y, owner.position.z, 2)),
        approachTimeout,
        'auto discard approach owner',
      )
    }
    if (token !== automation.autoDiscardToken || survival.active || foreground.command || automation.combatBusy) {
      throw Object.assign(new Error('自动丢弃被更高优先级任务暂停'), { code: 'PAUSED' })
    }
    try { await bot.lookAt(owner.position.offset(0, 1.3, 0), true) } catch {}
    let remaining = entry.count
    for (const item of bot.inventory.items().filter(item => matchesItem(item.name, entry.name))) {
      if (remaining <= 0) break
      const take = Math.min(item.count, remaining)
      await bot.toss(item.type, item.metadata, take)
      remaining -= take
      delivered += take
    }
    automation.lastAction = {
      at: Date.now() / 1000,
      name: entry.name,
      count: delivered,
      owner: ownerName,
    }
    if (delivered > 0) {
      await speak(`主人，我意外捡到了 ${entry.name} ×${delivered}，已经带到你身边丢下了。`)
      log(`Auto-discard delivered ${entry.name} x${delivered} to ${ownerName}`)
    }
    const missing = Math.max(0, entry.count - delivered)
    if (missing > 0 && delivered === 0) {
      // Nothing matching in the inventory anymore (consumed by a task or
      // otherwise gone) - looping retries would never recover it.
      entry.state = 'completed'
      entry.note = '交付时物品已不在背包'
      entry.count = 0
      finishAutoDiscardTask(entry)
    } else if (missing > 0) {
      entry.state = 'completed'
      entry.note = `部分交付，剩余 ${missing} 个`
      finishAutoDiscardTask(entry)
      automation.pending.push({
        id: `task-${++automation.taskSeq}`,
        name: entry.name,
        count: missing,
        state: 'pending',
        attempts: 0,
        lastError: '',
        queuedAt: Date.now() / 1000,
      })
    } else {
      entry.state = 'completed'
      finishAutoDiscardTask(entry)
    }
  } catch (error) {
    entry.attempts = Number(entry.attempts || 0) + 1
    entry.lastError = String(error?.message || error).slice(0, 160)
    automation.lastError = entry.lastError
    const remainingCount = Math.max(0, entry.count - delivered)
    if (automation.autoDiscardEnabled && remainingCount > 0) {
      // Keep the task alive: a long chase must never silently drop it.
      entry.count = remainingCount
      entry.state = 'retrying'
      entry.nextTryAt = Date.now() / 1000 + AUTO_DISCARD_RETRY_COOLDOWN_S
      if (entry.attempts < 3) automation.pending.unshift(entry)
      else automation.pending.push(entry)
      log(`Auto-discard retry #${entry.attempts} queued: ${entry.name} (remaining ${remainingCount})`, 'info')
    } else {
      entry.state = delivered > 0 ? 'completed' : 'failed'
      finishAutoDiscardTask(entry)
    }
    log(`Auto-discard paused/failed: ${entry.lastError}`, error?.code === 'PAUSED' ? 'info' : 'warn')
  } finally {
    automation.autoDiscardBusy = false
    automation.current = null
    if (taskMovements) taskMovements.canDig = true
    syncAutomationState()
    if (follow.enabled && !survival.active && !foreground.command) {
      follow.active = false
      await reconcileFollow()
    }
    emitState()
  }
}

function foodPoints(item) {
  if (!item) return 0
  return Number(item.foodPoints ?? bot?.registry?.foodsByName?.[item.name]?.foodPoints ?? 0)
}

function clearControls() {
  if (!bot) return
  for (const key of ['forward', 'back', 'left', 'right', 'jump', 'sprint', 'sneak']) {
    try { bot.setControlState(key, false) } catch {}
  }
}

function stopPathfinder() {
  if (!bot?.pathfinder) return
  try { bot.pathfinder.setGoal(null) } catch {}
  try { bot.pathfinder.stop() } catch {}
  follow.active = false
}

function createMovements() {
  followMovements = new Movements(bot)
  followMovements.canDig = false
  followMovements.allow1by1towers = false
  followMovements.allowParkour = false
  followMovements.allowSprinting = true
  followMovements.canOpenDoors = true
  enhanceMovementsForPassages(bot, followMovements)

  taskMovements = new Movements(bot)
  taskMovements.canDig = true
  taskMovements.allow1by1towers = false
  taskMovements.allowParkour = false
  taskMovements.allowSprinting = true
  taskMovements.canOpenDoors = true
  taskMovements.dontCreateFlow = true
  enhanceMovementsForPassages(bot, taskMovements)
  if (bot.collectBlock) bot.collectBlock.movements = taskMovements
  // Keep every A* search bounded.  Dynamic GoalFollow can continuously
  // invalidate a large search when the target falls or the terrain changes,
  // starving timers and heartbeats.  Follow uses periodically refreshed
  // static goals below instead.
  if (bot.pathfinder) {
    bot.pathfinder.thinkTimeout = 2000
    bot.pathfinder.tickTimeout = 20
    bot.pathfinder.searchRadius = Math.max(16, Math.min(128, Number(config?.follow_search_range || 64)))
  }
}

function setTaskMovements() {
  if (bot?.pathfinder && taskMovements) bot.pathfinder.setMovements(taskMovements)
}

function setFollowMovements() {
  if (bot?.pathfinder && followMovements) bot.pathfinder.setMovements(followMovements)
}

// --- survival / emergency state machine -----------------------------------

function recordLifecycleEvent(name, detail = {}) {
  lifecycle.lastEvent = name
  lifecycle.lastEventAt = Date.now() / 1000
  output({ type: 'lifecycle', event: name, ...detail })
}

function survivalAction(text) {
  survival.actions.push({ ts: Date.now() / 1000, text: String(text).slice(0, 120) })
  survival.actions = survival.actions.slice(-10)
  state.survival.actions = survival.actions
}

function syncSurvivalState() {
  state.survival.mode = survival.active ? 'emergency' : survival.caution ? 'caution' : 'normal'
  state.survival.reason = survival.reason
  state.survival.since = survival.since
  state.survival.health = bot?.health ?? null
  state.survival.oxygen_level = typeof bot?.oxygenLevel === 'number' ? bot.oxygenLevel : null
}

async function setEmergency(reason) {
  if (survival.active) return
  survival.active = true
  survival.reason = reason
  survival.since = Date.now() / 1000
  log(`EMERGENCY: ${reason}`, 'error')
  survivalAction(`emergency:${reason}`)
  pauseAutoDiscard()
  cancelAutoCombat('emergency')
  // Cancel the foreground task and pause follow; both are restored after the
  // emergency clears (follow) or reported as interrupted (task).  This must
  // finish before a rescue starts digging: cancelForeground calls
  // stopDigging(), which otherwise races and cancels the rescue itself.
  await cancelForeground(`emergency:${reason}`)
  clearControls()
  stopPathfinder()
  syncSurvivalState()
  emitState()
}

async function clearEmergency(summary = '') {
  if (!survival.active) return
  survival.active = false
  survival.reason = ''
  survival.since = 0
  syncSurvivalState()
  log(`Emergency cleared: ${summary || survival.reason}`)
  if (summary) await speak(summary)
  if (follow.enabled) {
    follow.active = false
    state.follow_mode = 'resuming'
    await reconcileFollow()
  }
  emitState()
}

function isSolidBlock(block) {
  return Boolean(block && block.boundingBox === 'block' && block.name !== 'air')
}

function isDigSolid(block) {
  return isSolidBlock(block) && isSafeDigTarget(block)
}

function headOccupant() {
  if (!bot?.entity) return null
  return bot.blockAt(bot.entity.position.offset(0, 1.62, 0))
}

function isSuffocating() {
  return isSolidBlock(headOccupant())
}

async function digWithBestTool(block) {
  try { await bot.tool.selectTool(block, false) } catch {}
  await bot.dig(block)
}

/** Find a diggable side block with air beyond it, for lateral escapes. */
function sideEscapeCandidate() {
  if (!bot?.entity) return null
  const feet = bot.entity.position.floored()
  for (const offset of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
    const wall = bot.blockAt(feet.offset(offset[0], 0, offset[1]))
    const beyond = bot.blockAt(feet.offset(offset[0] * 2, 0, offset[1] * 2))
    const beyondHead = bot.blockAt(feet.offset(offset[0] * 2, 1, offset[1] * 2))
    if (isDigSolid(wall) && !isSolidBlock(beyond) && !isSolidBlock(beyondHead)) {
      return { block: wall, direction: offset }
    }
  }
  return null
}

async function moveToward(offset, milliseconds = 1200) {
  if (!offset) return
  const control = offset[0] > 0 ? 'right' : offset[0] < 0 ? 'left' : ''
  bot.setControlState('forward', true)
  if (control) bot.setControlState(control, true)
  await wait(milliseconds)
  clearControls()
}

async function suffocationRescue() {
  if (survival.busy || !bot?.entity) return
  survival.busy = true
  await setEmergency('suffocation')
  const dug = []
  try {
    for (let layer = 0; layer < SUFFOCATION_MAX_LAYERS; layer += 1) {
      if (!bot.entity) return
      const head = headOccupant()
      const headBlock = bot.blockAt(bot.entity.position.offset(0, 1, 0))
      const aboveHead = bot.blockAt(bot.entity.position.offset(0, 2, 0))
      const dangerousAbove = DANGEROUS_FLUIDS.has(aboveHead?.name)
      let target = null
      if (!dangerousAbove) {
        if (isDigSolid(head)) target = head
        else if (isDigSolid(headBlock)) target = headBlock
      }
      if (!target) {
        if (!isSuffocating()) break
        break
      }
      survivalAction(`dig ${target.name}${FALLING_BLOCKS.has(target.name) ? ' (falling block)' : ''}`)
      await digWithBestTool(target)
      dug.push(target.name)
      await wait(300) // let falling sand/gravel settle
      // Do not jump or move while the server still considers the entity inside
      // a solid block.  Some servers reject that correction as invalid player
      // movement and kick the client. Falling blocks are handled by digging the
      // new head occupant on the next loop iteration.
    }
    const free = !isSuffocating()
    if (free) {
      await clearEmergency(
        `窒息脱困完成：挖掉了 ${dug.length} 个方块，当前生命 ${bot.health}/20；` +
        `${foreground.command ? '刚才的任务被打断，需要请再吩咐。' : follow.enabled ? '继续跟随。' : ''}`,
      )
    } else if (bot.health < 6) {
      await speak(`无法自救！头部仍被堵住（挖了 ${dug.length} 个方块），生命只剩 ${bot.health}/20，请救我！`)
      log('Suffocation self-rescue failed', 'error')
      // Even a failed rescue must end the emergency: the rescue loop is over,
      // and a survival.active left true parks follow on paused_emergency
      // FOREVER (nothing re-triggers unless damage continues).  If damage
      // resumes, the damage handler re-enters the rescue.
      await clearEmergency(`窒息自救未完全脱困（挖了 ${dug.length} 个方块），已退出紧急状态继续待命。`)
    } else {
      await speak(`自救进度有限：已挖 ${dug.length} 个方块仍在尝试，生命 ${bot.health}/20。`)
      await clearEmergency(`窒息自救进度有限（挖了 ${dug.length} 个方块），已退出紧急状态继续待命。`)
    }
  } catch (error) {
    log(`Suffocation rescue error: ${error.message}`, 'error')
    if (bot?.entity) await clearEmergency(`窒息自救出错：${error.message}`)
  } finally {
    survival.busy = false
    syncSurvivalState()
  }
}

async function lavaEscape() {
  if (survival.busy) return
  survival.busy = true
  await setEmergency('lava')
  try {
    survivalAction('lava escape: seeking water or exit')
    let escaped = false
    try {
      const waterPosition = bot.findBlock({
        matching: block => block?.name === 'water',
        maxDistance: 16,
      })
      if (waterPosition) {
        setTaskMovements()
        taskMovements.canDig = false
        await timeout(
          bot.pathfinder.goto(new goals.GoalGetToBlock(waterPosition.x, waterPosition.y, waterPosition.z)),
          8000,
          'lava escape pathing',
        )
        escaped = true
        survivalAction('reached water / exit')
      }
    } catch (error) {
      log(`Lava escape pathing failed: ${error.message}`, 'warn')
    }
    if (!escaped) {
      // Last resort: hold jump and move to reduce further lava damage.
      bot.setControlState('jump', true)
      await wait(1500)
      clearControls()
    }
    await clearEmergency(
      escaped
        ? `已离开岩浆区域，当前生命 ${bot.health}/20。`
        : `岩浆撤离不完全，当前生命 ${bot.health}/20，请帮我！`,
    )
  } finally {
    survival.busy = false
    if (taskMovements) taskMovements.canDig = true
    syncSurvivalState()
  }
}

async function swimUpForAir() {
  if (survival.busy || !bot?.entity?.isInWater) return
  survival.busy = true
  await setEmergency('drowning')
  try {
    survivalAction('swimming up for air')
    const started = Date.now()
    while (Date.now() - started < 8000 && bot.entity?.isInWater) {
      bot.setControlState('jump', true)
      bot.setControlState('sprint', false)
      await wait(250)
      if (typeof bot.oxygenLevel === 'number' && bot.oxygenLevel >= 18) break
    }
    bot.setControlState('jump', false)
    await clearEmergency(`已回到水面恢复呼吸，氧气 ${bot.oxygenLevel ?? '?'}/20。`)
  } catch (error) {
    log(`Swim up failed: ${error.message}`, 'error')
    await clearEmergency(`上浮出错：${error.message}`)
  } finally {
    survival.busy = false
    syncSurvivalState()
  }
}

function hostileMobPolicy() {
  return String(config?.survival?.mob_policy || 'counter').toLowerCase()
}

function nearestHostile(maxDistance = 6) {
  return nearestEntity(entity => isHostileEntity(entity), maxDistance)
}

function isHostileEntity(entity) {
  if (!entity || entity === bot?.entity) return false
  if (entity.username === follow.target) return false
  if (entity.type === 'hostile') return true
  if (entity.type === 'mob' && typeof entity.name === 'string') {
    return HOSTILE_MOB_RE.test(entity.name)
  }
  return false
}

function isOwnerEntity(entity) {
  const owner = String(publicPlayerName('Doctor') || '').toLowerCase()
  return Boolean(owner && String(entity?.username || '').toLowerCase() === owner)
}

function isHuntableAnimal(entity) {
  if (!entity || entity === bot?.entity || entity.type !== 'mob') return false
  const name = String(entity.name || '').toLowerCase()
  // Whitelist semantics intentionally exclude villagers, wandering traders,
  // pets and neutral utility mobs.
  return HUNTABLE_ANIMALS.has(name)
}

function combatTargetLabel(entity) {
  return String(entity?.username || entity?.displayName || entity?.name || '目标')
}

function cancelAutoCombat(reason = 'cancelled') {
  if (!automation.combatBusy) return
  automation.combatToken += 1
  automation.combatBusy = false
  automation.combatMode = 'idle'
  automation.combatTarget = ''
  automation.combatLastError = reason
  try { bot?.pvp?.stop?.() } catch {}
  stopPathfinder()
  clearControls()
  syncAutomationState()
}

async function equipCombatWeapon(target = null) {
  // Situation-aware weapon policy (weapons.mjs): economy mode while healthy
  // (fewest hits = least durability spent, cheapest material on ties),
  // maximum damage regardless of durability once injured.
  let targetHp = 20
  try {
    const mob = target?.name
      ? bot.registry?.entitiesByName?.[target.name] ?? bot.registry?.mobsByName?.[target.name]
      : null
    if (Number(mob?.health) > 0) targetHp = Number(mob.health)
  } catch {}
  const choice = chooseWeapon(bot.inventory.items(), {
    health: Number.isFinite(Number(bot.health)) ? bot.health : null,
    targetHp,
  })
  if (choice?.item) {
    survivalAction(`weapon ${choice.name} (damage ${choice.damage})`)
    try { await bot.equip(choice.item, 'hand') } catch {}
  }
}

/** Put a held weapon/tool back into the inventory after combat/mining so
 *  accidental swings or uses cannot wear it.  No-op for anything else. */
async function sheatheHeldTool() {
  const held = bot?.heldItem
  if (!held || !isWieldableTool(held.name)) return
  try { await bot.unequip('hand') } catch {}
}

// Foreground verbs whose finishing state should sheathe the held tool
// (combat engagements and digging; an explicit "equip X" command is the
// user's own choice and is never overridden).
const SHEATHE_AFTER_VERBS = new Set([
  'attack', 'chop', 'collect', 'mine_at', 'mine_below', 'mine_nearby',
])

function configuredCombatRange() {
  return Math.max(8, Math.min(128, Number(config?.combat_detection_range || 48)))
}

/**
 * Conservative server-safe melee.  mineflayer-pvp continuously follows a
 * moving target and sprint-jumps; some vanilla-compatible servers reject the
 * resulting correction as invalid_player_movement.  Here pathfinding stops
 * before each strike and sprinting stays disabled for the whole combat.
 */
async function conservativeAttack(target, options = {}) {
  if (!target?.position || !bot?.entity) throw new Error('攻击目标已经离开视野')
  const expectedAutomationToken = options.automationToken
  const expectedForegroundToken = options.foregroundToken
  const assertCombatCurrent = () => {
    if (expectedForegroundToken != null) assertCurrent(expectedForegroundToken)
    if (expectedAutomationToken != null && automation.combatToken !== expectedAutomationToken) {
      const error = new Error('automatic combat cancelled')
      error.code = 'CANCELLED'
      throw error
    }
    if (!bot?.entity || !state.connected) throw new Error('机器人已断开连接')
  }

  const oldSprint = taskMovements?.allowSprinting
  confirmedEntityDeaths.delete(target.id)
  const maxRange = configuredCombatRange()
  if (taskMovements) taskMovements.allowSprinting = false
  setTaskMovements()
  try {
    await equipCombatWeapon(target)
    assertCombatCurrent()
    const deadline = Date.now() + Number(options.durationMs || 110_000)
    let swings = 0
    let pathFailures = 0
    let distance = distanceTo(target)
    while (Date.now() < deadline && !confirmedEntityDeaths.has(target.id)) {
      assertCombatCurrent()
      const current = bot.entities?.[target.id]
      if (!current?.position) {
        await wait(300)
        if (!confirmedEntityDeaths.has(target.id) && !bot.entities?.[target.id]) {
          throw new Error('目标已离开客户端识别范围，未确认死亡')
        }
        continue
      }
      target = current
      distance = distanceTo(target)
      if (distance == null) throw new Error('无法测量目标距离')
      if (distance > maxRange) {
        throw new Error(`目标距离 ${round(distance)} 格，超出自定义识别范围 ${maxRange} 格`)
      }
      if (distance > 3.15) {
        if (expectedForegroundToken != null) {
          activityProgress(
            'pursuing', Math.min(90, 10 + swings * 3),
            `正在追击 ${combatTargetLabel(target)}，距离 ${round(distance)}/${maxRange} 格；可点击取消`,
          )
        }
        setTaskMovements()
        try {
          await timeout(
            bot.pathfinder.goto(new goals.GoalNear(
              target.position.x, target.position.y, target.position.z, 3,
            )),
            6000,
            'combat approach',
          )
          pathFailures = 0
        } catch (error) {
          // A path can become temporarily impossible when the target jumps,
          // falls, swims or moves behind newly placed blocks.  This is not a
          // completed/failed combat task while the same entity is still in
          // recognition range.  Refresh its position and retry at a bounded
          // cadence; cancellation and disconnect still escape immediately.
          assertCombatCurrent()
          pathFailures += 1
          const reason = String(error?.message || error || 'path unavailable')
          log(
            `Combat route unavailable; retry ${pathFailures} for ${combatTargetLabel(target)} ` +
            `(${round(distance)} blocks): ${reason}`,
            'warn',
          )
          if (expectedForegroundToken != null) {
            activityProgress(
              'pursuit_blocked', Math.min(90, 8 + swings * 3),
              `暂时找不到通往 ${combatTargetLabel(target)} 的路线，仍在范围内，正在第 ${pathFailures} 次重试；可点击取消`,
            )
          }
          await wait(Math.min(1500, 400 + pathFailures * 150))
        } finally {
          stopPathfinder()
          clearControls()
        }
        continue
      }
      stopPathfinder()
      clearControls()
      await bot.lookAt(target.position.offset(0, Math.min(1.4, Number(target.height || 1.4) * 0.7), 0), true)
      await bot.attack(target)
      swings += 1
      await wait(650)
    }
    // The target may die at the same moment a new foreground task supersedes
    // this one.  Re-check ownership before returning a successful result so
    // the old task cannot write kill metadata into the new activity object.
    assertCombatCurrent()
    return {
      swings,
      defeated: confirmedEntityDeaths.has(target.id),
      distance: distanceTo(target),
    }
  } finally {
    stopPathfinder()
    clearControls()
    if (taskMovements && oldSprint != null) taskMovements.allowSprinting = oldSprint
  }
}

async function runAutomaticCombat(mode, target) {
  if (!target || automation.combatBusy || survival.active || foreground.command) return
  if (mode === 'counterattack' && isOwnerEntity(target) && !automation.counterattackOwner) {
    automation.combatLastError = '主人反击已关闭'
    syncAutomationState()
    return
  }
  pauseAutoDiscard()
  const token = ++automation.combatToken
  automation.combatBusy = true
  automation.combatMode = mode
  automation.combatTarget = combatTargetLabel(target)
  automation.combatLastError = ''
  if (follow.enabled) {
    stopPathfinder()
    state.follow_mode = 'paused_for_combat'
  }
  syncAutomationState()
  emitState()
  try {
    const result = await conservativeAttack(target, { automationToken: token, durationMs: 8000 })
    automation.combatLastAction = {
      at: Date.now() / 1000,
      mode,
      target: combatTargetLabel(target),
      swings: result.swings,
      defeated: result.defeated,
    }
    if (!result.defeated && Number(result.distance) > 3.6) {
      automation.combatLastError = '目标离开安全攻击范围，停止追击'
    }
  } catch (error) {
    if (error?.code !== 'CANCELLED') {
      automation.combatLastError = String(error?.message || error).slice(0, 160)
      log(`Automatic combat failed: ${automation.combatLastError}`, 'warn')
    }
  } finally {
    if (automation.combatToken === token) {
      automation.combatBusy = false
      automation.combatMode = 'idle'
      automation.combatTarget = ''
      syncAutomationState()
      // Leaving combat: put the weapon away so accidental swings cannot
      // wear it.
      await sheatheHeldTool()
      if (follow.enabled && !survival.active && !foreground.command && !planActive) {
        follow.active = false
        await reconcileFollow()
      }
      emitState()
    }
  }
}

function processCombatAutomation() {
  if (!bot?.entity || !state.connected || survival.active || foreground.command || planActive || automation.combatBusy) return
  const hostileRange = follow.enabled ? 4.5 : 8
  if (automation.autoAttackHostiles) {
    const hostile = nearestHostile(hostileRange)
    if (hostile) return void runAutomaticCombat('hostile', hostile)
  }
  // Hunting is an idle activity and never pulls Saya away from persistent
  // follow.  Explicit tasks also always pre-empt it.
  if (automation.autoHuntAnimals && !follow.enabled) {
    const animal = nearestEntity(isHuntableAnimal, 10)
    if (animal) void runAutomaticCombat('hunt', animal)
  }
}

// Auto-mine toggle: while otherwise idle, scan for every ENABLED ore mode
// (each ore group has its own enable flag and detection distance) every
// ORE_MINE_SCAN_INTERVAL_MS (detection is surfaced in the status view as
// "nearest"), and every ORE_MINE_COOLDOWN_MS actually run ONE collect round
// for the closest detected block.  While persistently following the doctor
// each mode's effective range is capped short so the walk is not constantly
// hijacked.
const ORE_MINE_COOLDOWN_MS = 20_000
const ORE_MINE_SCAN_INTERVAL_MS = 5_000
const ORE_MINE_FOLLOW_RANGE_CAP = 6

function modeScanRange(mode) {
  return follow.enabled ? Math.min(mode.distance, ORE_MINE_FOLLOW_RANGE_CAP) : mode.distance
}

function harvestableWithBestPickaxe(block) {
  const required = block?.harvestTools
  if (!required) return true   // registry says hand-minable
  const pickaxeIds = new Set(
    bot.inventory.items().filter(item => item.name.endsWith('_pickaxe')).map(item => item.type),
  )
  if (!pickaxeIds.size) return false
  return Object.keys(required).some(id => pickaxeIds.has(Number(id)))
}

function processOreMiningAutomation() {
  const enabledModes = Object.entries(automation.autoMineOreModes)
    .filter(([, mode]) => mode.enabled)
  const enabled = automation.autoMineOres && enabledModes.length > 0
  if (!enabled || !bot?.entity || !state.connected) {
    if (automation.nearestOre) {
      automation.nearestOre = null
      syncAutomationState()
    }
    return
  }
  if (survival.active || foreground.command || planActive || automation.combatBusy) return
  const now = Date.now()
  if (now - automation.lastOreScanAt < ORE_MINE_SCAN_INTERVAL_MS) return
  automation.lastOreScanAt = now

  // One bounded scan per enabled ore mode with ITS OWN detection distance;
  // the closest hit across modes wins.
  let best = null
  for (const [group, mode] of enabledModes) {
    const positions = bot.findBlocks({
      matching: block => oreGroupForBlock(block?.name) === group && harvestableWithBestPickaxe(block),
      maxDistance: modeScanRange(mode),
      count: 8,
    })
    for (const position of positions) {
      const distance = bot.entity.position.distanceTo(position)
      if (!best || distance < best.distance) best = { position, distance, group }
    }
  }
  const block = best ? bot.blockAt(best.position) : null
  if (!block) {
    if (automation.nearestOre) {
      automation.nearestOre = null
      syncAutomationState()
    }
    return
  }

  const distance = round(best.distance)
  automation.nearestOre = {
    name: block.name,
    group: best.group,
    position: [round(block.position.x), round(block.position.y), round(block.position.z)],
    distance,
    at: now / 1000,
  }
  syncAutomationState()
  if (now - automation.lastOreMineAt < ORE_MINE_COOLDOWN_MS) return
  automation.lastOreMineAt = now
  log(`Auto-mine: ${block.name} at ${block.position}`)
  // quiet: an automatic activity must not spam chat; failures are logged.
  void dispatchCommand(`collect ${block.name} 1`, 'automation', true)
}

/** Nearby crafting tables / furnaces (cached: scanned at most every 5s). */
function refreshStations() {
  if (!bot?.entity) return
  if (Date.now() - stationCache.at < STATION_CACHE_MS) return
  try {
    const tables = bot.findBlocks({
      matching: block => block?.name === 'crafting_table',
      maxDistance: 32,
      count: 3,
    })
    const furnaces = bot.findBlocks({
      matching: block => FURNACE_BLOCK_NAMES.has(block?.name),
      maxDistance: 32,
      count: 3,
    })
    const brewingStands = bot.findBlocks({
      matching: block => block?.name === 'brewing_stand',
      maxDistance: 32,
      count: 3,
    })
    const cauldrons = bot.findBlocks({
      matching: block => ['cauldron', 'water_cauldron', 'lava_cauldron', 'powder_snow_cauldron'].includes(block?.name),
      maxDistance: 32,
      count: 3,
    })
    stationCache.at = Date.now()
    stationCache.tables = tables
    stationCache.furnaces = furnaces
    stationCache.brewingStands = brewingStands
    stationCache.cauldrons = cauldrons
  } catch {}
}

function stationView(positions) {
  return positions.map(position => {
    const block = bot.blockAt(position)
    return {
      x: position.x, y: position.y, z: position.z,
      name: block?.name || '',
      distance: bot.entity ? round(bot.entity.position.distanceTo(position)) : null,
    }
  })
}

async function applyMobPolicy() {
  const policy = hostileMobPolicy()
  const attacker = nearestHostile(6)
  if (!attacker) return
  const label = attacker.name || attacker.displayName || '敌对生物'
  survivalAction(`mob policy ${policy}: ${label}`)
  if (policy === 'counter' && automation.autoCounterattack) {
    void runAutomaticCombat('counterattack', attacker)
    return
  }
  if (policy === 'flee') {
    const owner = bot.players?.[publicPlayerName('Doctor')]?.entity || bot.players?.[follow.target]?.entity
    try {
      if (owner) {
        setFollowMovements()
        await timeout(bot.pathfinder.goto(new goals.GoalNear(owner.position.x, owner.position.y, owner.position.z, 2)), 8000, 'flee to owner')
      } else {
        const away = attacker.position && bot.entity
          ? bot.entity.position.plus(bot.entity.position.minus(attacker.position).normalize().scaled(8))
          : null
        if (away) {
          setTaskMovements()
          await timeout(bot.pathfinder.goto(new goals.GoalNear(away.x, away.y, away.z, 1)), 8000, 'flee away')
        }
      }
    } catch (error) {
      log(`Flee pathing failed: ${error.message}`, 'warn')
    }
    return
  }
  await speak(`我被 ${label} 攻击了，生命 ${bot.health}/20。`)
}

async function eatHealingItem() {
  const items = bot.inventory.items()
  const healing = HEALING_FOODS.map(name => bot.registry.itemsByName[name])
    .filter(Boolean)
    .flatMap(item => items.filter(entry => entry.type === item.id))
  const target = healing[0]
  if (target) {
    survivalAction(`eat healing item ${target.name}`)
    await bot.equip(target, 'hand')
    await bot.consume()
    return true
  }
  // Normal food only restores hunger; it helps when natural regeneration can
  // kick in (food >= 18 regenerates, so still eat when food is low).
  if (bot.food < 18) {
    try { await eatBestFood(true) } catch {}
  }
  return false
}

async function handleLowHealth() {
  if (!bot?.entity) return
  const health = bot.health
  if (survival.active) return
  if (health < 6) {
    await setEmergency('low_health')
    survivalAction('low health: eating recovery item')
    try {
      await eatHealingItem()
    } catch (error) {
      log(`Healing eat failed: ${error.message}`, 'warn')
    }
    await speak(`生命只剩 ${health}/20，已进入紧急状态并尝试恢复。`)
    await clearEmergency(`生命恢复处理完成，当前生命 ${bot.health}/20。`)
    return
  }
  if (health < 10 && !survival.caution && !foreground.command) {
    survival.caution = true
    syncSurvivalState()
    // Keep moving toward the owner when following (that IS the safe move);
    // otherwise approach the owner once if visible.
    const owner = bot.players?.[publicPlayerName('Doctor')]?.entity
    if (!follow.enabled && owner) {
      try {
        await speak(`生命偏低（${health}/20），我先靠近你一些。`)
        setTaskMovements()
        await timeout(bot.pathfinder.goto(new goals.GoalNear(owner.position.x, owner.position.y, owner.position.z, 2)), 10_000, 'approach owner')
      } catch (error) {
        log(`Low health approach failed: ${error.message}`, 'warn')
      }
    }
    return
  }
  if (health >= 12 && survival.caution) {
    survival.caution = false
    syncSurvivalState()
  }
}

async function onDamageTaken(amount) {
  if (!bot?.entity) return
  const position = bot.entity.position
  let cause = 'unknown'
  const swungRecently = recentAggressor.entity && Date.now() - recentAggressor.at <= 2200
    && bot.entities?.[recentAggressor.entity.id]
  const likelyAttacker = swungRecently ? recentAggressor.entity : nearestHostile(6)
  if (bot.entity.isInLava) cause = 'lava'
  else if (isSuffocating()) cause = 'suffocation'
  else if (likelyAttacker?.username) cause = 'player'
  else if (likelyAttacker) cause = 'mob'
  else if (bot.entity.isInWater) cause = 'drowning'
  state.last_damage = {
    at: Date.now() / 1000,
    amount: round(amount),
    cause,
    position: [round(position.x), round(position.y), round(position.z)],
    health: bot.health,
    source: likelyAttacker ? combatTargetLabel(likelyAttacker) : 'unknown',
  }
  survivalAction(`damage ${round(amount)} (${cause}) health=${bot.health}`)

  if (cause === 'lava') return void lavaEscape()
  if (cause === 'suffocation') return void suffocationRescue()
  if (typeof bot.oxygenLevel === 'number' && bot.oxygenLevel <= 6 && bot.entity.isInWater) {
    return void swimUpForAir()
  }
  if ((cause === 'mob' || cause === 'player') && automation.autoCounterattack && likelyAttacker) {
    if (!isOwnerEntity(likelyAttacker) || automation.counterattackOwner) {
      return void runAutomaticCombat('counterattack', likelyAttacker)
    }
  }
  if (cause === 'mob') return void applyMobPolicy()
  if (Date.now() - damageReportAt > 8000) {
    damageReportAt = Date.now()
    void speak(`我受到伤害了（${cause}），生命 ${bot.health}/20。`)
  }
}

async function autoEatIfHungry() {
  const threshold = Number(config?.survival?.auto_eat_food_level ?? 7)
  if (bot.food > threshold || autoEating) return
  survivalAction('auto eat')
  // Eating pauses digging / attacking / swimming path control.
  if (foreground.command) await cancelForeground('自动进食')
  if (survival.active) return
  try {
    await eatBestFood(true)
  } catch (error) {
    log(`Auto eat unavailable: ${error.message}`, 'warn')
    if (Date.now() - follow.lastReportAt > 15_000) {
      follow.lastReportAt = Date.now()
      void speak('我饿了，但背包里没有能直接吃的食物。')
    }
  }
}

async function speak(text) {
  if (!bot || !state.connected || !text) return false
  try {
    bot.chat(String(text).slice(0, 240))
    return true
  } catch (error) {
    log(`Chat failed: ${error.message}`, 'error')
    return false
  }
}

function refreshRuntime() {
  if (!bot?.entity) {
    state.runtime = { position: null, players: [], entities: [], inventory: [] }
    return
  }
  const self = bot.entity.position
  const players = Object.entries(bot.players || {})
    .filter(([name, player]) => name !== bot.username && player?.entity?.position)
    .map(([name, player]) => ({
      entity_id: player.entity.id,
      name,
      username: name,
      x: round(player.entity.position.x),
      y: round(player.entity.position.y),
      z: round(player.entity.position.z),
      distance: round(self.distanceTo(player.entity.position)),
      horizontal_distance: round(Math.hypot(
        self.x - player.entity.position.x,
        self.z - player.entity.position.z,
      )),
    }))
    .sort((a, b) => a.distance - b.distance)

  const entities = Object.values(bot.entities || {})
    .filter(entity => entity?.position && entity !== bot.entity)
    .map(entity => ({
      entity_id: entity.id,
      name: entity.username || entity.name || entity.displayName || 'entity',
      username: entity.username || '',
      type: entity.type || '',
      hostile: isHostileEntity(entity),
      x: round(entity.position.x),
      y: round(entity.position.y),
      z: round(entity.position.z),
      distance: round(self.distanceTo(entity.position)),
    }))
    .sort((a, b) => a.distance - b.distance)
    .slice(0, 32)

  const inventory = (bot.inventory?.items() || []).map(item => {
    const potion = potionIdentity(item, potionNamesById)
    return {
      slot: item.slot,
      name: item.name,
      display_name: item.displayName,
      count: item.count,
      stack_size: item.stackSize,
      food_points: foodPoints(item),
      selected: item.slot === 36 + Number(bot.quickBarSlot || 0),
      ...(potion ? { potion } : {}),
    }
  })

  const position = { x: round(self.x), y: round(self.y), z: round(self.z) }
  refreshStations()
  const held = bot.heldItem
  const heldDetail = held
    ? {
        name: held.name,
        count: held.count,
        durability: {
          used: typeof held.durabilityUsed === 'number' ? held.durabilityUsed : null,
          max: Number(held.maxDurability ?? bot?.registry?.itemsByName?.[held.name]?.maxDurability ?? 0) || null,
        },
      }
    : null
  state.runtime = {
    position,
    players,
    entities,
    inventory_scope: 'full',
    inventory,
    hotbar: inventory.filter(item => item.slot >= 36 && item.slot <= 44),
    selected_hotbar_slot: Number(bot.quickBarSlot || 0),
    held_item: bot.heldItem?.name || '',
    held_item_detail: heldDetail,
    potion_registry: {
      size: potionNamesById.size,
      names: [...potionNamesById.values()],
      source: potionRegistrySource,
    },
    stations: {
      crafting_tables: stationView(stationCache.tables),
      furnaces: stationView(stationCache.furnaces),
      brewing_stands: stationView(stationCache.brewingStands),
      cauldrons: stationView(stationCache.cauldrons),
    },
    game_mode: bot.game?.gameMode || '',
    dimension: bot.game?.dimension || '',
    time_of_day: bot.time?.timeOfDay ?? null,
    weather: bot.isRaining ? 'rain' : 'clear',
    vitals: { health: bot.health ?? null, food: bot.food ?? null, saturation: bot.foodSaturation ?? null },
  }
  state.environment = {
    in_water: Boolean(bot.entity.isInWater),
    in_lava: Boolean(bot.entity.isInLava),
    on_ground: Boolean(bot.entity.onGround),
    is_sleeping: Boolean(bot.isSleeping),
    state: bot.entity.isInWater ? 'swimming' : bot.entity.isInLava ? 'in_lava' : bot.entity.onGround ? 'grounded' : 'airborne',
  }
  syncSurvivalState()
  state.last_status = `Online as ${bot.username} at (${position.x}, ${position.y}, ${position.z}), health ${bot.health ?? '?'}, food ${bot.food ?? '?'}/20`
}

function round(value) {
  return Math.round(Number(value) * 100) / 100
}

function beginFollow(name) {
  follow.enabled = true
  follow.target = publicPlayerName(name)
  follow.active = false
  follow.stalls = 0
  follow.lastPosition = null
  follow.lastDistance = null
  follow.goalPosition = null
  follow.lastPlanAt = 0
  follow.recoveries = 0
  follow.cooldownUntil = 0
  state.follow_enabled = true
  state.follow_target = follow.target
  state.follow_mode = foreground.command ? 'paused_for_task' : 'awaiting_target'
  log(`Persistent follow enabled: ${follow.target}`)
}

function disableFollow() {
  follow.enabled = false
  follow.target = ''
  follow.active = false
  follow.stalls = 0
  follow.goalPosition = null
  follow.recoveries = 0
  follow.cooldownUntil = 0
  state.follow_enabled = false
  state.follow_target = ''
  state.follow_mode = 'off'
  state.follow_watchdog = { status: 'off', checked_at: Date.now() / 1000 }
  stopPathfinder()
}

async function reconcileFollow() {
  if (!bot?.entity || !state.connected || !follow.enabled) return
  if (survival.active) {
    state.follow_mode = 'paused_emergency'
    state.follow_watchdog = {
      status: 'paused_emergency',
      target: follow.target,
      emergency: survival.reason,
      checked_at: Date.now() / 1000,
    }
    return
  }
  if (foreground.command || planActive) {
    state.follow_mode = 'paused_for_task'
    state.follow_watchdog = {
      status: 'paused_for_task',
      target: follow.target,
      foreground: foreground.command || 'plan',
      checked_at: Date.now() / 1000,
    }
    return
  }
  if (automation.combatBusy) {
    state.follow_mode = 'paused_for_combat'
    state.follow_watchdog = {
      status: 'paused_for_combat', target: follow.target,
      combat: automation.combatMode, checked_at: Date.now() / 1000,
    }
    return
  }
  if (follow.pitEscape.busy) {
    // A pit escape is in flight (several seconds of jump-and-place); no
    // replanning or stall bookkeeping may run underneath it.
    state.follow_mode = 'pillar_escape'
    state.follow_watchdog = {
      status: 'pillar_escape', target: follow.target,
      checked_at: Date.now() / 1000,
    }
    return
  }

  const target = bot.players?.[follow.target]?.entity
  if (!target) {
    if (follow.active) stopPathfinder()
    state.follow_mode = 'target_missing'
    state.follow_watchdog = {
      status: 'target_missing', target: follow.target,
      checked_at: Date.now() / 1000, teleport_fallback: false,
    }
    if (Date.now() - follow.lastReportAt > 30_000) {
      follow.lastReportAt = Date.now()
      void speak(`暂时看不到 ${follow.target}，持续跟随仍开启；重新看到你后我会继续走过去。`)
    }
    return
  }

  follow.lastVisibleAt = Date.now()
  const distance = distanceTo(target)
  const now = Date.now()
  if (!bot.entity.onGround && !bot.entity.isInWater) {
    stopPathfinder()
    follow.cooldownUntil = now + 1500
    state.follow_mode = 'waiting_to_land'
    state.follow_watchdog = {
      status: state.follow_mode, target: follow.target,
      distance: distance == null ? null : round(distance), checked_at: now / 1000,
    }
    return
  }
  if (now < follow.cooldownUntil) {
    state.follow_mode = 'recovery_cooldown'
    return
  }

  const targetMoved = !follow.goalPosition || target.position.distanceTo(follow.goalPosition) >= FOLLOW_REPLAN_DISTANCE
  const planStale = now - follow.lastPlanAt >= FOLLOW_REPLAN_INTERVAL_MS
  if (!follow.active || targetMoved || planStale) {
    setFollowMovements()
    // Static, bounded snapshots avoid the unbounded CPU churn observed with a
    // dynamic GoalFollow when terrain changes during a fall.
    bot.pathfinder.setGoal(
      new goals.GoalNear(target.position.x, target.position.y, target.position.z, FOLLOW_DISTANCE),
      false,
    )
    follow.active = true
    follow.goalPosition = target.position.clone()
    follow.lastPlanAt = now
    if (!targetMoved) follow.stalls = 0
    log(`Bounded follow goal refreshed for ${follow.target}`)
  }

  const current = bot.entity.position.clone()
  const displacement = follow.lastPosition ? current.distanceTo(follow.lastPosition) : 0
  if (distance != null && follow.lastDistance != null && distance > FOLLOW_DISTANCE + 1) {
    const progressed = distance < follow.lastDistance - 0.15 || displacement > 0.35
    follow.stalls = progressed ? 0 : follow.stalls + 1
  } else {
    follow.stalls = 0
  }
  follow.lastPosition = current
  follow.lastDistance = distance
  const near = distance != null && distance <= FOLLOW_DISTANCE + 0.6
  if (near) {
    stopPathfinder()
    follow.recoveries = 0
    follow.stalls = 0
    state.follow_mode = 'near_target'
  } else if (follow.stalls >= FOLLOW_STALL_CHECKS) {
    stopPathfinder()
    clearControls()
    follow.recoveries += 1
    follow.stalls = 0
    follow.cooldownUntil = now + Math.min(10_000, 1000 * (2 ** follow.recoveries))
    state.follow_mode = follow.recoveries >= 3 ? 'stalled_waiting' : 'replanning'
    if (follow.recoveries === 1 || follow.recoveries === 3) {
      void speak(
        follow.recoveries >= 3
          ? '跟随路线连续不可达，我先停在安全位置等待环境变化；跟随开关仍保留，可随时取消。'
          : '跟随路线暂时卡住，我正在停止旧路线并重新计算。',
      )
    }
    // Pit escape: a stalled follow with the owner ABOVE is almost always the
    // bot sitting in a hole pathfinder cannot climb out of (it never pills on
    // its own).  Jump-and-place a couple of junk blocks to get back to the
    // owner's level, then let the normal replan take over.  Bounded: needs
    // scaffold blocks, fires at most every 15s and only on the first two
    // recoveries (or any recovery when the level gap is obvious).
    const levelGap = target.position ? target.position.y - bot.entity.position.y : 0
    if (
      !follow.pitEscape.busy &&
      follow.recoveries <= 2 &&
      now - follow.pitEscape.lastAt >= 15_000 &&
      findJunkBlock() &&
      (levelGap >= 1.5 || follow.recoveries >= 2)
    ) {
      follow.pitEscape.busy = true
      follow.pitEscape.lastAt = now
      state.follow_mode = 'pillar_escape'
      log(`Follow stalled with owner ${round(levelGap)} blocks above; pillaring up to escape the pit`)
      void pillarUp(2, null)
        .then(() => {
          follow.recoveries = 0
          log('Follow pit escape finished; replanning the route')
        })
        .catch(error => log(`Follow pit escape failed: ${error.message}`, 'warn'))
        .finally(() => {
          follow.pitEscape.busy = false
          follow.cooldownUntil = Date.now() + 1500
          follow.active = false
          void reconcileFollow().catch(() => {})
        })
    }
  } else {
    state.follow_mode = 'following'
  }
  state.follow_watchdog = {
    status: state.follow_mode,
    target: follow.target,
    distance: distance == null ? null : round(distance),
    stalls: follow.stalls,
    recoveries: follow.recoveries,
    displacement: round(displacement),
    checked_at: Date.now() / 1000,
    teleport_fallback: false,
  }
  state.movement = {
    state: state.follow_mode,
    reason: `follow:${follow.target}`,
    method: 'bounded_static_goal',
    distance: distance == null ? null : round(distance),
  }
}

async function cancelForeground(reason, announce = false, replacement = '') {
  if (!foreground.command) return false
  const old = foreground.command
  foreground.token += 1
  foreground.command = ''
  foreground.verb = ''
  // collectBlock performs its own pathfinder cleanup asynchronously.  Waiting
  // here prevents the previous collection goal from racing the replacement
  // task (especially a resumed follow goal).
  try { await bot?.collectBlock?.cancelTask?.() } catch {}
  try { bot?.pvp?.stop?.() } catch {}
  try { bot?.stopDigging?.() } catch {}
  stopPathfinder()
  clearControls()
  activityFinish('cancelled', `被新指令取消：${reason}`)
  if (announce) await speak(`新任务“${replacement}”优先，已取消“${old}”。`)
  // Re-engage persistent follow immediately instead of waiting for the next
  // watchdog tick, so a cancel never leaves the bot frozen mid-motion with
  // follow enabled.  A plan in flight keeps follow deferred (its terminal
  // state re-engages it).
  if (follow.enabled && !planActive) {
    follow.active = false
    state.follow_mode = 'resuming'
    void reconcileFollow().catch(() => {})
  }
  return true
}

function assertCurrent(token) {
  if (foreground.token !== token) {
    const error = new Error('task superseded')
    error.code = 'CANCELLED'
    throw error
  }
}

function timeout(promise, milliseconds, label) {
  let timer
  return Promise.race([
    promise,
    new Promise((_, reject) => {
      timer = setTimeout(() => reject(Object.assign(new Error(`${label} timed out`), { code: 'TIMEOUT' })), milliseconds)
    }),
  ]).finally(() => clearTimeout(timer))
}

async function runForeground(parsed, quiet = false, taskId = null, origin = 'saya') {
  // emergency > stop > new task > current task > follow: while an emergency
  // rescue is running, ordinary tasks are refused (status queries stay free).
  if (survival.active && parsed.verb !== 'status') {
    await speak(`我正在紧急自救（${survival.reason}），暂时不能执行“${parsed.raw}”，稍后再叫我。`)
    return
  }
  pauseAutoDiscard()
  cancelAutoCombat('被主人任务打断')
  await cancelForeground('superseded', true, parsed.raw)
  const token = ++foreground.token
  foreground.command = parsed.raw
  foreground.verb = parsed.verb
  foreground.startedAt = Date.now()
  if (follow.enabled) {
    stopPathfinder()
    state.follow_mode = 'paused_for_task'
    if (!quiet) await speak(`开始“${parsed.raw}”，跟随暂时脱离；任务结束后恢复。`)
  }
  activityStart(parsed.raw, parsed.verb, taskId)
  emitState()

  try {
    // Pathfinding cannot pillar: a bot standing in a 1-block pit reports
    // "no path" forever.  On a no-path failure, pillar up with junk blocks
    // (if any are carried) and retry the SAME task once.
    let escapedOnce = false
    let message
    for (;;) {
      try {
        message = await timeout(executeTask(parsed, token), TASK_TIMEOUT_MS, parsed.verb)
        break
      } catch (taskError) {
        const text = String(taskError?.message || taskError)
        const isNoPath = /no path|NoPath|找不到.{0,8}(路|路径)|路径不可达|unreachable|没有可达/i.test(text)
        const canEscape = isNoPath && !escapedOnce && !survival.active && findJunkBlock()
          && bot?.entity && foreground.token === token
        if (!canEscape) throw taskError
        escapedOnce = true
        activityProgress('escaping', 5, '找不到路，尝试向上搭方块脱困后重试')
        try {
          await pillarUp(2, token)
        } catch (escapeError) {
          log(`Pillar escape failed: ${escapeError.message}`, 'warn')
          throw taskError
        }
      }
    }
    assertCurrent(token)
    activityFinish('completed', message)
    if (!quiet) await speak(`任务完成：${message}${follow.enabled ? `；继续跟随 ${follow.target}` : ''}`)
  } catch (error) {
    if (error?.code === 'CANCELLED' || foreground.token !== token) return
    const resultState = error?.code === 'TIMEOUT' ? 'timeout' : 'failed'
    const message = String(error?.message || error)
    if (error?.code === 'TIMEOUT' && foreground.token === token) {
      // Promise.race cannot cancel the underlying task by itself.  Invalidate
      // its token so combat/collection loops stop at their next boundary.
      foreground.token += 1
      foreground.command = ''
      foreground.verb = ''
      try { await bot?.collectBlock?.cancelTask?.() } catch {}
      stopPathfinder()
      clearControls()
    }
    activityFinish(resultState, message)
    if (!quiet) await speak(`任务失败：${message}${follow.enabled ? '；持续跟随仍保持开启' : ''}`)
    log(`Task failed (${parsed.verb}): ${message}`, 'error')
  } finally {
    if (foreground.token === token) {
      const finishedVerb = foreground.verb
      foreground.command = ''
      foreground.verb = ''
      clearControls()
      // Leaving a combat/mining state: put the weapon/tool away so idle
      // swings cannot wear it.
      if (SHEATHE_AFTER_VERBS.has(finishedVerb)) await sheatheHeldTool()
      // Plan steps stay follow-free between steps: the plan executor keeps
      // sending plan_state, and follow resumes only after the whole plan
      // reports finished.  Single commands re-engage follow right away.
      if (follow.enabled && origin !== 'plan') {
        follow.active = false
        state.follow_mode = 'resuming'
        await reconcileFollow()
      }
      emitState()
    }
  }
}

async function executeTask(parsed, token) {
  if (!bot?.entity || !state.connected) throw new Error('机器人尚未连接服务器')
  setTaskMovements()
  const { verb, args } = parsed

  if (verb === 'come') {
    const name = publicPlayerName(args)
    const entity = bot.players?.[name]?.entity
    if (!entity) throw new Error(`当前看不到玩家 ${name}`)
    const before = distanceTo(entity)
    activityProgress('moving', 5, `正在前往 ${name}，当前距离 ${round(before)} 格`)
    await bot.pathfinder.goto(new goals.GoalNear(entity.position.x, entity.position.y, entity.position.z, 2))
    assertCurrent(token)
    const current = bot.players?.[name]?.entity
    const after = current ? distanceTo(current) : null
    return `已来到 ${name} 身边${after == null ? '' : `（距离 ${round(after)} 格）`}`
  }

  if (verb === 'goto') {
    const [x, y, z] = args
    activityProgress('moving', 5, `正在前往坐标 ${x}, ${y}, ${z}`)
    await bot.pathfinder.goto(new goals.GoalNear(x, y, z, 1))
    assertCurrent(token)
    return `已到达坐标附近 (${round(bot.entity.position.x)}, ${round(bot.entity.position.y)}, ${round(bot.entity.position.z)})`
  }

  if (verb === 'chop') return collectBlocks('_log', Number(args), token)
  if (verb === 'collect') return collectBlocks(args.block, args.count, token)

  if (verb === 'mine_at') {
    const block = bot.blockAt(new Vec3(Math.floor(args[0]), Math.floor(args[1]), Math.floor(args[2])))
    if (!block || block.name === 'air') throw new Error('目标坐标没有可挖掘方块')
    activityProgress('mining', 15, `正在接近并挖掘 ${block.name}`)
    await bot.collectBlock.collect(block)
    assertCurrent(token)
    return `已挖掘 ${block.name}`
  }

  if (verb === 'mine_below') {
    const position = bot.entity.position.floored().offset(0, -1, 0)
    const block = bot.blockAt(position)
    if (!isSafeDigTarget(block)) throw new Error('脚下没有可安全挖掘的方块')
    activityProgress('mining', 15, `正在挖掘脚下的 ${block.name}`, {
      target: [block.position.x, block.position.y, block.position.z],
    })
    await bot.collectBlock.collect(block)
    assertCurrent(token)
    return `已挖掘脚下的 ${block.name}`
  }

  if (verb === 'mine_nearby') {
    let block = null
    try {
      const cursorBlock = bot.blockAtCursor(5)
      if (isSafeDigTarget(cursorBlock) && canReach(cursorBlock)) block = cursorBlock
    } catch (error) {
      log(`Cursor block lookup unavailable: ${error.message}`, 'warn')
    }
    if (!block) {
      block = bot.findBlocks({
        matching: candidate => isSafeDigTarget(candidate),
        maxDistance: 5,
        count: 64,
      })
        .map(position => bot.blockAt(position))
        .filter(candidate => isSafeDigTarget(candidate) && canReach(candidate))
        .sort((a, b) => bot.entity.position.distanceTo(a.position) - bot.entity.position.distanceTo(b.position))[0]
    }
    if (!block) throw new Error('附近没有可见且可安全挖掘的方块')
    activityProgress('mining', 15, `正在挖掘附近的 ${block.name}`, {
      target: [block.position.x, block.position.y, block.position.z],
    })
    await bot.collectBlock.collect(block)
    assertCurrent(token)
    return `已挖掘附近的 ${block.name}`
  }

  if (verb === 'pickup') {
    const item = nearestEntity(entity => entity.name === 'item' || entity.displayName === 'Item')
    if (!item) throw new Error('附近没有可见掉落物')
    activityProgress('picking_up', 20, `正在接近掉落物，距离 ${round(distanceTo(item))} 格`)
    await bot.collectBlock.collect(item)
    assertCurrent(token)
    return '已捡起掉落物'
  }

  if (verb === 'inventory') {
    const items = bot.inventory.items()
    const total = items.reduce((sum, entry) => sum + entry.count, 0)
    const foods = items
      .filter(entry => foodPoints(entry) > 0)
      .reduce((sum, entry) => sum + entry.count, 0)
    return `背包共 ${items.length} 组 / ${total} 件物品，其中食物 ${foods} 件`
  }

  if (verb === 'sleep') return sleepInNearbyBed(token)
  if (verb === 'pillar') return pillarUp(Number(args) || 1, token)

  if (verb === 'chest_find') {
    const storages = findStorages(32)
    if (!storages.length) throw new Error('32 格内没有找到箱子/木桶')
    activityProgress('chest_find', 60, `找到 ${storages.length} 个容器`)
    const lines = storages.slice(0, 6).map((block, index) => {
      const distance = round(bot.entity.position.distanceTo(block.position))
      return `${index + 1}. ${storageLabel(block)} (${block.position.x}, ${block.position.y}, ${block.position.z}) 距离 ${distance} 格`
    })
    return `附近共 ${storages.length} 个容器：\n${lines.join('\n')}`
  }
  if (verb === 'chest_deposit') return chestTransfer('deposit', args, token)
  if (verb === 'chest_withdraw') return chestTransfer('withdraw', args, token)

  if (verb === 'equip') {
    const wanted = String(args || '')
    const item = inventoryItemMatching(wanted)
    if (!item) throw new Error(`背包里没有 ${wanted}`)
    const destination = armorDestinationFor(item.name)
    const equipVerb = destination === 'head' ? '戴上'
      : destination === 'torso' || destination === 'legs' || destination === 'feet' ? '穿上'
        : destination === 'off-hand' ? '换到副手拿' : '手持'
    activityProgress('equipping', 40, `正在${equipVerb} ${item.name}`)
    await bot.equip(item, destination)
    assertCurrent(token)
    // Verify the item really landed in the destination: armor moves go
    // through inventory window clicks and used to silently stay in hand.
    if (!equippedInDestination(item.name, destination)) {
      await wait(300)
      if (!equippedInDestination(item.name, destination)) {
        throw new Error(`没能把 ${item.name} ${equipVerb}（装备槽位校验失败）`)
      }
    }
    return `已${equipVerb} ${item.name}`
  }

  if (verb === 'drop') {
    // Legacy string form ("one"|"stack") or {item, count|'all'} object.
    if (typeof args === 'object' && args !== null) {
      const wanted = String(args.item || '')
      const variant = parseVariantItem(wanted)
      if (variant) {
        // Variant targets never go through bot.toss(type, metadata, n):
        // all 1.21.5 potions share the same type+metadata and the generic
        // lookup would grab the first same-type bottle, not the wanted one.
        const available = countWanted(wanted, variant)
        if (!available) throw new Error(`背包里没有 ${wanted}`)
        const requested = args.count === 'all' ? available : Math.max(1, Number(args.count) || 1)
        if (available < requested) {
          throw new Error(`背包里只有 ${available} 瓶 ${wanted}，不够扔出 ${requested} 瓶`)
        }
        const dropped = await tossWanted(wanted, variant, requested, token)
        const left = countWanted(wanted, variant)
        return `已扔出 ${wanted}×${dropped}，背包还剩 ${left} 瓶`
      }
      let remaining = args.count === 'all' ? Number.POSITIVE_INFINITY : Math.max(1, Number(args.count) || 1)
      const matching = bot.inventory.items().filter(entry => matchesItem(entry.name, wanted))
      if (!matching.length) throw new Error(`背包里没有 ${wanted}`)
      const total = matching.reduce((sum, entry) => sum + entry.count, 0)
      if (remaining !== Number.POSITIVE_INFINITY && total < remaining) {
        throw new Error(`背包里只有 ${total} 个 ${wanted}，不够扔出 ${remaining} 个`)
      }
      let dropped = 0
      for (const entry of matching) {
        if (remaining <= 0) break
        activityProgress('dropping', 50, `正在扔出 ${wanted}（已扔 ${dropped}）`)
        const take = remaining === Number.POSITIVE_INFINITY ? entry.count : Math.min(entry.count, remaining)
        await bot.toss(entry.type, entry.metadata, take)
        assertCurrent(token)
        remaining -= take
        dropped += take
      }
      return `已扔出 ${dropped} 个 ${wanted}`
    }
    const item = bot.heldItem || bot.inventory.items()[0]
    if (!item) throw new Error('背包里没有可以扔出的物品')
    activityProgress('dropping', 50, `正在扔出 ${item.name}`)
    if (String(args).toLowerCase() === 'all' || String(args).toLowerCase() === 'stack') await bot.tossStack(item)
    else await bot.toss(item.type, item.metadata, 1)
    return `已扔出${String(args).toLowerCase() === 'stack' ? '一组' : '一个'} ${item.name}`
  }

  if (verb === 'give') {
    return giveItem(args, token)
  }

  if (verb === 'place') {
    return placeBlockItem(String(args || ''), token)
  }

  if (verb === 'eat') return eatBestFood(false)
  if (verb === 'smelt') return smeltItem(args, token)
  if (verb === 'brew') return brewPotion(args, token)
  if (verb === 'cauldron') return useCauldron(args, token)
  if (verb === 'milk') return milkCow(args, token)
  if (verb === 'craft') return craftItem(args.item, args.count, token)

  if (verb === 'jump') {
    if (!bot.entity.onGround && !bot.entity.isInWater) throw new Error('当前不在地面或水中，无法安全起跳')
    const before = bot.entity.position.y
    let peak = before
    activityProgress('jumping', 30, '正在起跳')
    try {
      bot.setControlState('jump', true)
      for (let i = 0; i < 10; i += 1) {
        await wait(50)
        assertCurrent(token)
        peak = Math.max(peak, bot.entity.position.y)
      }
    } finally {
      bot.setControlState('jump', false)
    }
    for (let i = 0; i < 10; i += 1) {
      await wait(50)
      assertCurrent(token)
      peak = Math.max(peak, bot.entity.position.y)
    }
    return `已跳跃（最高上升约 ${round(Math.max(0, peak - before))} 格）`
  }

  if (verb === 'swim') {
    if (!bot.entity.isInWater) throw new Error('当前不在水中')
    activityProgress('swimming', 20, '正在向水面游')
    bot.setControlState('jump', true)
    bot.setControlState('forward', true)
    await wait(3000)
    clearControls()
    return '已向水面游动'
  }

  if (verb === 'attack') {
    const wanted = String(args?.target || args || 'nearest').toLowerCase()
    const requested = Math.max(1, Math.min(32, Number(args?.count) || 1))
    const detectionRange = configuredCombatRange()
    let killed = 0
    while (killed < requested) {
      assertCurrent(token)
      let target = null
      if (wanted !== 'nearest') {
        const selectedEntityId = /^#\d+$/.test(wanted) ? Number(wanted.slice(1)) : null
        if (selectedEntityId != null) {
          const selected = bot.entities?.[selectedEntityId]
          target = selected?.position && distanceTo(selected) <= detectionRange ? selected : null
        } else {
          target = bot.players?.[args?.target || args]?.entity || nearestEntity(entity =>
            [entity.name, entity.displayName, entity.username]
              .some(value => String(value || '').toLowerCase() === wanted), detectionRange)
        }
      }
      target ||= wanted === 'nearest' ? nearestEntity(entity => {
        if (!entity || entity === bot.entity || entity.username === follow.target) return false
        return entity.type === 'mob' || entity.type === 'hostile'
      }, detectionRange) : null
      if (!target) {
        throw new Error(
          killed
            ? `只确认击杀 ${killed}/${requested} 个 ${wanted}，附近没有下一个目标`
            : `附近没有可攻击的 ${wanted}`,
        )
      }
      const label = combatTargetLabel(target)
      activityProgress(
        'attacking', Math.min(95, (killed / requested) * 95),
        `正在攻击 ${label}（确认击杀 ${killed}/${requested}）`,
      )
      const result = await conservativeAttack(target, { foregroundToken: token })
      assertCurrent(token)
      if (!result.defeated) {
        throw new Error(`未确认 ${label} 死亡；攻击 ${result.swings} 次后停止，不能算作击杀`)
      }
      killed += 1
      activityProgress(
        'attacking', Math.min(95, (killed / requested) * 95),
        `已确认击杀 ${killed}/${requested} 个 ${wanted}`,
        { kills: killed, kill_target: wanted },
      )
    }
    return `已由死亡事件确认击杀 ${killed}/${requested} 个 ${wanted}`
  }

  throw new Error(`尚未实现任务 ${verb}`)
}

function nearestEntity(predicate, maxDistance = 32) {
  if (!bot?.entity) return null
  return Object.values(bot.entities || {})
    .filter(entity => entity?.position && predicate(entity))
    .map(entity => ({ entity, distance: distanceTo(entity) }))
    .filter(item => item.distance != null && item.distance <= maxDistance)
    .sort((a, b) => a.distance - b.distance)[0]?.entity || null
}

function isSafeDigTarget(block) {
  if (!block || !block.position || block.name === 'air' || block.boundingBox !== 'block') return false
  if (['bedrock', 'barrier', 'water', 'lava'].includes(block.name)) return false
  return block.diggable !== false && Number(block.hardness) >= 0
}

function canReach(block) {
  try {
    return Boolean(bot.canDigBlock(block))
  } catch {
    return false
  }
}

/** Milk nearby cows: hold an empty bucket, use it on a cow, and only report
 *  success when a milk_bucket actually appears (milking consumes the bucket). */
async function milkCow(args, token) {
  const requested = Math.max(1, Math.min(3, Number(args) || 1))
  const bucket = inventoryItemMatching('bucket')
  if (!bucket) throw new Error('背包里没有铁桶，没法挤牛奶（先给我一个桶，或让我用铁锭做一个）')
  if (bucket.count < requested) throw new Error(`挤 ${requested} 桶牛奶需要 ${requested} 个铁桶，当前只有 ${bucket.count} 个`)

  let milked = 0
  for (let round = 0; round < requested; round += 1) {
    assertCurrent(token)
    const cow = nearestEntity(entity =>
      String(entity?.name || '') === 'cow' && entity.position.distanceTo(bot.entity.position) < 8, 8)
    if (!cow) throw new Error(milked
      ? `已挤到 ${milked}/${requested} 桶，附近没有下一头奶牛了`
      : '8 格内没有奶牛')
    activityProgress('milking', Math.round(((round + 1) / requested) * 90), `正在给奶牛挤奶（${round + 1}/${requested}）`)
    await bot.equip(bucket, 'hand')
    assertCurrent(token)
    await bot.lookAt(cow.position.offset(0, 1, 0), true)
    const before = inventoryCountMatching('milk_bucket')
    await bot.useOn(cow)
    await wait(600)
    if (inventoryCountMatching('milk_bucket') <= before) {
      throw new Error('挤奶没有成功：牛奶桶没有出现（奶牛可能还在冷却）')
    }
    milked += 1
  }
  return `已挤到 ${milked} 桶牛奶`
}

// Blocks acceptable for pillar-escaping a pit: common, replaceable, and
// never tools/weapons/food.  First match wins.
const PILLAR_JUNK_BLOCKS = [
  'dirt', 'cobblestone', 'cobbled_deepslate', 'netherrack', 'grass_block',
  'sandstone', 'andesite', 'diorite', 'granite', 'gravel',
]

function findJunkBlock() {
  const items = bot.inventory.items()
  for (const name of PILLAR_JUNK_BLOCKS) {
    const item = items.find(entry => entry.name === name)
    if (item) return item
  }
  return null
}

/**
 * Jump-and-place pillar: escapes 1-block-deep pits / raises the bot `count`
 * blocks.  Uses ONLY junk blocks, never tools or valuables.
 *
 * Placement timing is the whole trick: the server silently rejects a block
 * whose target cell intersects the placer's hitbox, so the block can only go
 * into the cell the feet just vacated at/after the jump APEX (~1.25 blocks
 * up, ~0.26s in).  Placing while still rising (the old fixed 180ms wait)
 * overlaps the hitbox and nothing is ever placed - the bot just jumps in
 * place.  Each layer therefore waits for apex, places onto the top face of
 * the support two cells below the (risen) feet, and VERIFIES the block
 * actually appeared and the bot ended up standing higher.
 */
async function pillarUp(count, token) {
  const total = Math.max(1, Math.min(8, Number(count) || 1))
  const blockItem = findJunkBlock()
  if (!blockItem) throw new Error('背包里没有可以垫脚的普通方块（泥土/圆石等），没法向上搭')
  if (blockItem.count < total) {
    throw new Error(`向上搭 ${total} 格需要 ${total} 个 ${blockItem.name}，只有 ${blockItem.count} 个`)
  }
  activityProgress('pillaring', 5, `正在向上搭方块（${total} 格）`)
  await bot.equip(blockItem, 'hand')
  if (token != null) assertCurrent(token)
  clearControls()
  let placed = 0
  try {
    while (placed < total) {
      if (token != null) assertCurrent(token)
      const startY = bot.entity.position.y
      let reachedApex = false
      let placedThisLayer = false
      for (let attempt = 0; attempt < 3 && !placedThisLayer; attempt += 1) {
        if (token != null) assertCurrent(token)
        bot.setControlState('jump', true)
        // Rise to the apex: feet must clear the target cell (>=1 block up,
        // upward velocity spent) before the placement can be accepted.
        const riseDeadline = Date.now() + 800
        while (Date.now() < riseDeadline) {
          const risen = bot.entity.position.y - startY
          if (risen >= 1.05 && (bot.entity.velocity.y <= 0.01 || risen >= 1.2)) break
          await wait(20)
        }
        bot.setControlState('jump', false)
        const risen = bot.entity.position.y - startY
        if (risen < 1.0) {
          // Never got high enough (low ceiling or blocked jump): placing
          // would be rejected; bail out with a diagnosis instead.
          continue
        }
        reachedApex = true
        const reference = bot.blockAt(bot.entity.position.floored().offset(0, -2, 0))
        if (!reference || reference.boundingBox !== 'block') {
          throw new Error('脚下方块未加载或不是实体方块，无法搭方块')
        }
        try {
          await bot.placeBlock(reference, new Vec3(0, 1, 0))
        } catch (error) {
          log(`Pillar place rejected: ${error.message}`, 'warn')
        }
        // Land on the placed block, then verify it actually took effect -
        // a rejected placement resolves without any error from the server.
        const landDeadline = Date.now() + 900
        while (Date.now() < landDeadline && !bot.entity.onGround) await wait(30)
        await wait(120)
        const support = bot.blockAt(bot.entity.position.floored().offset(0, -1, 0))
        if (bot.entity.position.y > startY + 0.9 && support?.boundingBox === 'block') {
          placedThisLayer = true
        } else {
          await wait(250)  // settle before re-jumping the same layer
        }
      }
      if (!placedThisLayer) {
        throw new Error(reachedApex
          ? `第 ${placed + 1} 格搭放被服务器拒绝（头顶空间可能不足）`
          : '跳不起来（头顶空间不足），无法向上搭方块')
      }
      placed += 1
      activityProgress('pillaring', Math.round(((placed + 1) / total) * 95), `已向上搭 ${placed}/${total} 格`)
    }
  } finally {
    clearControls()
  }
  return `已向上搭了 ${placed} 格方块`
}

// ------------------------------------------------------------------ chests
// Storage containers Saya can open, count and move items in/out of.  Ender
// chests are deliberately excluded (shared inventory, different semantics).
const STORAGE_BLOCK_NAMES = new Set(['chest', 'trapped_chest', 'barrel'])

function findStorages(maxDistance = 32) {
  const positions = bot.findBlocks({
    matching: block => STORAGE_BLOCK_NAMES.has(block?.name),
    maxDistance,
    count: 16,
  })
  return positions
    .map(position => bot.blockAt(position))
    .filter(Boolean)
    .sort((a, b) => bot.entity.position.distanceTo(a.position) - bot.entity.position.distanceTo(b.position))
}

function storageLabel(block) {
  if (!block) return '容器'
  if (block.name === 'barrel') return '木桶'
  return block.name === 'trapped_chest' ? '陷阱箱' : '箱子'
}

/** Every inventory entry matching a wanted item (or potion variant). */
function stacksMatching(wanted, variant) {
  if (variant) {
    return bot.inventory.items().filter(entry =>
      potionDescriptorMatches(entry, { container: variant.base, potion: variant.potion }, potionNamesById))
  }
  return bot.inventory.items().filter(entry => matchesItem(entry.name, wanted))
}

function countMatchingIn(entries, wanted, variant) {
  if (variant) {
    return entries.filter(entry =>
      potionDescriptorMatches(entry, { container: variant.base, potion: variant.potion }, potionNamesById))
      .reduce((sum, entry) => sum + entry.count, 0)
  }
  return entries.filter(entry => matchesItem(entry.name, wanted))
    .reduce((sum, entry) => sum + entry.count, 0)
}

/**
 * Deposit into / withdraw from the nearest chest-family container.  Movement
 * uses dig-disabled pathing (never tunnel to a chest), item matching follows
 * the same wanted/variant conventions as give/drop, and variant potions move
 * NBT-precisely so the wrong bottle never lands in (or leaves) the box.  The
 * result is verified against a full inventory recount afterwards.
 */
async function chestTransfer(direction, args, token) {
  const wanted = String(args?.item ?? args ?? '').trim()
  if (!wanted) throw new Error(direction === 'deposit' ? '要存什么？请指定物品' : '要取什么？请指定物品')
  const rawCount = args?.count
  const requested = rawCount === 'all'
    ? Number.POSITIVE_INFINITY
    : Math.max(1, Math.min(640, Number(rawCount) || (direction === 'deposit' ? Number.POSITIVE_INFINITY : 1)))
  const variant = parseVariantItem(wanted)
  const inventoryAtStart = countWanted(wanted, variant)
  if (direction === 'deposit' && inventoryAtStart === 0) {
    throw new Error(`背包里没有 ${wanted}`)
  }
  const storage = findStorages(32)[0]
  if (!storage) throw new Error('32 格内没有找到箱子/木桶，请把容器放在附近')
  activityProgress('chest', 15, `正在走向 ${storageLabel(storage)}（${storage.position.x}, ${storage.position.y}, ${storage.position.z}）`)
  await gotoWithoutDigging(new goals.GoalNear(
    storage.position.x + 0.5, storage.position.y + 0.5, storage.position.z + 0.5, 1.2,
  ), 'chest approach')
  assertCurrent(token)

  const window = await bot.openChest(storage)
  let moved = 0
  try {
    if (direction === 'deposit') {
      const stacks = stacksMatching(wanted, variant)
      for (const entry of stacks) {
        if (moved >= requested) break
        const take = Math.min(entry.count, requested === Number.POSITIVE_INFINITY ? entry.count : requested - moved)
        // NBT-precise for variants: deposit(type, metadata, count, nbt).
        await window.deposit(entry.type, entry.metadata, take, variant ? entry.nbt : undefined)
        assertCurrent(token)
        moved += take
      }
    } else {
      const contents = window.slots().slice(0, window.inventoryStart)
      const chestHas = countMatchingIn(contents, wanted, variant)
      if (!chestHas) throw new Error(`${storageLabel(storage)}里没有 ${wanted}`)
      const goal = requested === Number.POSITIVE_INFINITY ? chestHas : Math.min(requested, chestHas)
      for (const entry of contents) {
        if (moved >= goal) break
        const matches = variant
          ? potionDescriptorMatches(entry, { container: variant.base, potion: variant.potion }, potionNamesById)
          : entry && matchesItem(entry.name, wanted)
        if (!entry || !matches) continue
        const take = Math.min(entry.count, goal - moved)
        await window.withdraw(entry.type, entry.metadata, take, variant ? entry.nbt : undefined)
        assertCurrent(token)
        moved += take
      }
    }
  } finally {
    try { window.close() } catch {}
  }
  if (!moved) {
    throw new Error(direction === 'deposit' ? `${storageLabel(storage)}已满或没有可移动的 ${wanted}` : `没能取出 ${wanted}`)
  }
  // Verify against a fresh inventory recount (snapshot lag exists here too).
  const deadline = Date.now() + 2500
  let after = inventoryAtStart
  do {
    after = countWanted(wanted, variant)
    if (direction === 'deposit' ? after + moved === inventoryAtStart : after - moved === inventoryAtStart) break
    await wait(100)
  } while (Date.now() < deadline)
  const expected = direction === 'deposit' ? inventoryAtStart - moved : inventoryAtStart + moved
  if (after !== expected) {
    throw new Error(`存取数量校验失败：预期背包 ${expected} 个 ${wanted}，实际 ${after} 个`)
  }
  const verbText = direction === 'deposit' ? '已存入' : '已取出'
  return `${verbText} ${moved} 个 ${wanted}（${storageLabel(storage)} @ ${storage.position.x}, ${storage.position.y}, ${storage.position.z}）`
}

/** Find a bed within range and sleep in it; wake obstructing villagers by
 *  walking into them (pushing them off) before retrying. */
async function sleepInNearbyBed(token) {
  const findBed = maxDistance => bot.findBlock({
    matching: block => block?.name.endsWith('_bed'),
    maxDistance,
  })
  let bed = findBed(32)
  if (!bed) throw new Error('32 格内没有找到床')
  activityProgress('moving_to_bed', 15, '正在走向床', {
    target: [bed.position.x, bed.position.y, bed.position.z],
  })
  // Digging stays disabled for the whole approach: with the default
  // dig-enabled movements the pathfinder used to mine the bed itself when it
  // blocked a shortcut, and the arrival re-check then failed.
  await gotoWithoutDigging(new goals.GoalNear(bed.position.x, bed.position.y, bed.position.z, 1), 'bed approach')
  assertCurrent(token)

  for (let attempt = 1; attempt <= 3; attempt += 1) {
    assertCurrent(token)
    // Re-scan from live world data instead of trusting the remembered
    // position: chunk reloads and stale caches made this check fail even
    // with a bed right next to the bot.  Either half of the bed is
    // sleepable, so the closest found block is used as-is.
    bed = findBed(6) || bed
    if (!bed) throw new Error('走到附近却找不到床了（床可能被破坏或被移动）')
    try {
      await bot.sleep(bed)
      assertCurrent(token)
      return `已躺上 ${bed.name} 睡觉；发送 stop 可醒来`
    } catch (error) {
      const message = String(error?.message || error)
      if (/occup/i.test(message)) {
        // A villager is using the bed: walk into them to push them off.
        const squatter = nearestEntity(entity =>
          entity?.type === 'mob' && /villager/i.test(String(entity.name || ''))
          && entity.position.distanceTo(bed.position) < 2.2, 8)
        if (squatter) {
          activityProgress('waking_villager', 40, '床被村民占了，正在把他挤下床')
          survivalAction('push villager off bed')
          bot.setControlState('forward', true)
          await wait(1800)
          clearControls()
          await wait(600)
          continue
        }
        throw new Error('床被占用，且没找到占床的村民')
      }
      if (/already sleeping/i.test(message)) {
        return '已经在睡觉了。'
      }
      if (/not night|thunderstorm|day|monsters|can.?only sleep/i.test(message)) {
        // mineflayer's sleep() refuses client-side outside sleepable hours
        // and never sends the use packet.  Vanilla still sets the respawn
        // point when a bed is used, so right-click it ourselves: 睡觉 doubles
        // as "set spawn here" during the day.
        try {
          await bot.activateBlock(bed)
          assertCurrent(token)
          return `现在睡不着（${message}），已右键这张床，重生点设在了这里`
        } catch (clickError) {
          throw new Error(`右键床失败：${String(clickError?.message || clickError)}`)
        }
      }
      throw new Error(`睡觉失败：${message}`)
    }
  }
  throw new Error('尝试三次仍未能睡下（床可能仍被占用）')
}

function inventoryItemMatching(wanted) {
  const items = bot.inventory.items()
  return items.find(entry => entry.name === wanted) || items.find(entry => matchesItem(entry.name, wanted)) || null
}

/** Player inventory window slots: 5-8 armor (head/torso/legs/feet), 45 off-hand. */
const DESTINATION_SLOT = { head: 5, torso: 6, legs: 7, feet: 8, 'off-hand': 45 }

function equippedInDestination(name, destination) {
  const slot = DESTINATION_SLOT[destination]
  if (slot === undefined) return Boolean(bot.heldItem && bot.heldItem.name === name)
  const equipped = bot.inventory.slots[slot]
  return Boolean(equipped && equipped.name === name)
}

/**
 * Run a pathfinder goto with digging disabled so the bot can never break the
 * very block it is meant to interact with.  The old sleep flow let the
 * default dig-enabled movements plan straight through the target: the bed
 * got mined en route and the post-arrival re-check failed with
 * "床不在原位了".  Brewing stands and cauldrons share this class of bug.
 */
async function gotoWithoutDigging(goal, label) {
  // Mutate whichever movements instance the pathfinder is actually using
  // (taskMovements is the active one during foreground tasks).
  const movements = bot.pathfinder?.movements || taskMovements
  const previous = movements ? movements.canDig : null
  if (movements) movements.canDig = false
  try {
    await bot.pathfinder.goto(goal)
  } catch (error) {
    stopPathfinder()
    clearControls()
    throw Object.assign(error instanceof Error ? error : new Error(String(error)), { label })
  } finally {
    if (movements) movements.canDig = previous === null ? true : previous
  }
}

/** Deliver items to a player: approach, face them, toss, watch the pickup. */
/** Count inventory items matching a wanted name or variant key ("potion/healing"). */
function countWanted(wanted, variant) {
  if (variant) {
    return bot.inventory.items()
      .filter(entry => potionDescriptorMatches(entry, { container: variant.base, potion: variant.potion }, potionNamesById))
      .reduce((sum, entry) => sum + entry.count, 0)
  }
  return bot.inventory.items()
    .filter(entry => matchesItem(entry.name, wanted))
    .reduce((sum, entry) => sum + entry.count, 0)
}

/**
 * Toss exactly `amount` of a wanted item/variant.  For variant targets
 * (potion/healing style) this is SLOT-PRECISE: 1.21.5 potions all share one
 * item type + metadata, bot.toss(type, metadata, n) would grab the FIRST
 * same-type stack (possibly the wrong potion), so each round re-selects the
 * exact Item object and tossStacks it (potions never stack).  Re-selection
 * also survives the slot shifts each toss causes.
 */
async function tossWanted(wanted, variant, amount, token) {
  let done = 0
  while (done < amount) {
    const entry = variant
      ? bot.inventory.items().find(item => potionDescriptorMatches(item, { container: variant.base, potion: variant.potion }, potionNamesById))
      : bot.inventory.items().find(item => matchesItem(item.name, wanted))
    if (!entry) break
    const take = Math.min(entry.count, amount - done)
    if (take >= entry.count) await bot.tossStack(entry)
    else await bot.toss(entry.type, entry.metadata, take)
    assertCurrent(token)
    done += take
  }
  return done
}

async function giveItem(args, token) {
  const wanted = String(args?.item || '')
  const count = Math.max(1, Number(args?.count) || 1)
  const name = publicPlayerName(args?.target || 'Doctor')
  const variant = parseVariantItem(wanted)
  const unit = variant ? '瓶' : '个'
  const total = countWanted(wanted, variant)
  if (total < count) throw new Error(`背包里只有 ${total} ${unit} ${wanted}，不够交付 ${count} ${unit}`)

  let target = bot.players?.[name]?.entity
  if (!target) {
    // The owner entity can lag a moment behind (chunk reload / just
    // teleported).  Waiting a few beats beats an instant failure that used
    // to cascade into retry->replan->suspend while the item sat in hand.
    const visibleDeadline = Date.now() + 10_000
    while (Date.now() < visibleDeadline && !target) {
      assertCurrent(token)
      activityProgress('moving', 5, `正在等待看到 ${name} 以交付 ${wanted}×${count}`)
      await wait(500)
      target = bot.players?.[name]?.entity
    }
  }
  if (!target) throw new Error(`当前看不到玩家 ${name}`)
  if (distanceTo(target) > 2.5) {
    activityProgress('moving', 10, `正在靠近 ${name} 以交付 ${wanted}×${count}`)
    await bot.pathfinder.goto(new goals.GoalNear(target.position.x, target.position.y, target.position.z, 2))
    assertCurrent(token)
    target = bot.players?.[name]?.entity
    if (!target) throw new Error(`交付时看不到 ${name} 了`)
  }

  await bot.lookAt(target.position.offset(0, 1.5, 0), true)
  const itemEntitiesBefore = new Set(
    Object.values(bot.entities || {})
      .filter(entity => entity.name === 'item' || entity.displayName === 'Item')
      .map(entity => entity.id),
  )
  const delivered = await tossWanted(wanted, variant, count, token)
  if (delivered < count) {
    throw new Error(`只丢出了 ${delivered}/${count} ${unit} ${wanted}，交付中断`)
  }

  const afterToss = countWanted(wanted, variant)
  if (afterToss > total - delivered) {
    throw new Error(`丢出 ${wanted} 后背包数量没有减少，交付未确认`)
  }

  // Delivery means the requested stack left Saya's inventory.  Pickup is a
  // useful extra signal, but must not hold the multi-step plan for 20 seconds
  // or mistake an unrelated old ground item for the delivered one.
  activityProgress('delivering', 80, `已丢出 ${wanted}×${delivered}，等待 ${name} 拾取`)
  const deadline = Date.now() + 4_000
  let picked = false
  while (Date.now() < deadline) {
    assertCurrent(token)
    const deliveredEntity = nearestEntity(entity =>
      (entity.name === 'item' || entity.displayName === 'Item') && !itemEntitiesBefore.has(entity.id), 5)
    if (!deliveredEntity) { picked = true; break }
    await wait(600)
  }
  return picked
    ? `已把 ${wanted}×${delivered} 交给 ${name}（已被拾取）`
    : `${wanted}×${delivered} 已丢在 ${name} 附近地上，尚未被拾取`
}

/** Place a block item from the inventory on a safe adjacent spot. */
async function placeBlockItem(wanted, token) {
  const item = inventoryItemMatching(wanted)
  if (!item) throw new Error(`背包里没有可放置的 ${wanted}`)
  const feet = bot.entity.position.floored()
  const candidates = []
  const isAir = block => ['air', 'cave_air', 'void_air'].includes(block?.name)
  for (let radius = 1; radius <= 3; radius += 1) {
    for (let dx = -radius; dx <= radius; dx += 1) {
      for (let dz = -radius; dz <= radius; dz += 1) {
        if (Math.max(Math.abs(dx), Math.abs(dz)) !== radius) continue
        for (const dy of [0, -1, 1]) {
          const target = feet.offset(dx, dy, dz)
          const support = bot.blockAt(target.offset(0, -1, 0))
          const at = bot.blockAt(target)
          const above = bot.blockAt(target.offset(0, 1, 0))
          if (
            support?.boundingBox === 'block' &&
            !DANGEROUS_FLUIDS.has(support.name) &&
            !FALLING_BLOCKS.has(support.name) &&
            isAir(at) && isAir(above) &&
            bot.entity.position.distanceTo(target.offset(0.5, 0.5, 0.5)) <= 4.5
          ) {
            candidates.push({ support, target })
          }
        }
      }
    }
  }
  if (!candidates.length) throw new Error('4 格内没有安全的空位可以放置，请移动到开阔一点的地方')
  await bot.equip(item, 'hand')
  assertCurrent(token)
  const failures = []
  for (const spot of candidates) {
    assertCurrent(token)
    activityProgress('placing', 60, `正在尝试放置 ${item.name}`, {
      target: [spot.target.x, spot.target.y, spot.target.z],
    })
    try {
      await bot.placeBlock(spot.support, new Vec3(0, 1, 0))
      await wait(350)
      const placed = bot.blockAt(spot.target)
      if (placed?.name === item.name) {
        stationCache.at = 0
        return `已在 (${spot.target.x}, ${spot.target.y}, ${spot.target.z}) 放置 ${item.name}`
      }
      failures.push(`${spot.target}:当前${placed?.name || '未知'}`)
    } catch (error) {
      failures.push(`${spot.target}:${error.message}`)
    }
  }
  throw new Error(`尝试了 ${candidates.length} 个安全位置仍未放好 ${item.name}：${failures[0] || '未知原因'}`)
}

async function collectBlocks(wanted, count, token) {
  const requested = Math.max(1, Math.min(32, Number(count) || 1))
  const inventoryAtStart = new Map()
  for (const item of bot.inventory.items()) {
    inventoryAtStart.set(item.name, (inventoryAtStart.get(item.name) || 0) + item.count)
  }
  const expectedProducts = new Set()
  const itemNameForId = id => {
    const direct = bot.registry?.items?.[Number(id)]
    if (direct?.name) return direct.name
    return Object.values(bot.registry?.itemsByName || {})
      .find(item => Number(item.id) === Number(id))?.name || ''
  }
  const productsForBlock = block => {
    const registryBlock = bot.registry?.blocksByName?.[block?.name]
    const names = [...new Set((registryBlock?.drops || [])
      .map(itemNameForId).filter(Boolean))]
    // Self-dropping/family blocks remain measurable even on registries that
    // omit a `drops` array.
    return names.length ? names : [block?.name || String(wanted)]
  }
  const gainedExpectedItems = () => {
    const current = new Map()
    for (const item of bot.inventory.items()) {
      current.set(item.name, (current.get(item.name) || 0) + item.count)
    }
    let total = 0
    for (const [name, currentCount] of current) {
      if (![...expectedProducts].some(product => matchesItem(name, product))) continue
      total += Math.max(0, currentCount - (inventoryAtStart.get(name) || 0))
    }
    return total
  }
  let collected = 0
  let mined = 0
  const attempted = new Set()
  while (collected < requested) {
    assertCurrent(token)
    const positions = bot.findBlocks({
      matching: block => matchesBlock(block?.name, wanted),
      maxDistance: 64,
      count: Math.min(128, requested * 12),
    })
    const blocks = positions
      .map(position => bot.blockAt(position))
      .filter(block => block && !attempted.has(block.position.toString()))
      .sort((a, b) => bot.entity.position.distanceTo(a.position) - bot.entity.position.distanceTo(b.position))
    const block = blocks[0]
    if (!block) {
      if (collected) {
        throw new Error(`只实际获得 ${collected}/${requested} 个目标物品，附近已没有可达的 ${wanted}`)
      }
      throw new Error(`64 格内没有找到可达的 ${wanted}`)
    }
    for (const product of productsForBlock(block)) expectedProducts.add(product)
    attempted.add(block.position.toString())
    activityProgress(
      'collecting', Math.max(5, (collected / requested) * 95),
      `正在采集 ${block.name}（实际获得 ${collected}/${requested}）`,
      {
        target: [block.position.x, block.position.y, block.position.z],
        expected_items: [...expectedProducts],
      },
    )
    try {
      await bot.collectBlock.collect(block)
      assertCurrent(token)
      mined += 1
      // collectBlock normally waits for pickup, but allow a short inventory
      // propagation window.  A broken block without a matching inventory
      // increase is never counted as task progress.
      const pickupDeadline = Date.now() + 2500
      do {
        collected = gainedExpectedItems()
        if (collected >= requested) break
        await wait(100)
      } while (Date.now() < pickupDeadline)
      activityProgress(
        'collecting', Math.min(95, (collected / requested) * 95),
        `已挖掘 ${mined} 个方块，实际获得 ${collected}/${requested} 个 ${[...expectedProducts].join('/')}`,
        { result_items: [...expectedProducts], result_count: collected, mined_blocks: mined },
      )
    } catch (error) {
      log(`Collect candidate failed at ${block.position}: ${error.message}`, 'warn')
      if (attempted.size >= Math.min(12, positions.length)) break
    }
  }
  if (collected < requested) {
    throw new Error(`采集未达标：只实际获得 ${collected}/${requested} 个 ${[...expectedProducts].join('/') || wanted}`)
  }
  return `采集达标：实际获得 ${collected}/${requested} 个 ${[...expectedProducts].join('/')}（挖掘 ${mined} 个方块）`
}

async function eatBestFood(automatic) {
  if (autoEating) throw new Error('已经在进食')
  const foods = bot.inventory.items()
    .filter(item => foodPoints(item) > 0)
    .sort((a, b) => foodPoints(b) - foodPoints(a))
  const food = foods[0]
  if (!food) throw new Error('背包里没有可直接食用的食物')
  autoEating = true
  try {
    activityProgress('eating', 40, `正在食用 ${food.name}`)
    await bot.equip(food, 'hand')
    await bot.consume()
    if (automatic) await speak(`我饿了，已经吃了 ${food.displayName || food.name}。`)
    return `已吃下 ${food.name}，饥饿值 ${bot.food}/20`
  } finally {
    autoEating = false
  }
}

function fuelCapacity(name) {
  const value = String(name || '')
  if (FUEL_CAPACITY[value]) return FUEL_CAPACITY[value]
  if (value.endsWith('_log') || value.endsWith('_wood') || value.endsWith('_planks')) return 1.5
  return 0
}

function inventoryCountMatching(wanted) {
  return bot.inventory.items()
    .filter(item => matchesItem(item.name, wanted))
    .reduce((total, item) => total + item.count, 0)
}

function compatibleFurnaceNames(inputName) {
  if (BLAST_FURNACE_INPUTS.has(inputName)) return new Set(['blast_furnace', 'furnace'])
  if (SMOKER_INPUTS.has(inputName)) return new Set(['smoker', 'furnace'])
  return new Set(['furnace'])
}

async function smeltItem(args, token) {
  let inputName = String(args?.input || '')
  const outputName = String(args?.output || '')
  const requested = Math.max(1, Math.min(8, Number(args?.count) || 1))
  const requestedFuel = String(args?.fuel || '')
  if (!inputName || !outputName) throw new Error('烧炼命令缺少原料或产物')

  // Idempotent retries: Python re-sends a step whose postcondition was
  // verified against a state snapshot that can lag the worker by ~1s.  If
  // the goal is already satisfied (or partially so), smelt only the
  // shortfall - never a second batch on top of a finished one.
  const already = inventoryCountMatching(outputName)
  const needed = Math.max(0, requested - already)
  if (needed === 0) return `已有 ${already}/${requested} 个 ${outputName}，无需再烧炼`

  let input = inventoryItemMatching(inputName)
  if (!input && inputName === '_log') {
    input = bot.inventory.items().find(item => item.name.endsWith('_log') || item.name.endsWith('_wood'))
  }
  if (!input || inventoryCountMatching(inputName) < needed) {
    throw new Error(`烧炼需要 ${needed} 个 ${inputName}，当前背包不足`)
  }
  inputName = input.name

  const compatibleStations = compatibleFurnaceNames(inputName)
  const furnaceBlock = bot.findBlock({
    matching: block => compatibleStations.has(block?.name),
    maxDistance: 32,
  })
  if (!furnaceBlock) {
    throw new Error(`32 格内没有可处理 ${inputName} 的熔炉（可用：${[...compatibleStations].join(' / ')}）`)
  }
  activityProgress('moving_to_furnace', 10, `正在前往 ${furnaceBlock.name}`)
  try {
    await timeout(
      bot.pathfinder.goto(new goals.GoalGetToBlock(
        furnaceBlock.position.x, furnaceBlock.position.y, furnaceBlock.position.z,
      )),
      12_000,
      'furnace approach',
    )
  } catch (error) {
    stopPathfinder()
    clearControls()
    throw error
  }
  assertCurrent(token)

  const before = inventoryCountMatching(outputName)
  let furnace = null
  try {
    furnace = await bot.openFurnace(furnaceBlock)
    assertCurrent(token)
    const occupiedInput = furnace.inputItem()
    const occupiedOutput = furnace.outputItem()
    if (occupiedInput && occupiedInput.name !== inputName) {
      throw new Error(`熔炉输入槽正放着 ${occupiedInput.name}，为避免拿错物品已停止`)
    }
    if (occupiedOutput && occupiedOutput.name !== outputName) {
      throw new Error(`熔炉输出槽正放着 ${occupiedOutput.name}，为避免拿错物品已停止`)
    }
    // Output from an earlier (already-reported) run may still be sitting in
    // the furnace: collect it first and count it toward the goal instead of
    // smelting another batch over it.
    if (occupiedOutput) {
      activityProgress('collecting_output', 22, `取出炉内已有的 ${occupiedOutput.count} 个 ${outputName}`)
      await furnace.takeOutput()
      assertCurrent(token)
      if (inventoryCountMatching(outputName) - before >= needed) {
        return `从炉内取出已有的 ${outputName}，共 ${inventoryCountMatching(outputName)}/${requested} 个`
      }
    }

    const fuelCandidates = bot.inventory.items()
      .filter(item => fuelCapacity(item.name) > 0)
      .filter(item => !requestedFuel || matchesItem(item.name, requestedFuel))
      .sort((a, b) => fuelCapacity(b.name) - fuelCapacity(a.name))
    const fuel = fuelCandidates[0]
    if (!fuel) throw new Error(`背包里没有${requestedFuel ? `指定燃料 ${requestedFuel}` : '可用燃料'}`)
    const fuelNeeded = Math.max(
      1,
      Number(args?.fuelCount) || Math.ceil(needed / fuelCapacity(fuel.name)),
    )
    if (fuel.count < fuelNeeded) {
      throw new Error(`燃料不足：${fuel.name} 需要 ${fuelNeeded} 个，只有 ${fuel.count} 个`)
    }

    activityProgress('loading_furnace', 20, `正在放入 ${needed} 个 ${inputName} 和 ${fuelNeeded} 个 ${fuel.name}`)
    await furnace.putInput(input.type, input.metadata, needed)
    const slotFuel = furnace.fuelItem()
    const existingFuelCapacity = slotFuel ? fuelCapacity(slotFuel.name) * slotFuel.count : 0
    if (existingFuelCapacity < needed) {
      if (slotFuel && slotFuel.name !== fuel.name) {
        throw new Error(`熔炉燃料槽已有 ${slotFuel.name} 且不足，无法安全混放 ${fuel.name}`)
      }
      await furnace.putFuel(fuel.type, fuel.metadata, fuelNeeded)
    }

    const deadline = Date.now() + needed * 12_000 + 15_000
    let lastGain = 0
    let lastProgressAt = Date.now()
    while (Date.now() < deadline) {
      assertCurrent(token)
      const ready = furnace.outputItem()
      if (ready) {
        if (ready.name !== outputName) throw new Error(`熔炉产生了意外物品 ${ready.name}`)
        await furnace.takeOutput()
      }
      const gained = inventoryCountMatching(outputName) - before
      if (gained > lastGain) {
        lastGain = gained
        lastProgressAt = Date.now()
      }
      activityProgress(
        'smelting', Math.min(95, 25 + (gained / needed) * 70),
        `烧炼中：已取得 ${gained}/${needed} 个 ${outputName}`,
        { result_item: outputName, result_count: gained },
      )
      if (gained >= needed) return `已烧炼并取出 ${gained}/${needed} 个 ${outputName}（共 ${already + gained}/${requested}）`
      if (Date.now() - lastProgressAt > 18_000 && Number(furnace.progress || 0) <= 0) {
        throw new Error('熔炉 18 秒没有进展，请检查配方、燃料或服务器状态')
      }
      await wait(500)
    }
    throw new Error(`烧炼超时：只取得 ${lastGain}/${requested} 个 ${outputName}`)
  } finally {
    try { furnace?.close() } catch {}
    stopPathfinder()
    clearControls()
  }
}

function windowItemsRange(window, start, end) {
  return window.slots.slice(start, end).filter(Boolean)
}

function windowPotionCount(window, potion, start = window.inventoryStart, end = window.inventoryEnd) {
  return windowItemsRange(window, start, end)
    .filter(item => potionMatches(item, potion, potionNamesById))
    .reduce((sum, item) => sum + Number(item.count || 0), 0)
}

async function transferOneIntoSlot(window, item, slot) {
  await bot.transfer({
    window,
    itemType: item.type,
    metadata: item.metadata,
    nbt: item.nbt,
    count: 1,
    sourceStart: item.slot,
    sourceEnd: item.slot + 1,
    destStart: slot,
    destEnd: slot + 1,
  })
}

async function unloadBrewingSlot(window, slot) {
  if (!window.slots[slot]) return
  const destination = window.firstEmptySlotRange(window.inventoryStart, window.inventoryEnd)
  if (destination == null) throw new Error('背包没有空位，无法安全整理酿造台')
  await bot.moveSlotItem(slot, destination)
}

async function brewPotion(args, token) {
  const target = normalizePotionName(args?.potion)
  const requested = Math.max(1, Math.min(3, Number(args?.count) || 1))
  const fullPath = brewingPath(target)
  if (!fullPath.length) throw new Error(`${target || '目标'} 不是可执行的酿造目标`)
  if (!potionNamesById.size) {
    throw new Error(`当前 ${config?.version || '未知'} 版本没有可靠的药水注册表，已拒绝盲目酿造`)
  }

  const standBlock = bot.findBlock({
    matching: block => block?.name === 'brewing_stand',
    maxDistance: 32,
  })
  if (!standBlock) throw new Error('32 格内没有酿造台')

  activityProgress('moving_to_brewing_stand', 5, '正在前往酿造台')
  try {
    await timeout(
      // No digging on approach: the pathfinder must never mine the very
      // stand it is about to open (same class as the bed bug).
      gotoWithoutDigging(new goals.GoalGetToBlock(
        standBlock.position.x, standBlock.position.y, standBlock.position.z,
      )),
      12_000,
      'brewing stand approach',
    )
  } catch (error) {
    stopPathfinder()
    clearControls()
    throw error
  }
  assertCurrent(token)

  let window = null
  try {
    window = await timeout(bot.openBlock(standBlock), 8_000, 'open brewing stand')
    assertCurrent(token)
    if (window?.type !== 'minecraft:brewing_stand' || window.inventoryStart !== 5) {
      throw new Error(`打开的容器不是标准酿造台（${window?.type || 'unknown'}）`)
    }

    // Clear all machine slots into the inventory first.  This prevents a
    // forgotten fourth-party potion or ingredient from being transformed or
    // consumed as collateral damage.
    for (const slot of [0, 1, 2, 3]) {
      assertCurrent(token)
      await unloadBrewingSlot(window, slot)
    }

    const beforeTarget = windowPotionCount(window, target)
    const candidateInputs = [...new Set(fullPath.map(step => step.input))].reverse()
    const initialPotion = candidateInputs.find(name =>
      windowPotionCount(window, name) >= requested)
    if (!initialPotion) {
      throw new Error(`缺少 ${requested} 瓶可用的基础药水（需要 ${candidateInputs.join(' 或 ')}）`)
    }
    const startIndex = fullPath.findIndex(step => step.input === initialPotion)
    const steps = fullPath.slice(startIndex)

    const ingredientNeeds = new Map()
    for (const step of steps) {
      ingredientNeeds.set(step.ingredient, (ingredientNeeds.get(step.ingredient) || 0) + 1)
    }
    const fuelSlot = window.slots[4]
    if (fuelSlot && fuelSlot.name !== 'blaze_powder') {
      await unloadBrewingSlot(window, 4)
    }
    if (!window.slots[4]) {
      ingredientNeeds.set('blaze_powder', (ingredientNeeds.get('blaze_powder') || 0) + 1)
    }
    for (const [ingredient, count] of ingredientNeeds) {
      const available = windowItemsRange(window, window.inventoryStart, window.inventoryEnd)
        .filter(item => item.name === ingredient)
        .reduce((sum, item) => sum + item.count, 0)
      if (available < count) throw new Error(`炼药材料不足：需要 ${ingredient} x${count}，当前 ${available}`)
    }

    for (let slot = 0; slot < requested; slot += 1) {
      const source = windowItemsRange(window, window.inventoryStart, window.inventoryEnd)
        .find(item => potionMatches(item, initialPotion, potionNamesById))
      if (!source) throw new Error(`装瓶时找不到 ${initialPotion}，容器状态可能已变化`)
      await transferOneIntoSlot(window, source, slot)
      assertCurrent(token)
      if (!potionMatches(window.slots[slot], initialPotion, potionNamesById)) {
        throw new Error(`基础药水没有进入瓶槽 ${slot}`)
      }
    }

    if (!window.slots[4]) {
      const fuel = windowItemsRange(window, window.inventoryStart, window.inventoryEnd)
        .find(item => item.name === 'blaze_powder')
      if (!fuel) throw new Error('缺少酿造燃料 blaze_powder')
      await transferOneIntoSlot(window, fuel, 4)
    }
    if (window.slots[4]?.name !== 'blaze_powder') throw new Error('烈焰粉未能进入酿造燃料槽')

    for (let index = 0; index < steps.length; index += 1) {
      const step = steps[index]
      assertCurrent(token)
      if (window.slots[3]) await unloadBrewingSlot(window, 3)
      const ingredient = windowItemsRange(window, window.inventoryStart, window.inventoryEnd)
        .find(item => item.name === step.ingredient)
      if (!ingredient) throw new Error(`缺少炼药材料 ${step.ingredient}`)
      await transferOneIntoSlot(window, ingredient, 3)
      if (window.slots[3]?.name !== step.ingredient) throw new Error(`${step.ingredient} 未能进入原料槽`)

      const stageDeadline = Date.now() + 32_000
      let lastReport = 0
      while (Date.now() < stageDeadline) {
        assertCurrent(token)
        const completed = Array.from({ length: requested }, (_, slot) =>
          potionMatches(window.slots[slot], step.output, potionNamesById),
        ).filter(Boolean).length
        if (completed === requested) break
        if (Date.now() - lastReport >= 1000) {
          lastReport = Date.now()
          const overall = 15 + ((index + Math.min(0.9, (32_000 - (stageDeadline - Date.now())) / 22_000)) / steps.length) * 75
          activityProgress(
            'brewing', Math.min(92, overall),
            `酿造 ${target}：第 ${index + 1}/${steps.length} 阶段 ${step.input} → ${step.output}`,
            { potion: target, stage: index + 1, stages: steps.length },
          )
        }
        await wait(250)
      }
      const verified = Array.from({ length: requested }, (_, slot) =>
        potionMatches(window.slots[slot], step.output, potionNamesById),
      ).filter(Boolean).length
      if (verified !== requested) {
        const actual = Array.from({ length: requested }, (_, slot) =>
          potionIdentity(window.slots[slot], potionNamesById)?.potion || 'empty')
        throw new Error(`酿造阶段超时：期望 ${step.output} x${requested}，实际 ${actual.join(', ')}`)
      }
    }

    activityProgress('collecting_potions', 94, `正在取出 ${target} x${requested}`)
    for (let slot = 0; slot < requested; slot += 1) {
      assertCurrent(token)
      await unloadBrewingSlot(window, slot)
    }
    const gained = windowPotionCount(window, target) - beforeTarget
    if (gained < requested) {
      throw new Error(`酿造后置条件未通过：背包只新增 ${gained}/${requested} 瓶 ${target}`)
    }
    return `已酿造并取出 ${target} x${requested}`
  } finally {
    try { window?.close() } catch {}
    stopPathfinder()
    clearControls()
  }
}

async function useCauldron(args, token) {
  const action = String(args?.action || '')
  const fluid = String(args?.fluid || 'water')
  if (!['fill', 'collect'].includes(action) || !['water', 'lava', 'powder_snow'].includes(fluid)) {
    throw new Error('炼药锅命令应为 cauldron fill/collect water|lava|powder_snow')
  }
  const expectedFilled = `${fluid}_cauldron`
  const matchingName = action === 'fill' ? 'cauldron' : expectedFilled
  const cauldron = bot.findBlock({
    matching: block => block?.name === matchingName,
    maxDistance: 24,
  })
  if (!cauldron) {
    throw new Error(`24 格内没有${action === 'fill' ? '空的' : `装有 ${fluid} 的`}炼药锅`)
  }
  const inputName = action === 'fill' ? `${fluid}_bucket` : 'bucket'
  const outputName = action === 'fill' ? 'bucket' : `${fluid}_bucket`
  const input = inventoryItemMatching(inputName)
  if (!input) throw new Error(`背包里没有 ${inputName}`)

  activityProgress('moving_to_cauldron', 15, `正在前往炼药锅，准备${action === 'fill' ? '装入' : '取出'} ${fluid}`)
  await timeout(
    // No digging on approach: never mine the cauldron on the way to it.
    gotoWithoutDigging(new goals.GoalGetToBlock(
      cauldron.position.x, cauldron.position.y, cauldron.position.z,
    )),
    12_000,
    'cauldron approach',
  )
  assertCurrent(token)
  const beforeOutput = inventoryCountMatching(outputName)
  await bot.equip(input, 'hand')
  assertCurrent(token)
  await bot.activateBlock(cauldron)

  const deadline = Date.now() + 5000
  while (Date.now() < deadline) {
    assertCurrent(token)
    const current = bot.blockAt(cauldron.position)
    const blockOk = action === 'fill' ? current?.name === expectedFilled : current?.name === 'cauldron'
    const itemOk = inventoryCountMatching(outputName) > beforeOutput
    if (blockOk && itemOk) {
      return `已${action === 'fill' ? '装入' : '取出'} ${fluid}，炼药锅和背包变化均已确认`
    }
    activityProgress('using_cauldron', 70, `正在核对炼药锅与 ${outputName}`)
    await wait(200)
  }
  throw new Error(`炼药锅后置条件未通过：方块或 ${outputName} 没有按预期变化`)
}

async function craftItem(itemName, requested, token) {
  if (itemName === 'planks') {
    const source = bot.inventory.items().find(entry =>
      entry.name.endsWith('_log') || entry.name.endsWith('_wood') || entry.name.endsWith('_stem') || entry.name.endsWith('_hyphae'))
    if (source) {
      itemName = source.name
        .replace(/_log$/, '_planks')
        .replace(/_wood$/, '_planks')
        .replace(/_stem$/, '_planks')
        .replace(/_hyphae$/, '_planks')
    } else {
      throw new Error('背包里没有原木，无法制作木板（任意木材的原木都可以）')
    }
  }
  const item = bot.registry.itemsByName[itemName]
  if (!item) throw new Error(`未知物品 ${itemName}`)
  let table = null
  let recipes = bot.recipesFor(item.id, null, 1, null)
  if (!recipes.length) {
    const tablePosition = bot.findBlock({
      matching: bot.registry.blocksByName.crafting_table?.id,
      maxDistance: 32,
    })
    if (tablePosition) {
      table = tablePosition
      recipes = bot.recipesFor(item.id, null, 1, table)
    }
  }
  if (!recipes.length) throw new Error(`当前背包材料不足，或没有可用的 ${itemName} 配方`)
  if (table) {
    activityProgress('moving_to_crafting_table', 15, '正在前往工作台')
    await bot.pathfinder.goto(new goals.GoalGetToBlock(table.position.x, table.position.y, table.position.z))
    assertCurrent(token)
  }
  // Any-wood crafting: 1.21.5 has one recipe variant per planks family
  // member (oak, birch, cherry, crimson stems ...) and stone tools accept
  // cobblestone OR cobbled deepslate.  Pick the first variant the current
  // inventory can actually satisfy instead of a fixed variant order.
  const countOfType = (typeId) => bot.inventory.items()
    .filter(entry => entry.type === typeId)
    .reduce((sum, entry) => sum + entry.count, 0)
  const recipe = recipes.find(candidate =>
    (candidate.delta || []).every(entry =>
      entry.count >= 0 || countOfType(entry.id) >= -entry.count)) || recipes[0]
  const outputCount = Number(recipe.result?.count || 1)
  const crafts = Math.max(1, Math.ceil(Number(requested || 1) / outputCount))
  activityProgress('crafting', 60, `正在制作 ${itemName}，执行 ${crafts} 次配方`)
  await bot.craft(recipe, crafts, table || undefined)
  assertCurrent(token)
  state.last_craft = {
    at: Date.now() / 1000,
    name: itemName,
    count: crafts * outputCount,
  }
  return `已制作 ${itemName}（${crafts} 次配方）`
}

async function dispatchCommand(text, origin = 'saya', quiet = false, taskId = null) {
  const parsed = parseCommand(text)
  if (!parsed || !bot) return
  log(`Command (${origin}${quiet ? ', quiet' : ''}): ${parsed.raw}`)

  if (parsed.verb === 'raw') {
    // Slash/admin commands are trusted doctor input only. LLM output never
    // reaches this branch because Python validates commands first.
    if (origin !== 'doctor') {
      log(`Refused untrusted raw command from ${origin}: ${parsed.raw}`, 'warn')
      return
    }
    bot.chat(parsed.args)
    return
  }
  if (parsed.verb === 'say') {
    await speak(parsed.args)
    return
  }
  if (parsed.verb === 'follow') {
    beginFollow(parsed.args)
    await speak(`持续跟随已开启：${follow.target}。我会使用动态寻路，不会自动传送。`)
    await reconcileFollow()
    emitState()
    return
  }
  if (parsed.verb === 'unfollow') {
    disableFollow()
    clearControls()
    await speak('持续跟随已关闭。')
    emitState()
    return
  }
  if (parsed.verb === 'auto_discard') {
    if (parsed.args === 'on' || parsed.args === 'off') {
      setAutoDiscardEnabled(parsed.args === 'on')
      await speak(`自动丢弃非任务拾取物已${parsed.args === 'on' ? '开启' : '关闭'}。`)
    } else {
      await speak(`自动丢弃当前${automation.autoDiscardEnabled ? '开启' : '关闭'}，待处理 ${automation.pending.length} 类物品。`)
    }
    return
  }
  if (parsed.verb === 'cancel_task') {
    cancelAutoCombat('doctor cancelled current task')
    const cancelled = await cancelForeground('doctor cancelled current task')
    clearControls()
    if (follow.enabled) {
      follow.active = false
      state.follow_mode = 'resuming'
      await reconcileFollow()
    }
    await speak(cancelled ? '已取消当前任务；持续跟随状态保持不变。' : '当前没有可取消的任务。')
    emitState()
    return
  }
  if (parsed.verb === 'stop') {
    // A doctor stop also aborts an emergency rescue (controlled experiments
    // need an escape hatch).
    if (survival.active) {
      survival.active = false
      survival.busy = false
      survival.reason = ''
      syncSurvivalState()
      log('Emergency aborted by stop command')
    }
    cancelAutoCombat('stop command')
    await cancelForeground('stop command')
    disableFollow()
    clearControls()
    await speak('已停止当前任务、关闭持续跟随并退出紧急状态。')
    emitState()
    return
  }
  if (parsed.verb === 'status') {
    const task = state.activity?.state === 'running' ? `当前任务 ${state.activity.command}：${state.activity.message}` : '当前没有任务'
    const followText = follow.enabled ? `；持续跟随 ${follow.target}（${state.follow_mode}）` : '；持续跟随关闭'
    const emergencyText = survival.active ? `；紧急状态：${survival.reason}` : ''
    const pendingTasks = automation.pending.length + (automation.current ? 1 : 0)
    const taskQueueText = pendingTasks > 0 ? `；小任务队列 ${pendingTasks} 项待完成` : ''
    const automationText = `；自动丢弃${automation.autoDiscardEnabled ? '开启' : '关闭'}`
      + `，主动攻击${automation.autoAttackHostiles ? '开' : '关'}`
      + `，狩猎${automation.autoHuntAnimals ? '开' : '关'}`
      + `，反击${automation.autoCounterattack ? '开' : '关'}`
    await speak(`${task}${followText}${emergencyText}${automationText}${taskQueueText}；生命 ${bot.health}/20，饥饿 ${bot.food}/20。`)
    return
  }
  void runForeground(parsed, quiet, taskId, origin)
}

/**
 * Keep asking the server for respawn on the SAME connection until the health
 * plugin reports alive again.  Retrying matters: a single dropped
 * client_command would otherwise leave Saya parked on the death screen
 * forever (invisible to Doctor, unable to chat, follow, rescue or craft).
 */
function scheduleRespawnRetries(attempt) {
  if (stopping || !bot || bot.isAlive || attempt >= 6) {
    if (attempt >= 6 && bot && !bot.isAlive) {
      log('Respawn request did not take effect after 6 attempts', 'error')
    }
    return
  }
  setTimeout(() => {
    if (stopping || !bot || bot.isAlive) return
    try { bot.respawn() } catch {}
    scheduleRespawnRetries(attempt + 1)
  }, 800)
}

// --- movement safety: mineflayer 4.33.0 x minecraft-data 3.115.0 ----------
//
// The 1.21.5 protocol schema parses the knockback packet (entity_velocity)
// as { entityId, velocity: {x, y, z} }, but mineflayer's handler still reads
// packet.velocityX/Y/Z.  Every knockback therefore wrote NaN into the physics
// velocity, and the next move packet carried non-finite coordinates - which
// vanilla 1.21.5 rejects with multiplayer.disconnect.invalid_player_movement
// (its handleMovePlayer checks isFinite on x/y/z/yaw/pitch and kicks).  So
// Saya used to leave the server the instant she was hit by anything with
// knockback: players, mobs, explosions.  Drowning has no knockback, which is
// why it never kicked.

/** Rebind the broken entity_velocity handler with a schema-aware one. */
function patchEntityVelocityHandler(instance) {
  const client = instance._client
  client.removeAllListeners('entity_velocity')
  client.on('entity_velocity', packet => {
    const entity = instance.entities[packet.entityId]
    if (!entity || !entity.velocity) return
    const raw = packet.velocity && typeof packet.velocity === 'object'
      ? packet.velocity
      : { x: packet.velocityX, y: packet.velocityY, z: packet.velocityZ }
    const vx = Number(raw.x) / 8000
    const vy = Number(raw.y) / 8000
    const vz = Number(raw.z) / 8000
    if (![vx, vy, vz].every(Number.isFinite)) {
      log(`Discarded non-finite velocity packet (entity ${packet.entityId})`, 'warn')
      return
    }
    entity.velocity.update(vx, vy, vz)
  })
  log('entity_velocity handler patched for the 1.21.5 schema')
}

/**
 * Second line of defence: vanilla kicks when a move packet claims
 * non-finite coordinates or more than ~10 blocks of movement in one tick.
 * The sanitizer never lets such a packet leave: non-finite packets are
 * rewritten to the last sent (finite) spot, oversized jumps are claimed as
 * "no movement this tick" (always legal; the server answers with a
 * rubber-band correction, never a kick).  Server teleports are exempt.
 */
const MOVE_JUMP_LIMIT_BLOCKS = 8
const sanitizer = { lastSent: null, serverPos: null, nonFinite: 0, clamped: 0 }

function finiteOr(v, fallback) {
  return Number.isFinite(v) ? v : fallback
}

function sanitizeMovePacket(name, data, instance) {
  const tele = sanitizer.serverPos
  const isTeleportConfirm = tele && Date.now() - tele.at < 100 &&
    Math.abs(data.x - tele.x) < 0.001 &&
    Math.abs(data.y - tele.y) < 0.001 &&
    Math.abs(data.z - tele.z) < 0.001

  const positionFinite = [data.x, data.y, data.z].every(Number.isFinite)
  const lookFinite = name !== 'position_look' ||
    (Number.isFinite(data.yaw) && Number.isFinite(data.pitch))

  if (!positionFinite || !lookFinite) {
    sanitizer.nonFinite += 1
    if (sanitizer.nonFinite % 20 === 1) {
      log('Movement guard: non-finite move packet rewritten', 'warn')
    }
    if (!sanitizer.lastSent) return null // nothing finite ever sent: drop
    // Poisoned physics state: snap back to the last finite values so the
    // simulation recovers instead of emitting NaN forever.
    const entity = instance.entity
    if (entity) {
      if (!entity.position || !Number.isFinite(entity.position.x)) {
        entity.position.set(sanitizer.lastSent.x, sanitizer.lastSent.y, sanitizer.lastSent.z)
      }
      if (!entity.velocity || !Number.isFinite(entity.velocity.x)) {
        entity.velocity.set(0, 0, 0)
      }
    }
    return {
      ...data,
      x: sanitizer.lastSent.x,
      y: sanitizer.lastSent.y,
      z: sanitizer.lastSent.z,
      yaw: finiteOr(data.yaw, 0),
      pitch: finiteOr(data.pitch, 0),
    }
  }

  if (!sanitizer.lastSent) {
    sanitizer.lastSent = { x: data.x, y: data.y, z: data.z }
    return data
  }

  const dx = data.x - sanitizer.lastSent.x
  const dy = data.y - sanitizer.lastSent.y
  const dz = data.z - sanitizer.lastSent.z
  const dist = Math.sqrt(dx * dx + dy * dy + dz * dz)
  if (!isTeleportConfirm && dist > MOVE_JUMP_LIMIT_BLOCKS) {
    sanitizer.clamped += 1
    if (sanitizer.clamped % 20 === 1) {
      log(`Movement guard: clamped ${Math.round(dist * 100) / 100} block jump to zero`, 'warn')
    }
    return {
      ...data,
      x: sanitizer.lastSent.x,
      y: sanitizer.lastSent.y,
      z: sanitizer.lastSent.z,
      yaw: finiteOr(data.yaw, 0),
      pitch: finiteOr(data.pitch, 0),
    }
  }
  sanitizer.lastSent = { x: data.x, y: data.y, z: data.z }
  return data
}

function installMovementGuards(instance) {
  const client = instance._client
  // Must run BEFORE mineflayer's own 'position' handler so the teleport
  // target is known when the confirming move packet is written.
  client.prependListener('position', packet => {
    if (packet && Number.isFinite(packet.x)) {
      sanitizer.serverPos = { x: packet.x, y: packet.y, z: packet.z, at: Date.now() }
    }
  })
  const originalWrite = client.write.bind(client)
  client.write = (name, data) => {
    let out = data
    try {
      if ((name === 'position' || name === 'position_look') && data) {
        out = sanitizeMovePacket(name, data, instance)
      }
    } catch {}
    if (out === null) return // drop a packet we can not make finite
    return originalWrite(name, out)
  }
}

function bindBotEvents(instance) {
  instance.once('spawn', () => {
    state.connected = true
    state.username = instance.username
    state.last_error = ''
    lifecycle.stage = 'playing'
    // respawn after death also fires spawn: keep the same client, never
    // recreate the Mineflayer connection for a respawn.
    if (lifecycle.death && !lifecycle.respawn) {
      lifecycle.respawn = {
        at: Date.now() / 1000,
        position: instance.entity
          ? [round(instance.entity.position.x), round(instance.entity.position.y), round(instance.entity.position.z)]
          : null,
      }
      recordLifecycleEvent('respawn', { respawn: lifecycle.respawn })
      log(`Respawned on the original connection as ${instance.username}`)
    }
    createMovements()
    survival.lastHealth = instance.health ?? null
    log(`Connected with Mineflayer ${instance.version} as ${instance.username}`)
    output({ type: 'ready', username: instance.username, version: instance.version })
    emitState()
  })

  // Modern player-chat forwarding: the deprecated 'chat' event is regex-
  // matched against EVERY message (system included), so command feedback
  // like "[Kurosawa_Jin] 已传送..." used to masquerade as owner chat.  The
  // 'message' event's position separates real player_chat packets from
  // systemChat output; only the former is forwarded, and the sender UUID
  // must resolve to an online player.
  instance.on('message', (msg, position, sender) => {
    if (!isPlayerChatPosition(position)) return
    const player = resolveChatSender(instance.players, sender)
    if (!player || player.username === instance.username) return
    const message = stripChatDecoration(String(msg), player.username)
    if (!message) return
    state.last_chat = `<${player.username}> ${message}`.slice(0, 240)
    output({ type: 'chat', username: player.username, message: message.slice(0, 240), ts: Date.now() / 1000 })
  })

  instance.on('playerCollect', (collector, collected) => {
    if (!automation.autoDiscardEnabled || pickupBelongsToCurrentTask()) return
    if (!collector || collector.id !== instance.entity?.id) return
    const item = droppedItemDetails(collected)
    if (!item) {
      log('Unexpected pickup detected but item metadata was unavailable; leaving it untouched', 'warn')
      return
    }
    queueUnexpectedPickup(item)
  })

  instance.on('health', () => {
    const health = instance.health
    const previous = survival.lastHealth
    survival.lastHealth = health
    syncSurvivalState()
    emitState()
    void autoEatIfHungry()
    void handleLowHealth()
    if (previous != null && health < previous) void onDamageTaken(previous - health)
  })

  instance.on('entityHurt', entity => {
    if (entity !== instance.entity) return
    if (!state.last_damage?.at || Date.now() / 1000 - state.last_damage.at > 2) {
      state.last_damage = { at: Date.now() / 1000, source: 'unknown' }
    }
  })

  instance.on('entityDead', entity => {
    if (!entity?.id) return
    confirmedEntityDeaths.set(entity.id, {
      at: Date.now(),
      name: combatTargetLabel(entity),
    })
  })

  instance.on('entitySwingArm', entity => {
    if (!entity || entity === instance.entity || distanceTo(entity) > 5.5) return
    recentAggressor = { entity, at: Date.now() }
  })

  instance.on('death', () => {
    const position = instance.entity?.position
    const cause = state.last_damage?.cause
      || (instance.entity?.isInLava ? 'lava' : isSuffocating() ? 'suffocation' : 'unknown')
    lifecycle.death = {
      at: Date.now() / 1000,
      position: position
        ? [round(position.x), round(position.y), round(position.z)]
        : null,
      cause,
      last_task: foreground.command || '',
    }
    lifecycle.respawn = null // the next spawn on this connection is the respawn
    lifecycle.stage = 'dead'
    survival.active = false
    survival.busy = false
    syncSurvivalState()
    log(`Saya died (cause: ${cause})`, 'error')
    recordLifecycleEvent('death', { death: lifecycle.death })
    // Cancel everything but do NOT destroy/recreate the client.
    void cancelForeground('death').then(() => {
      if (follow.enabled) follow.active = false
    })
    void speak(`我倒下了（${cause}），马上重生回来${follow.enabled ? '，回去后继续跟着你' : ''}。`)
    // options.respawn already asks once via mineflayer's health plugin, but a
    // dropped/mis-timed respawn request must never leave Saya parked on the
    // death screen: retry on the same connection until update_health says
    // alive again (bot.isAlive flips back in the health plugin).
    scheduleRespawnRetries(0)
    emitState()
  })

  instance.on('path_update', result => {
    const status = result?.status || 'updated'
    state.movement = {
      state: `path_${status}`,
      reason: foreground.command ? `task:${foreground.verb}` : `follow:${follow.target}`,
      method: 'mineflayer_pathfinder',
      visited_nodes: result?.visitedNodes ?? null,
      generated_nodes: result?.generatedNodes ?? null,
      path_length: result?.path?.length ?? null,
      time_ms: result?.time ?? null,
    }
    if (foreground.command) {
      const progress = status === 'success' ? 25 : status === 'partial' ? 15 : 8
      activityProgress('pathfinding', progress, `寻路状态：${status}`)
    }
    if (status === 'noPath') log(`Pathfinder reported noPath for ${state.movement.reason}`, 'warn')
  })

  instance.on('path_reset', reason => {
    state.movement = {
      state: 'path_reset', reason: String(reason || 'unknown'), method: 'mineflayer_pathfinder',
    }
    log(`Path reset: ${reason}`, reason === 'stuck' ? 'warn' : 'info')
  })

  instance.on('goal_reached', goal => {
    state.movement = { state: 'goal_reached', goal: goal?.constructor?.name || 'goal', method: 'mineflayer_pathfinder' }
    if (foreground.command) activityProgress('arrived', 45, '已到达任务交互范围')
  })

  instance.on('kicked', reason => {
    const text = typeof reason === 'string' ? reason : JSON.stringify(reason)
    state.last_error = `kicked: ${text}`.slice(0, 300)
    lifecycle.kick = { at: Date.now() / 1000, reason: text.slice(0, 200) }
    lifecycle.stage = 'kicked'
    recordLifecycleEvent('kicked', { kick: lifecycle.kick })
    log(state.last_error, 'error')
  })

  instance.on('error', error => {
    state.last_error = String(error?.message || error).slice(0, 300)
    log(`Mineflayer error: ${state.last_error}`, 'error')
  })

  instance.on('end', reason => {
    state.connected = false
    follow.active = false
    survival.active = false
    survival.busy = false
    syncSurvivalState()
    lifecycle.disconnect = {
      at: Date.now() / 1000,
      reason: String(reason || 'connection ended').slice(0, 200),
      stage: lifecycle.stage,
    }
    lifecycle.stage = 'disconnected'
    log(`Disconnected: ${lifecycle.disconnect.reason} (stage was ${lifecycle.disconnect.stage})`, 'warn')
    // The Python supervisor owns reconnection (single instance + backoff).
    // This worker stays alive and waits for a {"type":"reconnect"} message.
    recordLifecycleEvent('end', { disconnect: lifecycle.disconnect })
    emitState()
  })
}

function connectBot() {
  if (!config || stopping) return
  if (bot && state.connected) return
  lifecycle.stage = 'connecting'
  // A brand-new connection is not a death respawn; reset both markers so the
  // spawn handler does not misreport it.
  lifecycle.death = null
  lifecycle.respawn = null
  const options = {
    host: config.host || '127.0.0.1',
    port: Number(config.port || 25565),
    username: config.account || config.username || 'Saya',
    version: config.version || false,
    auth: config.account ? 'microsoft' : 'offline',
    // Vanilla servers keep the client on the death screen until the CLIENT
    // requests respawn.  Without this the bot is stuck out of the world
    // after every death and the whole survival machine is dead weight.
    respawn: true,
    hideErrors: false,
    checkTimeoutInterval: 30_000,
  }
  log(`Connecting Mineflayer to ${options.host}:${options.port} as ${options.username}`)
  recordLifecycleEvent('connecting', { host: options.host, port: options.port })
  bot = mineflayer.createBot(options)
  potionNamesById.clear()
  potionRegistrySource = installVanillaPotionRegistry(options.version, potionNamesById)
    ? `vanilla_${options.version}`
    : 'none'
  bot._client.on('registry_data', packet => {
    const before = potionNamesById.size
    loadPotionRegistryPacket(packet, potionNamesById)
    if (potionNamesById.size && (potionNamesById.size !== before || String(packet?.id || '').endsWith('potion'))) {
      potionRegistrySource = 'server'
    }
  })
  bot.loadPlugin(pathfinder)
  bot.loadPlugin(toolPlugin)
  bot.loadPlugin(collectBlockPlugin)
  bindBotEvents(bot)
  patchEntityVelocityHandler(bot)
  installMovementGuards(bot)
}

async function shutdown() {
  if (stopping) return
  stopping = true
  state.running = false
  clearInterval(statusTimer)
  cancelAutoCombat('worker shutdown')
  await cancelForeground('worker shutdown')
  disableFollow()
  try { bot?.quit('Saya worker stopped') } catch {}
  emitState()
  setTimeout(() => process.exit(0), 300).unref()
}

function handleMessage(message) {
  if (!message || typeof message !== 'object') return
  if (message.type === 'plan_state') {
    planActive = Boolean(message.active)
    state.plan_active = planActive
    log(`Plan ${planActive ? 'started' : 'finished'} (follow/hunt deferred while active)`)
    emitState()
    return
  }
  if (message.type === 'init') {
    if (config) return
    config = message.config || {}
    automation.autoDiscardEnabled = Boolean(config.auto_discard_unplanned)
    loadCombatAutomationConfig(config)
    syncAutomationState()
    state.username = config.username || 'Saya'
    state.config = { ...config, account: config.account ? '<configured>' : '' }
    connectBot()
    statusTimer = setInterval(() => {
      void reconcileFollow().catch(error => log(`Follow watchdog error: ${error.message}`, 'error'))
      void processAutoDiscardQueue().catch(error => log(`Auto-discard watchdog error: ${error.message}`, 'error'))
      processCombatAutomation()
      try { processOreMiningAutomation() } catch (error) { log(`Auto-mine error: ${error.message}`, 'warn') }
      if (state.activity?.state === 'running') {
        const staleMs = Date.now() - Number(state.activity.updated_at || state.activity.started_at) * 1000
        if (staleMs > 20_000 && !state.activity.stall_reported) {
          state.activity.stall_reported = true
          void speak('任务超过 20 秒没有进展，我仍在尝试；可以发送 stop 或新任务打断。')
        }
      }
      emitState()
    }, STATUS_INTERVAL_MS)
    return
  }
  if (message.type === 'command') void dispatchCommand(message.text, message.origin || 'saya', Boolean(message.quiet), message.task_id || null)
  else if (message.type === 'config_update') {
    config = { ...(config || {}), ...(message.config || {}) }
    state.config = { ...config, account: config.account ? '<configured>' : '' }
    const enabled = Boolean(config.auto_discard_unplanned)
    if (enabled !== automation.autoDiscardEnabled) setAutoDiscardEnabled(enabled)
    else syncAutomationState()
    loadCombatAutomationConfig(config)
    emitState()
  }
  else if (message.type === 'reconnect') {
    if (stopping) return
    if (bot && state.connected) return
    log('Reconnect requested by supervisor')
    connectBot()
  }
  else if (message.type === 'speak') void speak(message.text)
  else if (message.type === 'shutdown') void shutdown()
  else if (message.type === 'snapshot') emitState()
}

const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity })
input.on('line', line => {
  try { handleMessage(JSON.parse(line)) }
  catch (error) { log(`Invalid control message: ${error.message}`, 'error') }
})
input.on('close', () => void shutdown())

process.on('SIGINT', () => void shutdown())
process.on('SIGTERM', () => void shutdown())
process.on('uncaughtException', error => {
  log(`Uncaught exception: ${error.stack || error.message}`, 'error')
})
process.on('unhandledRejection', error => {
  log(`Unhandled rejection: ${error?.stack || error}`, 'error')
})

output({ type: 'worker_started', pid: process.pid, engine: 'mineflayer' })

function wait(milliseconds) {
  return new Promise(resolve => setTimeout(resolve, milliseconds))
}
