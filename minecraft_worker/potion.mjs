function stripNamespace(value) {
  return String(value || '').replace(/^minecraft:/, '')
}

// Registry declaration order in Java 1.21.5.  Newer protocol item stacks may
// store only the numeric registry id inside potion_contents, while some
// servers omit registry_data for built-in registries.  Keep this fallback
// strictly version-scoped; a server-provided registry always replaces it.
export const VANILLA_POTIONS_1_21_5 = [
  'water', 'mundane', 'thick', 'awkward',
  'night_vision', 'long_night_vision', 'invisibility', 'long_invisibility',
  'leaping', 'long_leaping', 'strong_leaping',
  'fire_resistance', 'long_fire_resistance',
  'swiftness', 'long_swiftness', 'strong_swiftness',
  'slowness', 'long_slowness', 'strong_slowness',
  'turtle_master', 'long_turtle_master', 'strong_turtle_master',
  'water_breathing', 'long_water_breathing',
  'healing', 'strong_healing', 'harming', 'strong_harming',
  'poison', 'long_poison', 'strong_poison',
  'regeneration', 'long_regeneration', 'strong_regeneration',
  'strength', 'long_strength', 'strong_strength',
  'weakness', 'long_weakness', 'luck',
  'slow_falling', 'long_slow_falling',
  'wind_charged', 'weaving', 'oozing', 'infested',
]

export function installVanillaPotionRegistry(version, destination = new Map()) {
  if (String(version || '') !== '1.21.5') return false
  destination.clear()
  VANILLA_POTIONS_1_21_5.forEach((name, index) => destination.set(index, name))
  return true
}

/** Capture the server's dynamic minecraft:potion registry (1.20.5+). */
export function loadPotionRegistryPacket(packet, destination = new Map()) {
  const registryName = stripNamespace(packet?.id)
  if (registryName !== 'potion' || !Array.isArray(packet?.entries)) return destination
  destination.clear()
  packet.entries.forEach((entry, index) => {
    if (entry?.key) destination.set(index, stripNamespace(entry.key))
  })
  return destination
}

function component(item, name) {
  const direct = item?.componentMap?.get?.(name)
  if (direct) return direct
  return (item?.components || []).find(entry => entry?.type === name) || null
}

/** Return a stable identity for potion stacks across NBT/component versions. */
export function potionIdentity(item, potionNamesById = new Map()) {
  const container = stripNamespace(item?.name)
  if (!['potion', 'splash_potion', 'lingering_potion'].includes(container)) return null

  const contents = component(item, 'potion_contents')?.data || null
  let potion = contents?.potionId ?? contents?.potion ?? null
  if (typeof potion === 'number') potion = potionNamesById.get(potion) || `#${potion}`
  else if (potion && typeof potion === 'object') potion = potion.name ?? potion.value ?? null
  if (potion != null) potion = stripNamespace(potion)

  potion ||= stripNamespace(
    item?.nbt?.value?.Potion?.value || item?.nbt?.value?.potion?.value || ''
  )
  // In modern protocol the default water component may be omitted from an
  // otherwise plain drinkable potion stack.
  if (!potion && container === 'potion' && !contents) potion = 'water'

  return {
    container,
    potion: potion || 'unknown',
    custom_effects: Array.isArray(contents?.customEffects) ? contents.customEffects.length : 0,
  }
}

export function potionMatches(item, wantedPotion, potionNamesById = new Map()) {
  const identity = potionIdentity(item, potionNamesById)
  return Boolean(identity && identity.potion === stripNamespace(wantedPotion))
}

/**
 * Full-identity match for delivery-grade operations.  Unlike potionMatches
 * (which only compares the effect id) this also requires the same container
 * (potion / splash_potion / lingering_potion) and refuses stacks carrying
 * custom effects - phase 1 only handles vanilla potions automatically.
 */
export function potionDescriptorMatches(item, wanted, potionNamesById = new Map()) {
  if (!wanted || typeof wanted !== 'object' || !wanted.potion) return false
  const identity = potionIdentity(item, potionNamesById)
  if (!identity) return false
  if (wanted.container && identity.container !== stripNamespace(wanted.container)) return false
  if (identity.potion !== stripNamespace(wanted.potion)) return false
  return identity.custom_effects === 0
}

/** Parse "potion/healing" style variant keys; null for plain item names. */
export function parseVariantItem(value) {
  const text = String(value ?? '').trim()
  const slash = text.indexOf('/')
  if (slash <= 0 || slash === text.length - 1) return null
  const base = stripNamespace(text.slice(0, slash).trim().toLowerCase())
  const potion = stripNamespace(text.slice(slash + 1).trim().toLowerCase())
  if (!base || !potion) return null
  return { base, potion }
}

/**
 * Select the exact Item objects for a variant target.  Callers must re-run
 * this against live inventory before EVERY single toss: after a toss the
 * remaining stacks shift slots, and mineflayer's toss(type, metadata, count)
 * would otherwise grab the first same-type stack (all 1.21.5 potions share
 * the same metadata), i.e. possibly the wrong potion.
 */
export function selectPotionStacks(items, wanted, potionNamesById = new Map()) {
  const picked = []
  let remaining = Number.isFinite(wanted?.count) ? Math.max(0, wanted.count) : Infinity
  for (const item of items || []) {
    if (remaining <= 0) break
    if (!item || stripNamespace(item.name) !== wanted?.base) continue
    if (!potionDescriptorMatches(item, { container: wanted?.base, potion: wanted?.potion }, potionNamesById)) continue
    picked.push(item)
    remaining -= item.count || 1
  }
  return picked
}
