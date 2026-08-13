# Messaging and streaming behavior

This page documents the adapter's message semantics, especially UMO identity, wake conditions, replies, album debounce, and streaming fallback.

## Inbound messages

The adapter normalizes text, images, audio, video, documents, stickers, locations, contacts, button/list responses, polls, native events, quoted messages, and mention metadata into AstrBot events.

Reaction-only inbound messages are currently recognized and then ignored instead of being dispatched as ordinary AstrBot events. Historical `inbound_reaction_events` configuration is deprecated.

## Formatting

With `default_parse_inbound_formatting=true`, common WhatsApp bold, italic, strikethrough, and code syntax is converted into Markdown. Recent compatibility fixes also prevent isolated backticks inside emoticons from swallowing the Markdown that follows.

## Stable public UMO identity

Since v0.2.37, PN, LID, Hosted, device JIDs, and group `@g.us` identifiers remain transport metadata in `raw_message` / `target_jid`, while AstrBot sessions use stable public projections:

| Context | `session_id` |
| --- | --- |
| DM | confirmed PN as a numeric ID; unresolved LID as `lid-N` |
| Group, session isolation off | group-JID local part (numeric or legacy `number-number`) |
| Group, session isolation on | `userID_groupID` |

`sender.user_id`, `self_id`, `group_id`, and common OneBot projection fields follow the same stable convention. Proactive sends remain compatible with legacy PN/LID/group-JID session strings.

The Gateway tries to resolve PN/LID before a first unknown-LID message enters AstrBot. The first public projection is persisted; later resolution does not move the UMO, and a contact previously exposed under two projections is merged once toward the earliest public ID.

## Replies and group wake semantics

Quoted-message metadata and wake signals are deliberately separate.

- Quoted content, nickname, message ID, and QQ-compatible fields are preserved for downstream plugins.
- Quoting a bot message **does not impersonate an @mention**.
- Group replies are triggered only by a real @bot, mention-all, command, or another normal AstrBot Core wake condition.
- Pre-ack reactions are independent from wake state and cannot turn an otherwise ordinary group message into a wake event.
- Outbound WhatsApp quoting happens only when the outgoing `MessageChain` contains `Reply`, and split messages consume the quote only once.

## Private image burst / album debounce

The default `default_media_album_debounce_seconds` is `2.5` seconds.

Candidate images are buffered per socket generation + chat JID + sender JID. Later text, replies, non-image media, or other semantic messages flush pending images first to preserve ordering.

Private captioned image bursts retain per-image captions, mentions, display names, and mention-all metadata where possible. Captioned group images remain conservative and are not blindly merged.

Set the debounce to `0` to disable coalescing.

## Outbound MessageChain

Standard components include `Plain`, `Reply`, `At` / `AtAll`, `Image`, `Record`, `Video`, `File`, and `Location`. WhatsApp-specific components include buttons, lists, polls, and message editing.

## Streaming replies

The platform declares `support_streaming_message=True`. The default edit throttle is `1.0s`.

1. Send the first visible chunk with `/send/text`.
2. Edit existing chunks through `/edit/text` after the throttle interval.
3. Send additional chunks when rendered text outgrows the current editable messages.
4. Flush text before sending media or other non-text components.
5. Force a final render/flush when the stream ends.

If edits become unsafe or unavailable, the adapter stops editing and uses the already-delivered raw offset to send only the required remaining/final content, minimizing duplicate whole-message fallbacks.

Concurrent streams keep independent state. Typing presence is coordinated so completion of one reply does not prematurely stop another reply still in progress.

## Pre-ack reaction

Pre-ack is an outbound reaction sent by the bot before the model finishes. It is not the same thing as handling inbound reactions and it does not affect wake state.

The defaults use `👀` before the reply and try to replace/send `✅` after successful delivery. Group behavior is controlled by `pre_ack_public=always/mentions/never`.

## Presence and read receipts

- `default_typing_indicator=true`: send composing / paused while replies are active.
- `default_mark_online=false`: do not stay globally available, although reply activity can make the account briefly visible before it returns to unavailable.
- `default_send_read_receipts=true`: mark accepted inbound messages read.

## Group metadata

The Gateway obtains group subject, owner, admins, and participants from Baileys GroupMetadata and fills AstrBot-compatible group-name/group-info fields. Metadata is cached and refreshed from `groups.update`.

## Ephemeral messages

With `apply_ephemeral=true`, outbound messages use the chat's real ephemeral expiration and setting timestamp. Incomplete or contradictory metadata falls back to an ordinary message instead of fabricating a timestamp.

## Native AI tools

The plugin exposes current-conversation-only tools for polls, contact cards, and native events. The model cannot provide an arbitrary destination JID; Python and Gateway validation keep each operation bound to the current WhatsApp conversation.

## Internal Gateway message routes

Current internal routes include `/send/text`, `/edit/text`, `/send/media`, `/send/location`, `/send/reaction`, `/send/buttons`, `/send/list`, `/send/poll`, `/send/contact`, and `/send/event`.

These are internal plugin protocol routes and are not a long-term public API contract.
