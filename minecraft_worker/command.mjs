const NUMBER_WORDS = new Map([
  ['一', 1], ['二', 2], ['两', 2], ['三', 3], ['四', 4], ['五', 5],
  ['六', 6], ['七', 7], ['八', 8], ['九', 9], ['十', 10],
])

const ITEM_ALIASES = {
  木剑: 'wooden_sword', 石剑: 'stone_sword', 铁剑: 'iron_sword', 金剑: 'golden_sword', 钻石剑: 'diamond_sword',
  木镐: 'wooden_pickaxe', 石镐: 'stone_pickaxe', 铁镐: 'iron_pickaxe', 金镐: 'golden_pickaxe', 钻石镐: 'diamond_pickaxe',
  木斧: 'wooden_axe', 石斧: 'stone_axe', 铁斧: 'iron_axe', 钻石斧: 'diamond_axe',
  木铲: 'wooden_shovel', 石铲: 'stone_shovel', 铁铲: 'iron_shovel', 钻石铲: 'diamond_shovel',
  木锄: 'wooden_hoe', 石锄: 'stone_hoe', 铁锄: 'iron_hoe',
  // Armor (mirror of the Python catalog's systematic MATRICES x TOOL_CN names;
  // contract-tested in tests/test_item_aliases.py)
  皮革头盔: 'leather_helmet', 锁链头盔: 'chainmail_helmet', 铁头盔: 'iron_helmet',
  金头盔: 'golden_helmet', 钻石头盔: 'diamond_helmet', 下界合金头盔: 'netherite_helmet',
  皮革胸甲: 'leather_chestplate', 锁链胸甲: 'chainmail_chestplate', 铁胸甲: 'iron_chestplate',
  金胸甲: 'golden_chestplate', 钻石胸甲: 'diamond_chestplate', 下界合金胸甲: 'netherite_chestplate',
  皮革护腿: 'leather_leggings', 锁链护腿: 'chainmail_leggings', 铁护腿: 'iron_leggings',
  金护腿: 'golden_leggings', 钻石护腿: 'diamond_leggings', 下界合金护腿: 'netherite_leggings',
  皮革靴子: 'leather_boots', 锁链靴子: 'chainmail_boots', 铁靴子: 'iron_boots',
  金靴子: 'golden_boots', 钻石靴子: 'diamond_boots', 下界合金靴子: 'netherite_boots',
  盾牌: 'shield', 铁砧: 'anvil', 金苹果: 'golden_apple',
  工作台: 'crafting_table', 合成台: 'crafting_table', 熔炉: 'furnace',
  木棍: 'stick', 火把: 'torch', 面包: 'bread', 苹果: 'apple',
  腐肉: 'rotten_flesh', 蛋: 'egg', 骨头: 'bone', 箭: 'arrow', 煤炭: 'coal', 木炭: 'charcoal',
  铁锭: 'iron_ingot', 金锭: 'gold_ingot', 铜锭: 'copper_ingot', 钻石: 'diamond',
  圆石: 'cobblestone', 石头: 'cobblestone',
}

const POTION_ALIASES = {
  水瓶: 'water', 粗制药水: 'awkward', 治疗药水: 'healing', 瞬间治疗药水: 'healing',
  强效治疗药水: 'strong_healing', 力量药水: 'strength', 强效力量药水: 'strong_strength',
  迅捷药水: 'swiftness', 抗火药水: 'fire_resistance', 水肺药水: 'water_breathing',
  夜视药水: 'night_vision', 隐身药水: 'invisibility', 剧毒药水: 'poison',
  再生药水: 'regeneration', 虚弱药水: 'weakness', 缓降药水: 'slow_falling',
}

function normalizePotionName(value) {
  const name = String(value ?? '').trim().toLowerCase().replace(/^minecraft:/, '').replaceAll(' ', '_')
  return POTION_ALIASES[name] || name.replace(/_potion$/, '').replace(/^potion_of_/, '')
}

export const SMELT_OUTPUTS = {
  raw_iron: 'iron_ingot', raw_gold: 'gold_ingot', raw_copper: 'copper_ingot',
  sand: 'glass', cobblestone: 'stone', stone: 'smooth_stone',
  clay_ball: 'brick', clay: 'terracotta', netherrack: 'nether_brick',
  beef: 'cooked_beef', porkchop: 'cooked_porkchop', chicken: 'cooked_chicken',
  mutton: 'cooked_mutton', rabbit: 'cooked_rabbit', cod: 'cooked_cod',
  salmon: 'cooked_salmon', potato: 'baked_potato', kelp: 'dried_kelp',
  cactus: 'green_dye', wet_sponge: 'sponge',
}

export function boundedCount(value, fallback = 1, maximum = 32) {
  const raw = String(value ?? '')
  const digit = raw.match(/\d{1,3}/)
  if (digit) return Math.max(1, Math.min(maximum, Number(digit[0])))
  for (const [word, number] of NUMBER_WORDS) {
    if (raw.includes(word)) return Math.min(maximum, number)
  }
  return fallback
}

export function normalizeBlockName(value) {
  const name = String(value ?? '').trim().toLowerCase().replace(/^minecraft:/, '').replaceAll(' ', '_')
  const aliases = {
    wood: '_log', log: '_log', logs: '_log', tree: '_log', trees: '_log',
    木头: '_log', 原木: '_log', 树: '_log',
    dirt: 'dirt', 泥土: 'dirt', stone: 'stone', 石头: 'stone',
    cobblestone: 'cobblestone', 圆石: 'cobblestone',
    sand: 'sand', 沙子: 'sand', gravel: 'gravel', 砂砾: 'gravel',
    coal: 'coal_ore', 煤: 'coal_ore', iron: 'iron_ore', 铁: 'iron_ore',
    copper: 'copper_ore', 铜: 'copper_ore', gold: 'gold_ore', 金: 'gold_ore',
    diamond: 'diamond_ore', 钻石: 'diamond_ore',
    redstone: 'redstone_ore', 红石: 'redstone_ore',
    lapis: 'lapis_ore', 青金石: 'lapis_ore',
    emerald: 'emerald_ore', 绿宝石: 'emerald_ore',
    planks: 'planks', plank: 'planks', 木板: 'planks',
    sticks: 'stick', stick: 'stick', 木棍: 'stick',
    工作台: 'crafting_table', 合成台: 'crafting_table',
  }
  return aliases[name] ?? name
}

export function normalizeItemName(value) {
  const name = String(value ?? '').trim().toLowerCase().replace(/^minecraft:/, '').replaceAll(' ', '_')
  if (ITEM_ALIASES[name]) return ITEM_ALIASES[name]
  if (['木头', '原木', '树', 'log', 'logs', 'wood'].includes(name)) return '_log'
  if (['木板', 'plank', 'planks'].includes(name)) return 'planks'
  return name
}

export function parseCommand(input) {
  const text = String(input ?? '').trim()
  if (!text) return null
  if (text.startsWith('/')) return { verb: 'raw', args: text, raw: text }

  // Keep the Worker boundary independently safe for direct-console clients.
  // Python normally canonicalizes natural-language input, but older desktop
  // clients and diagnostic callers can still write straight to this parser.
  // Previously such a line became `say 酿造治疗药水`, which looked like a
  // successful command while no brewing activity was ever created.
  const chineseBrew = text.match(
    /^(?:(?:请你?|麻烦你?|帮我|帮忙|劳驾|拜托)\s*)*(?:酿造|酿|做)\s*(\d+|[一二两三])?\s*(?:瓶|个)?\s*(.+?药水)\s*[吧啊呀呢啦。！!～~]*$/,
  )
  if (chineseBrew) {
    const target = normalizePotionName(chineseBrew[2])
    return target
      ? { verb: 'brew', args: { potion: target, count: boundedCount(chineseBrew[1], 1, 3) }, raw: text }
      : null
  }

  const [head, ...restParts] = text.split(/\s+/)
  const verb = head.toLowerCase()
  const rest = restParts.join(' ').trim()
  if (['say', 'chat', 'tell'].includes(verb)) return rest ? { verb: 'say', args: rest, raw: text } : null
  if (verb === 'follow') return { verb: 'follow', args: rest || 'Doctor', raw: text }
  if (['unfollow', 'nofollow'].includes(verb)) return { verb: 'unfollow', args: '', raw: text }
  if (verb === 'come') return { verb: 'come', args: rest || 'Doctor', raw: text }
  if (verb === 'stop') return { verb: 'stop', args: '', raw: text }
  if (verb === 'cancel' || verb === 'cancel_task') return { verb: 'cancel_task', args: '', raw: text }
  if (['status', 'state'].includes(verb)) return { verb: 'status', args: '', raw: text }
  if (verb === 'auto_discard' || verb === 'autodiscard' || (verb === 'auto' && restParts[0]?.toLowerCase() === 'discard')) {
    const mode = verb === 'auto' ? restParts.slice(1).join(' ').trim().toLowerCase() : rest.toLowerCase()
    if (['on', 'enable', 'enabled', '开启', '打开'].includes(mode)) return { verb: 'auto_discard', args: 'on', raw: text }
    if (['off', 'disable', 'disabled', '关闭'].includes(mode)) return { verb: 'auto_discard', args: 'off', raw: text }
    return { verb: 'auto_discard', args: 'status', raw: text }
  }
  if (['inventory', 'inv', 'items'].includes(verb)) return { verb: 'inventory', args: '', raw: text }
  if (['goto', 'walk'].includes(verb)) {
    const values = restParts.map(Number)
    return values.length === 3 && values.every(Number.isFinite)
      ? { verb: 'goto', args: values, raw: text }
      : null
  }
  if (['chop', 'woodcut', 'tree'].includes(verb)) return { verb: 'chop', args: boundedCount(rest, 4, 16), raw: text }
  if (['collect', 'gather', 'harvest'].includes(verb)) {
    const count = boundedCount(rest, 1, 32)
    const wanted = rest.replace(/\d+/g, '').trim() || 'nearby'
    return { verb: 'collect', args: { block: normalizeBlockName(wanted), count }, raw: text }
  }
  if (verb === 'mine') {
    const values = restParts.map(Number)
    if (values.length === 3 && values.every(Number.isFinite)) return { verb: 'mine_at', args: values, raw: text }
    if (['below', 'under', 'underfoot'].includes(rest.toLowerCase())) return { verb: 'mine_below', args: '', raw: text }
    if (['nearby', 'this', 'block', 'this_block'].includes(rest.toLowerCase())) return { verb: 'mine_nearby', args: '', raw: text }
    return { verb: 'collect', args: { block: normalizeBlockName(rest || 'stone'), count: boundedCount(rest, 1, 16) }, raw: text }
  }
  if (['pickup', 'get'].includes(verb)) return { verb: 'pickup', args: '', raw: text }
  if (['drop', 'throw'].includes(verb)) {
    const lowered = rest.toLowerCase()
    if (!rest || lowered === 'one' || lowered === 'item' || lowered === 'it') {
      return { verb: 'drop', args: 'one', raw: text }
    }
    if (lowered === 'stack' || lowered === 'all') return { verb: 'drop', args: 'stack', raw: text }
    // drop <item> [count|all]
    const match = lowered.match(/^(.+?)(?:\s+(\d{1,3}|all))?$/)
    const item = normalizeItemName(match[1])
    const amount = match[2] === undefined ? 1 : (match[2] === 'all' ? 'all' : boundedCount(match[2], 1, 640))
    return { verb: 'drop', args: { item, count: amount }, raw: text }
  }
  if (['eat', 'food'].includes(verb)) return { verb: 'eat', args: rest, raw: text }
  if (['craft', 'make'].includes(verb)) return rest ? { verb: 'craft', args: { item: normalizeItemName(rest.replace(/\d+/g, '').trim()), count: boundedCount(rest, 1, 64) }, raw: text } : null
  if (['brew', 'potion'].includes(verb)) {
    if (!rest) return null
    const count = boundedCount(rest, 1, 3)
    const target = normalizePotionName(rest.replace(/\d+/g, '').trim())
    return target ? { verb: 'brew', args: { potion: target, count }, raw: text } : null
  }
  if (verb === 'milk') {
    return { verb: 'milk', args: boundedCount(rest, 1, 3), raw: text }
  }
  if (verb === 'pillar') {
    return { verb: 'pillar', args: boundedCount(rest, 1, 8), raw: text }
  }
  if (verb === 'cauldron') {
    const [operation = '', fluid = 'water'] = restParts.map(part => part.toLowerCase())
    const action = ['fill', 'put'].includes(operation) ? 'fill'
      : ['collect', 'drain', 'take', 'empty'].includes(operation) ? 'collect' : ''
    return action && ['water', 'lava', 'powder_snow'].includes(fluid)
      ? { verb: 'cauldron', args: { action, fluid }, raw: text }
      : null
  }
  if (['smelt', 'cook'].includes(verb)) {
    const tokens = restParts.slice()
    if (!tokens.length) return null
    const input = normalizeItemName(tokens.shift())
    let output = SMELT_OUTPUTS[input]
      || (input === '_log' || input.endsWith('_log') || input.endsWith('_wood') ? 'charcoal' : '')
    if (tokens.length && !/^\d+$/.test(tokens[0])) output = normalizeItemName(tokens.shift())
    const count = tokens.length && /^\d+$/.test(tokens[0]) ? boundedCount(tokens.shift(), 1, 8) : 1
    const fuelToken = tokens.shift()
    const fuel = fuelToken ? normalizeItemName(fuelToken) : ''
    const fuelCount = tokens.length && /^\d+$/.test(tokens[0]) ? boundedCount(tokens.shift(), 1, 64) : 0
    return output ? { verb: 'smelt', args: { input, output, count, fuel, fuelCount }, raw: text } : null
  }
  if (verb === 'equip') {
    if (!rest) return null
    return { verb: 'equip', args: normalizeItemName(rest.replace(/\d+/g, '').trim()), raw: text }
  }
  if (verb === 'give') {
    // give <item> [count] [to <player>]
    const match = rest.match(/^(.+?)(?:\s+(\d{1,3}))?(?:\s+to\s+(\S+))?\s*$/i)
    if (!match || !match[1]) return null
    return {
      verb: 'give',
      args: {
        item: normalizeItemName(match[1].trim()),
        count: boundedCount(match[2], 1, 640),
        target: match[3] ? match[3].trim() : '',
      },
      raw: text,
    }
  }
  if (verb === 'place') {
    if (!rest) return null
    return { verb: 'place', args: normalizeItemName(rest.replace(/\d+/g, '').trim()), raw: text }
  }
  if (verb === 'chest') {
    // "chest find" / "chest list" / bare "chest"
    const sub = (restParts[0] || 'find').toLowerCase()
    if (['find', 'list', 'nearby', 'near'].includes(sub)) {
      return { verb: 'chest_find', args: '', raw: text }
    }
    return null
  }
  if (verb === 'deposit' || verb === 'store') {
    // deposit <item> [N|all] - default all: "存箱子" usually means the whole stack family.
    const tokens = rest ? rest.split(/\s+/).filter(Boolean) : []
    if (!tokens.length) return null
    let count = 'all'
    if (/^all$/i.test(tokens[tokens.length - 1])) tokens.pop()
    else if (tokens.length > 1 && /^\d+$/.test(tokens[tokens.length - 1])) {
      count = boundedCount(tokens.pop(), 1, 640)
    }
    return { verb: 'chest_deposit', args: { item: normalizeItemName(tokens.join(' ')), count }, raw: text }
  }
  if (verb === 'withdraw') {
    // withdraw <item> [N|all] - default 1: taking usually means a few.
    const tokens = rest ? rest.split(/\s+/).filter(Boolean) : []
    if (!tokens.length) return null
    let count = 1
    if (/^all$/i.test(tokens[tokens.length - 1])) {
      count = 'all'
      tokens.pop()
    } else if (tokens.length > 1 && /^\d+$/.test(tokens[tokens.length - 1])) {
      count = boundedCount(tokens.pop(), 1, 640)
    }
    return { verb: 'chest_withdraw', args: { item: normalizeItemName(tokens.join(' ')), count }, raw: text }
  }
  if (['attack', 'fight', 'defend', 'kill'].includes(verb)) {
    const tokens = rest ? rest.split(/\s+/).filter(Boolean) : []
    let count = 1
    if (tokens.length && /^\d+$/.test(tokens[tokens.length - 1])) {
      count = boundedCount(tokens.pop(), 1, 32)
    }
    return {
      verb: 'attack',
      args: { target: tokens.join('_') || 'nearest', count },
      raw: text,
    }
  }
  if (['jump', 'hop'].includes(verb)) return { verb: 'jump', args: '', raw: text }
  if (['swim', 'float'].includes(verb)) return { verb: 'swim', args: rest || 'up', raw: text }
  if (verb === 'sleep' || verb === 'bed') return { verb: 'sleep', args: '', raw: text }
  return { verb: 'say', args: text, raw: text }
}

export function matchesBlock(name, wanted) {
  const actual = String(name ?? '').replace(/^minecraft:/, '')
  const target = normalizeBlockName(wanted)
  if (target === '_log') return actual.endsWith('_log') || actual.endsWith('_stem')
  if (target.endsWith('_ore')) return actual === target || actual === `deepslate_${target}`
  // "stone" is exact-only: red_sandstone etc. must not satisfy a cobblestone
  // goal (they drop different items).
  if (target === 'stone') return actual === 'stone'
  return actual === target || actual.endsWith(`_${target}`)
}

export function matchesItem(name, wanted) {
  const actual = String(name ?? '').replace(/^minecraft:/, '')
  const target = normalizeItemName(wanted)
  if (target === '_log') return actual.endsWith('_log') || actual.endsWith('_stem') || actual.endsWith('_wood') || actual.endsWith('_hyphae')
  if (target === 'planks') return actual.endsWith('_planks')
  if (target === 'wool') return actual === 'wool' || actual.endsWith('_wool')
  if (target.endsWith('_ore')) return actual === target || actual === `deepslate_${target}`
  return actual === target || actual.endsWith(`_${target}`)
}

// Player inventory window slots for equipment destinations (mineflayer).
export const DESTINATION_SLOT = { head: 5, torso: 6, legs: 7, feet: 8, 'off-hand': 45 }

// Blocks the auto-mine toggle goes after, grouped per selectable ore type.
// The console checks individual groups (钻石/铁/...); only checked groups
// are ever mined.  Deepslate variants belong to the same group.
export const AUTO_MINE_GROUPS = {
  coal: ['coal_ore', 'deepslate_coal_ore'],
  copper: ['copper_ore', 'deepslate_copper_ore'],
  iron: ['iron_ore', 'deepslate_iron_ore'],
  gold: ['gold_ore', 'deepslate_gold_ore', 'nether_gold_ore'],
  redstone: ['redstone_ore', 'deepslate_redstone_ore'],
  lapis: ['lapis_ore', 'deepslate_lapis_ore'],
  diamond: ['diamond_ore', 'deepslate_diamond_ore'],
  emerald: ['emerald_ore', 'deepslate_emerald_ore'],
  quartz: ['nether_quartz_ore'],
  ancient_debris: ['ancient_debris'],
}

export const AUTO_MINE_ORES = new Set(Object.values(AUTO_MINE_GROUPS).flat())

// Per-ore detection distance bounds (blocks).  Each ore group is its own
// auto-mine mode: an enable flag plus how far Saya looks for it.
export const ORE_MINE_MIN_RANGE = 4
export const ORE_MINE_MAX_RANGE = 64
export const ORE_MINE_DEFAULT_RANGE = 24

/** Console per-ore auto-mine modes -> {group: {enabled, distance}}.
 *  Unknown groups are dropped, distances clamped to [4, 64].  A mode entry
 *  without its own distance falls back to the configured default range
 *  (auto_mine_range), then the built-in default.  The legacy flat target
 *  list is still accepted and enables those groups at the default range
 *  (explicit entries win, so a mode stays disableable). */
export function parseAutoMineOreModes(next = {}) {
  const modes = {}
  const defaultRange = Math.max(ORE_MINE_MIN_RANGE, Math.min(
    ORE_MINE_MAX_RANGE,
    Math.round(Number(next?.auto_mine_range) || ORE_MINE_DEFAULT_RANGE),
  ))
  const raw = next?.auto_mine_ore_modes
  if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
    for (const [group, value] of Object.entries(raw)) {
      if (!(group in AUTO_MINE_GROUPS) || !value || typeof value !== 'object') continue
      const distance = Math.max(ORE_MINE_MIN_RANGE, Math.min(
        ORE_MINE_MAX_RANGE,
        Math.round(Number(value.distance) || defaultRange),
      ))
      modes[group] = { enabled: Boolean(value.enabled), distance }
    }
  }
  if (Array.isArray(next?.auto_mine_targets)) {
    for (const group of next.auto_mine_targets) {
      if (typeof group === 'string' && group in AUTO_MINE_GROUPS && !modes[group]) {
        modes[group] = { enabled: true, distance: defaultRange }
      }
    }
  }
  return modes
}

/** Which selectable group a block belongs to ('' when it is none). */
export function oreGroupForBlock(name) {
  const actual = String(name ?? '').replace(/^minecraft:/, '')
  for (const [group, blocks] of Object.entries(AUTO_MINE_GROUPS)) {
    if (blocks.includes(actual)) return group
  }
  return ''
}

/** Which equipment destination an item belongs in ('hand' for everything
 *  that is not wearable).  Armor must be equipped to its own slot - the old
 *  equip flow hardcoded 'hand', so helmets ended up held, never worn. */
export function armorDestinationFor(name) {
  const actual = String(name ?? '').toLowerCase().replace(/^minecraft:/, '')
  if (actual === 'shield') return 'off-hand'
  if (actual.endsWith('_helmet')) return 'head'
  if (actual.endsWith('_chestplate')) return 'torso'
  if (actual.endsWith('_leggings')) return 'legs'
  if (actual.endsWith('_boots')) return 'feet'
  return 'hand'
}
