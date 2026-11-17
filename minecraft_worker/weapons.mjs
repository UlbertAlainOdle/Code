// Weapon selection policy shared by every attack scenario (auto-combat,
// explicit attack tasks, defense): pick the best melee weapon from the live
// inventory under two modes.
//
// - Healthy: minimise WEAR PER KILL (hits needed = ceil(targetHp / damage)),
//   i.e. the cheapest weapon that still kills quickly; ties prefer the
//   lower-material weapon and then the higher remaining durability.  A
//   wooden sword on a cow beats burning diamond-sword durability.
// - Injured (health <= injuredThreshold): maximum damage, durability be
//   damned - kill it NOW before it kills her.
//
// Damage values are Java-edition base melee damage (hearts*2); axes hit
// harder per swing but swing slower - for per-swing durability accounting
// they still cost 1 durability per hit, so hits-to-kill stays the right
// wear metric for this policy.

export const WEAPON_DAMAGE = {
  wooden_sword: 4, golden_sword: 4, stone_sword: 5, iron_sword: 6,
  diamond_sword: 7, netherite_sword: 8,
  wooden_axe: 7, golden_axe: 7, stone_axe: 9, iron_axe: 9,
  diamond_axe: 9, netherite_axe: 10,
  trident: 9, mace: 6,
}

// Material rarity weight: higher = more expensive to replace/repair.
export const WEAPON_RARITY = {
  wooden: 1, golden: 2, stone: 2, iron: 3, diamond: 5, netherite: 8,
}

// Held items that should be PUT AWAY after combat/mining ends, so later
// accidental swings/uses cannot wear them (持手换非工具以减少损耗).
const WIELDABLE_SUFFIXES = [
  '_sword', '_axe', '_pickaxe', '_shovel', '_hoe',
]
const WIELDABLE_EXACT = new Set(['trident', 'mace'])

export function isWieldableTool(name) {
  const value = String(name ?? '').replace(/^minecraft:/, '')
  return WIELDABLE_EXACT.has(value) || WIELDABLE_SUFFIXES.some(suffix => value.endsWith(suffix))
}

export const INJURED_HEALTH_THRESHOLD = 12

function rarityOf(name) {
  const material = String(name || '').split('_')[0]
  return WEAPON_RARITY[material] ?? 4
}

function remainingDurability(item) {
  const max = Number(item?.maxDurability)
  if (!(max > 0)) return Infinity
  const used = Number(item?.durabilityUsed)
  return Math.max(0, max - (Number.isFinite(used) ? used : 0))
}

/** All wieldable melee weapons found in an inventory item list. */
export function weaponCandidates(items) {
  return (items || [])
    .filter(item => item && Number(WEAPON_DAMAGE[item.name]) > 0)
    .map(item => ({
      item,
      name: item.name,
      damage: WEAPON_DAMAGE[item.name],
      rarity: rarityOf(item.name),
      durability: remainingDurability(item),
    }))
}

/**
 * Pick the best weapon for the situation.
 *   items        worker inventory items (or any {name, maxDurability, durabilityUsed} list)
 *   health       current bot health (default: healthy)
 *   maxHealth    for threshold scaling when health is omitted
 *   targetHp     the intended victim's max HP (default 20); 0/undefined -> generic
 * Returns the chosen candidate object, or null when no weapon is carried.
 */
export function chooseWeapon(items, { health = null, maxHealth = 20, targetHp = 20 } = {}) {
  const candidates = weaponCandidates(items)
  if (!candidates.length) return null
  const injured = Number.isFinite(Number(health))
    && Number(health) <= INJURED_HEALTH_THRESHOLD
    && Number(health) <= Number(maxHealth)
  const hp = Math.max(1, Number(targetHp) || 20)
  if (injured) {
    // Survival mode: maximum damage per swing; ties -> more durability left.
    candidates.sort((a, b) =>
      (b.damage - a.damage)
      || (b.durability - a.durability)
      || (a.rarity - b.rarity))
    return candidates[0]
  }
  // Economy mode: fewest hits to kill (least durability spent), then the
  // cheaper material, then the healthier copy.
  candidates.sort((a, b) =>
    (Math.ceil(hp / a.damage) - Math.ceil(hp / b.damage))
    || (a.rarity - b.rarity)
    || (b.durability - a.durability))
  return candidates[0]
}
