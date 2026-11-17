# Saya Minecraft worker

This directory contains the Minecraft execution layer for Saya. The Python
web app remains responsible for conversation, memory, configuration, command
validation and (since the 2026-09 plan) goal planning. This private Node.js
child process owns the Minecraft protocol, physics, full inventory,
pathfinding and game actions.

Communication uses newline-delimited JSON over the child process standard
input/output pipes. No additional HTTP port is opened.

## Setup

Node.js is installed in the `Saya` Conda environment. Install or repair the
worker dependencies with:

```text
conda activate Saya
npm --prefix minecraft_worker install
```

`start_saya.bat` checks for Mineflayer before launching and displays the repair
command if dependencies are missing.

## Supported commands

- Persistent state: `follow Doctor`, `unfollow`, `stop`, `status`
- Movement: `come`, `goto x y z`, `jump`, `swim up`
- World actions: `chop n`, `collect block n`, `mine x y z`, `mine below`,
  `mine nearby` (experimental)
- Inventory: `inventory`, `equip item`, `drop item n|all` (legacy
  `drop one|stack`), `give item n to player`, `pickup nearest`, `eat`
- Crafting and combat: `craft item n`, `attack target`, `place block`

The unified capability catalog (`saya_app/services/minecraft_catalog.py`,
exported here as `command_catalog.json`) is the single source of truth shared
by the desktop console, the LLM whitelist and the contract tests.

## Death and respawn

Saya never disconnects on damage.  Survival emergencies (suffocation / lava /
drowning / low health / hostile policy) run on the live connection, and when
health reaches 0 the same client asks the server for respawn
(`respawn: true` in the createBot options plus a retry loop in the death
handler) - vanilla servers keep the client on the death screen until the
CLIENT requests respawn, so without this the bot would be stuck out of the
world after every death.  Follow re-engages automatically after the respawn
spawn event; interrupted tasks stay cancelled and are reported.

## Knockback / movement safety patch

mineflayer 4.33.0 reads `packet.velocityX/Y/Z` in its `entity_velocity`
handler, but the 1.21.5 protocol schema (minecraft-data 3.115.0) delivers the
same data as `packet.velocity = {x, y, z}`.  The mismatch made every knockback
write NaN into the physics velocity, and the next move packet carried
non-finite coordinates - which vanilla 1.21.5 rejects with
`multiplayer.disconnect.invalid_player_movement` (handleMovePlayer checks
isFinite on x/y/z/yaw/pitch).  Saya was therefore kicked the instant she was
hit by anything with knockback (players, mobs, explosions), while drowning -
the one damage type without knockback - never kicked.

worker.mjs installs two guards at connect time:

1. `patchEntityVelocityHandler` - rebinds the handler schema-aware (root fix).
2. `installMovementGuards` - outgoing `position`/`position_look` packets are
   sanitized: non-finite packets are rewritten to the last finite spot (and
   the poisoned physics state snaps back), oversized jumps are claimed as
   zero movement (always legal; the server rubber-bands instead of kicking),
   and server teleport confirmations pass untouched.

`test/repro_move_kick.mjs` reproduces the original bug against a live LAN
server (two diagnostic bots; the attacker punches the instrumented victim) -
unpatched: NaN move packet + kick on the first hit; patched: stays connected.

## Planning integration

Python may send commands with `"origin": "plan", "quiet": true`: the worker
suppresses the per-step start/finish announcements for those (the PlanExecutor
reports on its own) while all activity/state events stay identical, so the
executor can wait for each step's activity to finish, verify the inventory
postcondition, and only then send the next step. Only ONE foreground command
is ever in flight; a plan is never a burst of competing commands.

## State additions

The 1s state snapshot additionally reports: full inventory with hotbar and
held item (incl. durability), nearby crafting tables / furnaces
(`runtime.stations`, cached 5s), hostile flags on nearby entities, and
`last_craft` (`{at, name, count}`) for "刚做的" references.

## Doors, trapdoors and fence gates

mineflayer-pathfinder 2.4.5 treats every block with a collision box as a wall
(`physical = boundingBox === 'block'`, ignoring the open/closed state) and its
`openable` set only contains fence gates.  Doors were therefore impassable -
open or closed - and during follow (canDig=false) absolutely impassable.

`passage.mjs` (wired into every Movements instance in `createMovements`)
wraps `Movements.getBlock`:

- OPEN door / trapdoor / gate -> walkable (panel is against the frame; height
  is reset so it is not mistaken for a step-up, and openable is cleared so
  the executor cannot close it again while passing);
- CLOSED wooden door / trapdoor / gate -> marked openable, which routes the
  forward move through the pathfinder's native useOne flow (the executor
  right-clicks the block with bot.activateBlock and walks through);
- the closed UPPER half of a door is free space (it opens with the lower
  half; otherwise the head-clearance check demands breaking the door);
- iron doors / iron trapdoors stay solid (cannot be hand-opened).

Unit-tested in `test/passage.test.mjs` against the real 1.21.5 registry.

## Checks

```text
node --check minecraft_worker/worker.mjs
node --test minecraft_worker/test/command.test.mjs minecraft_worker/test/catalog.test.mjs
python -B tests/smoke_mineflayer_manager.py
python -B tests/smoke_minecraft_bot.py
python -B tests/smoke_catalog_contract.py
```

After editing `saya_app/services/minecraft_catalog.py`, regenerate the export
read by the Node contract test:

```text
python -B scripts/export_command_catalog.py
```
