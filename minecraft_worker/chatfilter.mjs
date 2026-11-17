// Player-chat forwarding policy for the worker.
//
// The old code listened on mineflayer's deprecated 'chat' event, which is
// produced by a LEGACY REGEX matched against EVERY message string -
// including system messages.  Command feedback broadcast by the server
// ("[Kurosawa_Jin] 已传送到...", "Kurosawa_Jin: 已设置家") has exactly that
// shape, so slash-command output got re-packaged as if the OWNER had said
// it, and Saya happily replied to a command's echo.
//
// The modern contract: the 'message' event carries the packet POSITION -
// 'chat' (a real player_chat packet), 'system' or 'game_info' (systemChat
// packets: command feedback, join/leave, death messages).  Only genuine
// player chat is ever forwarded; the sender must also resolve to a real
// online player via the packet's sender UUID.

/** Which 'message' event positions count as player chat. */
export function isPlayerChatPosition(position) {
  return position === 'chat'
}

/** Resolve a playerChat packet's sender UUID to an online player entry. */
export function resolveChatSender(players, senderUuid) {
  if (!senderUuid) return null
  const wanted = String(senderUuid)
  const entry = Object.values(players || {})
    .find(player => String(player?.UUID ?? player?.uuid ?? '') === wanted)
  return entry?.username ? entry : null
}

function escapeRegex(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/** Strip the "<name> " / "name> " decoration a chat type may prepend. */
export function stripChatDecoration(text, username) {
  const raw = String(text ?? '')
  const name = escapeRegex(username)
  return raw
    .replace(new RegExp(`^<${name}>\\s*`), '')
    .replace(new RegExp(`^${name}\\s*[>:\\-»\\]\\)~]+\\s*`), '')
    .trim()
}
