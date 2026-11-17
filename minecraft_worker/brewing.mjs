const POTION_ALIASES = new Map(Object.entries({
  '水瓶': 'water', '粗制药水': 'awkward',
  '夜视': 'night_vision', '夜视药水': 'night_vision',
  '隐身': 'invisibility', '隐身药水': 'invisibility',
  '跳跃': 'leaping', '跳跃药水': 'leaping',
  '抗火': 'fire_resistance', '抗火药水': 'fire_resistance', '防火药水': 'fire_resistance',
  '迅捷': 'swiftness', '迅捷药水': 'swiftness', '速度药水': 'swiftness',
  '缓慢': 'slowness', '缓慢药水': 'slowness',
  '水肺': 'water_breathing', '水肺药水': 'water_breathing',
  '治疗': 'healing', '治疗药水': 'healing', '瞬间治疗': 'healing', '瞬间治疗药水': 'healing',
  '伤害': 'harming', '伤害药水': 'harming', '瞬间伤害': 'harming', '瞬间伤害药水': 'harming',
  '剧毒': 'poison', '剧毒药水': 'poison', '中毒药水': 'poison',
  '再生': 'regeneration', '再生药水': 'regeneration',
  '力量': 'strength', '力量药水': 'strength',
  '虚弱': 'weakness', '虚弱药水': 'weakness',
  '缓降': 'slow_falling', '缓降药水': 'slow_falling',
  '强效治疗': 'strong_healing', '强效治疗药水': 'strong_healing',
  '强效伤害': 'strong_harming', '强效伤害药水': 'strong_harming',
  '强效剧毒': 'strong_poison', '强效剧毒药水': 'strong_poison',
  '强效再生': 'strong_regeneration', '强效再生药水': 'strong_regeneration',
  '强效力量': 'strong_strength', '强效力量药水': 'strong_strength',
  '强效迅捷': 'strong_swiftness', '强效迅捷药水': 'strong_swiftness',
  '延长夜视': 'long_night_vision', '延长夜视药水': 'long_night_vision',
  '延长隐身': 'long_invisibility', '延长隐身药水': 'long_invisibility',
  '延长抗火': 'long_fire_resistance', '延长抗火药水': 'long_fire_resistance',
  '延长迅捷': 'long_swiftness', '延长迅捷药水': 'long_swiftness',
  '延长水肺': 'long_water_breathing', '延长水肺药水': 'long_water_breathing',
  '延长剧毒': 'long_poison', '延长剧毒药水': 'long_poison',
  '延长再生': 'long_regeneration', '延长再生药水': 'long_regeneration',
  '延长力量': 'long_strength', '延长力量药水': 'long_strength',
  '延长虚弱': 'long_weakness', '延长虚弱药水': 'long_weakness',
  '延长缓降': 'long_slow_falling', '延长缓降药水': 'long_slow_falling',
}))

// A deliberately conservative subset of vanilla recipes.  Every edge is a
// real brewing-stand transformation and therefore can be verified from the
// resulting potion_contents component.
export const BREWING_RECIPES = new Map([
  ['awkward', { input: 'water', ingredient: 'nether_wart' }],
  ['night_vision', { input: 'awkward', ingredient: 'golden_carrot' }],
  ['invisibility', { input: 'night_vision', ingredient: 'fermented_spider_eye' }],
  ['leaping', { input: 'awkward', ingredient: 'rabbit_foot' }],
  ['fire_resistance', { input: 'awkward', ingredient: 'magma_cream' }],
  ['swiftness', { input: 'awkward', ingredient: 'sugar' }],
  ['slowness', { input: 'swiftness', ingredient: 'fermented_spider_eye' }],
  ['water_breathing', { input: 'awkward', ingredient: 'pufferfish' }],
  ['healing', { input: 'awkward', ingredient: 'glistering_melon_slice' }],
  ['harming', { input: 'healing', ingredient: 'fermented_spider_eye' }],
  ['poison', { input: 'awkward', ingredient: 'spider_eye' }],
  ['regeneration', { input: 'awkward', ingredient: 'ghast_tear' }],
  ['strength', { input: 'awkward', ingredient: 'blaze_powder' }],
  ['weakness', { input: 'water', ingredient: 'fermented_spider_eye' }],
  ['slow_falling', { input: 'awkward', ingredient: 'phantom_membrane' }],
  ['long_night_vision', { input: 'night_vision', ingredient: 'redstone' }],
  ['long_invisibility', { input: 'invisibility', ingredient: 'redstone' }],
  ['long_leaping', { input: 'leaping', ingredient: 'redstone' }],
  ['strong_leaping', { input: 'leaping', ingredient: 'glowstone_dust' }],
  ['long_fire_resistance', { input: 'fire_resistance', ingredient: 'redstone' }],
  ['long_swiftness', { input: 'swiftness', ingredient: 'redstone' }],
  ['strong_swiftness', { input: 'swiftness', ingredient: 'glowstone_dust' }],
  ['long_slowness', { input: 'slowness', ingredient: 'redstone' }],
  ['strong_slowness', { input: 'slowness', ingredient: 'glowstone_dust' }],
  ['long_water_breathing', { input: 'water_breathing', ingredient: 'redstone' }],
  ['strong_healing', { input: 'healing', ingredient: 'glowstone_dust' }],
  ['strong_harming', { input: 'harming', ingredient: 'glowstone_dust' }],
  ['long_poison', { input: 'poison', ingredient: 'redstone' }],
  ['strong_poison', { input: 'poison', ingredient: 'glowstone_dust' }],
  ['long_regeneration', { input: 'regeneration', ingredient: 'redstone' }],
  ['strong_regeneration', { input: 'regeneration', ingredient: 'glowstone_dust' }],
  ['long_strength', { input: 'strength', ingredient: 'redstone' }],
  ['strong_strength', { input: 'strength', ingredient: 'glowstone_dust' }],
  ['long_weakness', { input: 'weakness', ingredient: 'redstone' }],
  ['long_slow_falling', { input: 'slow_falling', ingredient: 'redstone' }],
])

export function normalizePotionName(value) {
  let name = String(value || '').trim().toLowerCase().replace(/^minecraft:/, '').replaceAll(' ', '_')
  if (POTION_ALIASES.has(name)) return POTION_ALIASES.get(name)
  name = name.replace(/_potion$/, '').replace(/^potion_of_/, '')
  return POTION_ALIASES.get(name) || name
}

/** Return ordered {input, output, ingredient} edges from water to target. */
export function brewingPath(target) {
  const wanted = normalizePotionName(target)
  if (!wanted || wanted === 'water') return []
  const reversed = []
  const visited = new Set()
  let output = wanted
  while (output !== 'water') {
    if (visited.has(output)) throw new Error(`循环炼药配方：${output}`)
    visited.add(output)
    const recipe = BREWING_RECIPES.get(output)
    if (!recipe) throw new Error(`暂不支持酿造 ${wanted}`)
    reversed.push({ input: recipe.input, output, ingredient: recipe.ingredient })
    output = recipe.input
  }
  return reversed.reverse()
}

