/**
 * Passage blocks: doors, trapdoors and fence gates.
 *
 * mineflayer-pathfinder 2.4.5 treats every block with a collision box as a
 * wall: `physical = boundingBox === 'block'` ignores the open/closed block
 * state, and its `openable` set only contains fence gates (names containing
 * 'gate').  Doors were therefore impassable walls - open or closed - and
 * during follow (canDig=false) absolutely impassable.
 *
 * enhanceMovementsForPassages wraps Movements.getBlock so that:
 *
 * - an OPEN door / trapdoor / gate is walkable (the swung panel sits against
 *   the frame, not in the doorway); height is reset so it is not mistaken
 *   for a step-up, and openable is cleared so the executor does not close it
 *   again while passing;
 * - a CLOSED wooden door / trapdoor / gate becomes `openable`, which routes
 *   the forward move through the pathfinder's native useOne flow (the
 *   executor right-clicks the block via bot.activateBlock and walks through);
 * - the closed UPPER half of a door is treated as free space (it opens
 *   together with the lower half; without this the head-level clearance
 *   check would demand breaking the door);
 * - iron doors / iron trapdoors are untouched: they cannot be hand-opened
 *   and remain solid (breakable only when digging is allowed).
 */

const PASSAGE_SUFFIXES = ['_door', '_trapdoor', '_fence_gate', '_gate']

export function isPassageBlockName(name) {
  if (!name || typeof name !== 'string') return false
  if (name === 'iron_door' || name.startsWith('iron_')) return false
  return PASSAGE_SUFFIXES.some(suffix => name.endsWith(suffix))
}

export function enhanceMovementsForPassages(bot, movements) {
  if (!bot || !movements || typeof movements.getBlock !== 'function') return movements
  const originalGetBlock = movements.getBlock.bind(movements)

  movements.getBlock = (pos, dx, dy, dz) => {
    const info = originalGetBlock(pos, dx, dy, dz)
    if (!info || !info.name || !isPassageBlockName(info.name)) return info

    const props = info.properties || {}
    if (props.open === true) {
      info.physical = false
      info.safe = true
      info.openable = false
      if (pos) info.height = pos.y + dy
    } else if (info.name.endsWith('_door') && props.half === 'upper') {
      info.physical = false
      info.safe = true
    } else {
      info.openable = true
    }
    return info
  }
  return movements
}
