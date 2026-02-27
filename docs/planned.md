# meshMUD — Planned Design Document

Everything decided so far. No code, just concepts and rationale.

---

## Core Constraints

- 175 characters per Meshtastic LoRa message (200 overflow max)
- 30-day epoch (wipe cycle) — everything except player accounts and meta-progression resets
- 5-30 active players on a mesh network
- Async play — no guarantee two players are ever online simultaneously
- 5-15 minute daily sessions over slow radio links
- Hybrid model: individual gameplay in DMs, notable events broadcast to shared channel

## Genre Identity

meshMUD models BBS door games (Legend of the Red Dragon, TradeWars 2002, Barren Realms Elite), not classic DikuMUDs. Door games operated under identical constraints: limited daily sessions, asynchronous play, bandwidth scarcity, seasonal competition, leaderboard-driven engagement.

---

## Character System

### Stats: Three

- **POW** — offense, physical/spell damage
- **DEF** — damage reduction, survivability
- **SPD** — evasion, utility, initiative, caster scaling

Three stats produce seven viable archetypes (three pure, three hybrid, one generalist). Each stat anchors one class. Every stat fits in a compact display. Evidence: DCSS deliberately pruned from six to three. LORD ran on three combat-relevant numbers for decades.

Auto-derived from class and level. No point-buy allocation at creation. Players receive 2 stat points per level-up to allocate freely — this is where build diversity emerges.

### Classes: Three

- **Fighter** — POW-focused. High HP, melee verbs (strike, bash, rally, cleave). Passive: damage reduction.
- **Caster** — SPD-focused (spells scale on SPD). Low HP, spell verbs (bolt, ward, blast, drain). Passive: see enemy stats.
- **Rogue** — Balanced with SPD lean. Stealth/utility verbs (stab, dodge, ambush, steal). Passive: evasion chance.

Each class gets 4-5 unique abilities, unlocked at specific levels. No cross-training in v1 — three clean classes, no bleed. Keeps identity sharp and implementation simple.

### Character Creation

Two messages, one choice, under 30 seconds:
- Server: pick class
- Player: single letter
- Server: welcome message with starting stats, type LOOK to begin

No stat allocation, no background selection, no appearance customization at creation. Name plus class. Everything else emerges through play.

### Levels: Ten

Logarithmic XP curve over 30 days:
- Levels 1-4: achievable in first 5 days
- Levels 5-7: days 6-14
- Levels 8-10: days 15-30 (back half of epoch, overlaps with Breach and endgame)

Each level grants HP increase, stat points to allocate, and potentially a new ability unlock. Front-loaded so players feel powerful in their first session.

### Gear: Three Slots

- **Weapon** — primarily affects POW
- **Armor** — primarily affects DEF
- **Trinket** — wildcard (any stat, passive ability, or utility)

The trinket slot is what elevates the system from LORD's linear upgrade path to Brogue's meaningful choice space. Items have qualitative differences, not just bigger numbers — a dagger that attacks twice vs a hammer that stuns, leather that allows stealth vs chain that reflects damage.

Six item tiers matching dungeon depth progression. Tier 6 is endgame loot drops only, not sold in shops. Items found as loot or bought from merchants. 8-slot backpack for unequipped items.

---

## The Barkeep — Hub NPC

The barkeep is the social center of town. Not a slot machine, not a daily attendance check. Visiting the barkeep is always free — no action cost, no token cost for the basics. Three functions:

### Recap ("While you were gone...")

Summarizes broadcasts the player missed since their last session. Always free. This is the LORD Daily Happenings log adapted for async mesh play. You've been offline two days, you walk into the inn, and the barkeep tells you what happened — who died, who found what, what bounties completed, what the frontline looks like now.

This is the primary reason to visit. Makes the world feel alive even when you weren't there.

### Bard Tokens

Players passively earn 1 bard token per real-world day, whether they log in or not. Cap at 5. Spent at the barkeep for rewards that gold can't buy — information, temporary power, and discovery access. Players can spend daily for steady small advantages or save up for bigger payoffs.

Token menu:
- **1 token: Hint** — discovery pointer, monster weakness, bounty tip, or trap warning for current floor
- **1 token: Temporary buff** — choose +2 POW, +2 DEF, or +2 SPD for 5 combat rounds
- **2 tokens: Floor reveal** — reveals all traps and ambush monsters on your current floor for the session
- **2 tokens: Bonus dungeon action** — one extra action added to today's budget
- **3 tokens: Free consumable** — random item from current-tier pool (heal potion, smoke bomb, or something rarer)
- **5 tokens (full cap): Rare item intel** — the barkeep tells you exactly which room on which floor has an unclaimed stash or gear drop

Design principles:
- Nothing on the list replaces what gold already buys. The healer sells healing for gold. The shop sells gear for gold. Tokens buy things gold can't — information, temporary buffs, discovery access.
- Tokens never feel redundant at any point in the epoch. A hint is valuable on day 1 and day 30 for different reasons.
- The save-vs-spend decision is itself meaningful. Daily spender gets 30 small advantages across the epoch. Saver gets 5-6 big payoffs. Different rhythm, comparable total value.
- The cap at 5 means the save path maxes out at 5 days — no infinite hoarding, but enough to reward patience.
- Natural epoch arc: early days spend on hints about floor 1-2 basics. Mid-epoch save for floor reveals to push efficiently. Late epoch burn the full 5 on rare item intel to gear up for endgame.

### Bounty Board

Daily objectives posted by the barkeep. 1-2 active at a time. These are **per-world, not per-character** — everyone sees the same bounties, creating shared goals.

Bounties rotate from a pool of ~40 generated at epoch start, phased to match the 30-day progression arc:
- **Days 1-10** (~15 bounties): Floor 1-2 targets. Low HP pools, soloable. Gets new players into the system.
- **Days 11-20** (~15 bounties): Floor 2-3 targets. Higher HP, multi-player coordination rewarded. Breach-adjacent bounties appear after day 15.
- **Days 21-30** (~10 bounties): Floor 3-4 targets. Endgame difficulty, big HP pools, require group effort. These are the glory kills.

A dedicated solo player completes ~20-25 bounties across the epoch. The pool never runs dry — there's always something to hunt.

---

## Bounty System — Async Cooperation

Bounties are the primary multiplayer mechanic. They work entirely asynchronously through shared state.

### Shared Progress Pools

Kill bounties use shared HP pools. "The Bounty Troll has 200 HP and lives on Floor 3." Every player who fights it chips away at the same HP total. Alice does 40 damage Monday morning, Bob does 60 Monday night, Carol finishes it off Tuesday. All three contributed.

Other bounty types use the same shared-counter model:
- "Explore 10 rooms on Floor 2" — any player visiting an unvisited room ticks the counter
- "Deliver 500g to the war chest" — communal gold deposit pool
- "Slay 8 orcs across all players" — shared kill counter

Progress is visible at the barkeep: "Bounty Troll: 120/200 HP remaining."

### Bounty Monster Regen

Bounty targets partially regenerate between fights to prevent trivial poke-and-flee strategies. **5% of max HP per 8 hours.**

This means on a 200 HP bounty troll:
- Recovers 10 HP per 8 hours, ~30 HP per day
- A single mid-level player deals ~40-60 damage per session — can outpace regen solo but it takes multiple days
- Two players make progress comfortably
- Three players push through fast

Cooperation is rewarded but not mandatory. The action economy is the real balancing lever — each engagement costs 4-5 dungeon actions out of 12 daily.

### Chip-and-Run Combat

Players engage the bounty monster, trade rounds, and flee when health gets low. The flee mechanic carries real risk (speed-based chance, eat a hit on failure). The troll's damage persists in the DB between encounters.

The tactical decision: "Do I push one more round or run now?" You're gambling HP and carried gold (death penalty) for bounty contribution. Each engagement costs dungeon actions from your daily budget (~4-5 actions per attempt out of 12 dungeon actions per day).

### Rewards

**Threshold model** — anyone who contributed at least one hit gets the full bounty reward when it completes. On a 5-30 player network, you want people hitting the troll when they see it and feeling good about helping, not doing contribution math.

First player to land the killing blow gets a broadcast and a small bonus on top. Everyone else who participated gets the base reward. Prevents one fast player from locking out the server while giving the finisher a moment of glory.

### One-and-Done (with Hold the Line Exception)

Bounties are single-completion by default. Once the bounty troll dies, it's done for the epoch. A weaker regular version of the monster spawns in its place — normal loot, normal XP, no bounty rewards. The room stays relevant for farming but the special challenge is gone. Creates a "you had to be there" feel for contributors.

**Hold the Line exception:** Bounty monsters inside Hold the Line rooms can regenerate twice (three total lives). Each kill counts as bounty progress AND contributes to clearing the room. This makes bounty targets high-value rooms to prioritize — a small group that focuses on bounty rooms gets triple the clearing efficiency from one target. After the second regeneration and third kill, the bounty is fully dead and replaced with a weaker regular version like normal.

### Broadcast Integration

Bounty progress creates natural broadcast content:
- Halfway point: "🎯 Bounty Troll: 100/200HP. Keep pushing."
- Completion: "🎯 Carol finished the Bounty Troll! Contributors: Alice, Bob, Carol."
- New bounty: "🎯 New bounty: Clear the Spider Nest on Floor 2 (0/6 spiders)."

These are tier 2 broadcasts that make the server feel like a team without requiring any real-time coordination.

---

## Economy

### Currency: Gold Only

Single currency. Earned from combat, spent at shops and NPCs. The 30-day wipe is the ultimate gold sink — no inflation is possible when everything resets.

No bank interest. The bank is a vault — safe storage that survives death. The carry-vs-bank tradeoff around the death penalty is the entire banking mechanic: carry gold into the dungeon (risk losing 100% on death) or bank it before you go (safe but unavailable mid-run).

### Shop Pricing

Exponential scaling matching LORD's model. A dedicated player can afford one equipment tier upgrade every 2-3 days:
- Tier 1: ~50-80g (Day 1)
- Tier 2: ~200-300g (Day 2-3)
- Tier 3: ~800-1000g (Day 4-5)
- Tier 4: ~3000-3500g (Day 6-8)
- Tier 5: ~10000-15000g (Day 9-11)
- Tier 6: Loot drops only, not sold (Day 12-14)

Buy-only shops (DCSS model). No selling to shops for gold — eliminates the "hoover the dungeon for sell-fodder" grind. Items can be sold back at 50% value for upgrade decisions (selling current sword to afford the next tier) but this is a one-way commitment.

### Player-to-Player Economy

No formal trading system — 175-char constraint makes negotiation impractical. Indirect economic interaction through:
- Bounty system: communal gold deposits, shared progress rewards
- Information as currency: first discoverer of a secret gets named credit, map knowledge shared via player messages
- Cooperative contributions: bounty participation, discovery activation, Hold the Line room clearing

---

## Combat

### Turn-Based Auto-Resolution

Discworld MUD's configured auto-combat model. Players choose an action (attack, ability, flee), combat resolves in rounds with compressed output. Depth from monster behaviors, not input speed.

### Monster Behaviors: Six

- **AUTO** — basic melee attack (rats, skeletons)
- **FLEE** — retreats at low HP, creating chase gameplay (thieves, wolves)
- **CALL** — summons nearby allies at low HP (orc warlords, hive creatures)
- **CAST** — uses one special ability: poison, curse, heal-self, area damage (shamans, wraiths)
- **STEAL** — takes gold or item, then flees (monkeys, nymphs)
- **AMBUSH** — hidden until player enters, guaranteed first strike (lurkers, mimics)

Monsters create problems, not just absorb damage. A Wraith draining XP demands different tactics than a Troll regenerating HP.

### Monster Roster: Twenty + Boss

4 monsters per tier across 5 tiers. Three monster families of 4-5 members plus standalone creatures. New abilities appear at deeper levels, not just bigger numbers. Tier 1 only AUTO-attacks. Tier 2 introduces FLEE and CALL. Tier 5 features CAST + AMBUSH combinations.

### Boss: Wyrm of Depths

Multi-phase fight. Changes behavior at HP thresholds:
- 75% HP: starts calling minions
- 50% HP: starts AoE attacks
- 25% HP: flees deeper, player must pursue

Guards the Crown on Floor 4.

### Combat Message Format

Two-message pattern per round:
1. Narrative: "Your blade bites deep! Orc staggers. -8HP. It snarls and CALLS backup!"
2. Status: "HP:38/60 vs Orc(42/50) A)tk F)lee bash(2) drain"

Each under 175 chars. Templates with variable slots — deterministic resolution, narrative flavor.

---

## Death & Consequences

### Standard Death

- Lose 100% of carried gold (not banked)
- Lose 15% of current XP
- Keep equipped weapon, armor, trinket
- Keep level, skills, banked gold
- Lose all carried consumables
- Lose 1 daily action (not all remaining)
- Respawn at nearest campfire, one floor closer to surface
- Revive at 50% HP
- Clear all status effects

### Corpse Marker (Dark Souls Bloodstain)

A marker persists at the death location for 24 hours. Return to recover 50% of lost gold. Die again before reaching it and the marker vanishes permanently. Creates signature tension: rush back for gold (risky) or write it off (safe).

### Insurance Mechanics

- Soul Anchor (rare consumable, max 1 carried): prevents XP loss on death, consumed on use
- Death Defiance tokens (earned from quests/bounties, max 2): auto-revive at 25% HP

### Hardcore Mode

Opt-in at character creation. Separate leaderboard. Permadeath — character is gone. 10% XP bonus as compensation. Surprisingly well-suited for 30-day wipes since meta-loss is bounded. Natural dramatic escalation: dying on day 3 is a shrug, dying on day 25 is devastating.

### Scaling Tension

Percentage-based XP penalty creates natural escalation. Day 2: 15% of 200 XP = 30 XP lost (trivial). Day 12: 15% of 50,000 XP = 7,500 XP lost (potentially a full day setback). Death gets scarier as stakes rise without any special rules.

---

## Daily Session Structure

### Action Budget

Town actions are always free — shopping, healing, banking, training, visiting the barkeep. Nothing outside the dungeon or purchasing should cost actions. The scarcity is on the dangerous stuff, not the bookkeeping.

Costed actions:
- **12 dungeon actions** — combat, movement, exploration (the real budget)
- **2 social actions** — sending mail, posting to board
- **1 special action** — reserved for future mechanics

At ~45-60 seconds per action over mesh radio, produces 11-20 minute sessions. Over 30 days: ~450 dungeon actions total.

### Free Actions

Don't consume any budget: look, stats, inventory, help, who, map, leaderboard, timeleft, classes, reading mail/board, ALL town NPC interactions (shop, heal, bank, train, barkeep, sage). Information and town logistics are always free.

### Unused Actions Don't Carry Over

You can't hoard. Daily engagement is rewarded naturally through having more sessions (more fights, more progression) but the action budget itself resets clean. Combined with bard tokens accumulating passively, this means missing a day costs you a day's actions (natural consequence) but doesn't compound punishment.

### Bonus Actions

Bard tokens can grant 1 bonus dungeon action (for 2 tokens). This is the only way to exceed the base 12 dungeon actions in a day. Kept deliberately moderate — the token system is a care package, not a power multiplier.

---

## Dungeon

### The Darkcragg Depths

The dungeon is always the Darkcragg Depths. Like the Last Ember, the name is a constant — it persists across every epoch, every server, every wipe. The floors reskin, the layout regenerates, the monsters change, but the Depths are always the Depths. Players descend into the Darkcragg. They talk about the Darkcragg. It's a proper noun, not a generic dungeon.

The four floors are narratively re-skinned each epoch (Sunken Halls, Fungal Depths, Ember Caverns, Void Reach are defaults — the LLM pipeline may rename them) but the Darkcragg Depths is the name on the door every time.

### Hub-Spoke Layout with Loops

Each floor has a central hub room connecting to 3-4 branching paths of 3-5 rooms each, with 1-2 loops connecting branches. Hubs are mental anchors — players always know where they are relative to center.

4 floors, 15-20 rooms each, ~60-80 total rooms. Small enough to mentally map over 30 days, large enough for meaningful exploration.

### Floor Themes

Changing five elements per floor creates distinct feel:
- Floor 1: Sunken Halls — stone, damp, rats and goblins
- Floor 2: Fungal Depths — glowing mushrooms, spores, strange creatures
- Floor 3: Ember Caverns — lava, obsidian, fire drakes and golems
- Floor 4: Void Reach — lightless, crystal, liches and the Wyrm

### Vault Rooms

3-5 handcrafted rooms per floor injected into the procedural layout. Treasure room with guardian, puzzle room with riddle gate, boss chamber, shrine, hidden cache. Selected from a library of 20-30 vault templates, randomized per epoch.

### Room Descriptions

175-char template: `{Name}. {One sensory detail with active verb}. {Threat/item hint}. [{Exits}]`

First visit gets full description. Revisits get abbreviated: name + threats + exits.

Every room gets a unique, memorable name as a navigation landmark. No "all alike" rooms.

### Traps: Visible

Every trap is visible (DCSS v0.27 philosophy). Players choose: step over (risky stat check) or find alternate route (safe, costs actions). Three categories:
- Physical obstacles (spike pit, tripwire)
- Status traps (poison darts, paralysis gas)
- Environmental hazards (flooding, cave-in)

1-2 traps per floor, guarding optional vault rooms for risk-reward tension.

### Riddles

Riddle gates on vault doors. LLM generates riddle text around a mechanically verified answer word at epoch start. Wrong answer costs 5 HP. Resets each wipe cycle.

---

## Endgame: Three Rotating Modes

Three endgame modes, selected by player vote each cycle. The winning mode is narratively re-skinned each epoch — different names, descriptions, and story framing generated at world creation. This prevents staleness across epochs and serves different player archetypes.

### Mode Selection: Player Vote

On the final day of each epoch (day 30), the barkeep posts a ballot. Players vote for next epoch's mode as a free action.

Rules:
- **Votes are public.** "Alice voted Hold the Line" broadcasts to the channel. Creates social momentum and negotiation. Secret ballots are for governments, not games.
- **Votes can be changed** right up until the epoch ends. This lets deals happen. "I'll vote Hold the Line if you promise to help push floor 3."
- **No quorum.** If only 1 person votes, they pick the mode. Reward for being engaged. On a mesh network you might only have 2-3 active players some cycles.
- **Tiebreak: longest unplayed.** If votes tie, the mode that hasn't been played the longest wins. Guarantees every mode eventually gets its turn even on a server that heavily favors one style.
- **Fallback:** If nobody votes at all (dead server, everyone offline), longest-unplayed mode is selected automatically.

The vote itself becomes a multiplayer interaction. People argue on the broadcast channel about what they want. That's engagement before a single monster spawns.

### Mode 1: Retrieve and Escape (Cooperative Relay)

The server works together to extract an objective from floor 4 to the surface. One player carries the objective at a time, but the whole community can prepare the escape route, block the Pursuer, and support the carrier.

**Claiming the objective:**
- Defeat a guardian on floor 4 to claim the objective (anyone can attempt this)
- Once claimed: monster spawn rates double on all floors, invulnerable Pursuer spawns 3 rooms behind the carrier, carrier's stealth disabled

**The Pursuer — movement model:**
The Pursuer advances through the async action economy, not real-time. Every 2 actions the carrier takes, the Pursuer advances 1 room toward them. The carrier's own actions are the clock.
- Through clear rooms, the carrier outpaces the Pursuer at a 2:1 ratio (1 move action = 1 room gained, Pursuer advances half a room)
- Through hostile rooms, combat costs 3-5 actions per room — the Pursuer closes 1-2 rooms during each fight
- A fully cleared path means the Pursuer never catches up. A half-hostile path turns the ratio toward 1:1. An unprepared path is a death sentence.

**When the Pursuer catches the carrier:**
Not instant death — the Pursuer forces a fight the carrier cannot win. The carrier's only option is to flee (normal SPD-based chance, costs 1 action per attempt). A successful flee puts the carrier 1 room ahead and the chase continues. A failed flee means heavy damage and another attempt next action. Getting caught is a tax of 1-3 actions plus significant HP. A healthy carrier survives being caught once or twice. A carrier already battered from fighting through hostile rooms probably dies — and the relay begins.

**Relay mechanics:**
If the carrier dies, the objective drops at their death location and the server is notified. Any player can pick it up and continue the run. When the objective changes hands, the Pursuer resets to 5 rooms behind the new carrier — enough breathing room to move, but not enough to make intentional sacrifice relays free. Deliberate death-relays cost the dying player their death penalty (gold loss, XP loss) for 5 rooms of buffer.

**Three support roles for non-carriers:**

**Blockers — tank the Pursuer directly.** A non-carrier standing in a room between the Pursuer and the carrier becomes a blocker. The Pursuer must fight through them before advancing. It's still invulnerable — the blocker can't kill it — but every round the blocker survives is a round the Pursuer isn't moving. A DEF-tanked player might hold 4-5 rounds. A SPD build holds 2 rounds but has a better flee chance afterward. The blocker is buying distance for the carrier with their HP. Possibly their life.

**Warders — harden the escape path.** A non-carrier who clears a room on the escape route can spend 1 extra action to ward it. A warded room slows the Pursuer — it takes 2 ticks to pass through instead of 1. The ward breaks after one use. Clearing + warding costs 3-4 actions per room out of 12 daily. A dedicated player preps 3 rooms per day. Over several days before the run, the team can build a serious escape corridor. This is the preparation role — done across multiple sessions before anyone grabs the objective.

**Lures — divert the Pursuer off-path.** A non-carrier on the same floor as the Pursuer can spend 2 actions to lure it. The Pursuer diverts toward the lure player for 3 ticks before snapping back to tracking the carrier. Ideal play: lure it down a dead-end branch. The Pursuer burns 3 ticks going the wrong way, then 3 more returning — 6 ticks of breathing room. But the lure player is now in a dead end with an invulnerable monster between them and the exit. A SPD build can lure and escape. A slow tank might get trapped and die.

Each support role rewards a different build and time investment. Warders work across sessions before the run. Blockers and lures work during the live chase. All async-compatible — no two players need to be online simultaneously.

**Win condition:** Any player delivers the objective to the surface. Everyone who participated (fought the guardian, cleared or warded escape route rooms, blocked or lured the Pursuer, carried the objective) gets epoch win credit.

**Broadcast behavior during a run:**
- "👑 Alice claimed the Crown of the Depths! The Pursuer stirs."
- "👁 The Pursuer is 8 rooms behind the carrier."
- "👁 The Pursuer is 3 rooms behind. It's closing."
- "🛡 Mira is blocking the Pursuer on Floor 2! The carrier gains ground."
- "💀 Mira fell holding the line. The Pursuer advances."
- "🎯 Kael lured the Pursuer into the Bone Pit! It diverts from the carrier."
- "👁 The Pursuer has reacquired the carrier. The chase resumes."
- "⚠ The Pursuer has reached the carrier!"
- "💀 The carrier has fallen on Floor 2. The objective lies unguarded."
- "👑 Bob picks up the Crown! The Pursuer resets. The relay continues."
- "🏆 The Crown has reached the surface! Victory belongs to the server."

The relay mechanic is the key — a solo player probably can't make it from floor 4 to surface against doubled spawns and a Pursuer. But if Alice clears and wards floor 3 over two days, Bob grabs the objective and carries it through floors 4-3, Mira blocks the Pursuer on floor 2 and dies buying time, then Carol picks up the dropped objective and sprints a clear floor 1 to the surface. Four players, four sessions, one victory.

Narrative skins: Crown of the Depths, rescue the prisoner, recover the artifact, steal the war plans, carry the flame to the surface.

### Mode 2: Raid Boss (Cooperative)

A massive enemy spawns on floor 3-4 with a shared HP pool in the thousands. The entire server chips away over multiple days. Server-wide victory when it dies. The boss rolls random mechanics at epoch generation, and players must figure out what it does through scouting engagements before they can fight it efficiently.

**HP Scaling:**
Base HP = 300 × active players at epoch start, capped at 6000. Active player = anyone who has entered the dungeon at least once in the first 3 days of the epoch.
- 5 players → 1500 HP (regen: 135/day)
- 10 players → 3000 HP (regen: 270/day)
- 15 players → 4500 HP (regen: 405/day)
- 20+ players → 6000 HP cap (regen: 540/day)

Boss regenerates at 3% per 8 hours (~9% per day). Lower than bounty regen (5%/8h) because the absolute HP numbers are so much higher — at 3000 HP, that's 270 HP/day, which outpaces a single player's early-epoch damage output. The rate creates natural pacing: days 1-10 players can't outpace regen, days 10-15 mid-level builds cross the damage threshold and net progress begins, days 15-25 strong builds plus stacked discovery buffs accelerate the kill. No artificial unlock gate needed — regen *is* the gate.

**Boss Mechanic Table (roll 2-3 at epoch generation):**

Players discover mechanics through trial and error. The first 2-3 days of engagements are scouting runs. Knowledge sharing via player messages becomes critical: `msg boss 3rd hit dodge` or `msg boss flees at 50pct`.

*Offensive mechanics:*
- **Wind-up strike** — every 3rd combat round, the boss telegraphs a massive hit. Player must use a defend or dodge action on the next round or take triple damage. Rewards round counting.
- **Flat damage boost** — boss hits harder than its level suggests. Changes the math on safe engagement length.
- **Retribution** — at each HP threshold (75%, 50%, 25%), the boss unleashes a burst that hits the current fighter for massive damage. You want full HP when pushing it across a threshold.
- **Aura damage** — every round of combat deals small unavoidable damage regardless of DEF. Limits engagement length for all builds.

*Defensive mechanics:*
- **Extra regen** — heals at 5%/8h instead of 3%/8h. Harder DPS check. Forces more contributors or discovery buff stacking.
- **Armor phase** — takes half damage until a condition is met (a discovery secret on the floor, a certain number of unique players have hit it, or a stat check is passed during combat).
- **Boss flees** — at certain HP thresholds, the boss relocates to a random room on the same floor. Players have to find it again before reengaging. Exploration players become valuable for scouting.
- **Regeneration burst** — once per day, the boss heals 15% of max HP in one tick instead of spread regen. Creates an optimal timing window — figure out *when* the burst happens and push damage right after.

*Control mechanics:*
- **No escape** — below 25% HP, flee attempts automatically fail. Committed fight — kill it or it kills you. Makes the final stretch terrifying.
- **Summoner** — spawns 1-2 adds at the start of each engagement that must be killed before the boss can be damaged. Costs 2-4 extra actions per fight — a significant tax out of 12 daily.
- **Lockout** — after engaging the boss, that player can't engage again for 24 hours. Forces the group to rotate fighters. A 5-player server needs all 5 contributing.
- **Enrage timer** — if a single engagement goes longer than 5 rounds, the boss's damage doubles each round after. Punishes greedy players who try to squeeze too much damage per session.

**Phases (always present, not rolled):**

Every raid boss has 3 phases: 100-66%, 66-33%, 33-0%. At each threshold, rolled mechanics intensify or new behaviors activate. A boss with Wind-up + Summoner might summon 1 add in phase 1, 2 adds in phase 2, and the wind-up goes from every 3rd round to every 2nd in phase 3. The combination and escalation make each raid boss unique.

**Rewards:**
- Everyone who contributed at least one hit gets the full reward when it dies
- Killing blow gets broadcast glory and a bonus
- Phase thresholds get broadcast: "🐉 The Wyrm enters its second phase! Its wounds glow with fury."

Narrative skins: dragon, bandit warlord, ancient golem, plague beast, war machine, elder lich. The LLM skins rolled mechanics to match — a boss with Flee + Wind-up might be "a shadow drake that vanishes into the dark and strikes from ambush." Armor Phase + No Escape becomes "an ancient golem that hardens as it weakens, trapping challengers in its crumbling chamber."

### Mode 3: Hold the Line (Cooperative Territory Control)

The dungeon fights back. Players collectively push a front line deeper while the dungeon regenerates lost ground. Checkpoints lock in progress permanently.

Core mechanics:
- All dungeon rooms start hostile (monster-occupied)
- Players clear rooms by killing all monsters in them — cleared rooms become safe territory
- The dungeon regenerates on a spread schedule — rooms revert to hostile throughout the day, not in one big tick. No optimal login time to exploit.
- **Checkpoints** are fixed rooms on each floor (hub, midpoint, far end — 3-4 per floor). To establish a checkpoint, players must clear the checkpoint room plus its adjacent rooms
- Once a checkpoint is established, it becomes a permanent wall — dungeon regen cannot push past it
- Progress ratchets: you can lose ground between checkpoints, but never behind one
- Each floor's final checkpoint unlocks access to the next floor down
- The whole server is collectively descending — epoch win condition is establishing the final checkpoint on floor 4

### Regen Rate — Scaled by Floor (Tuned for 30-Day Epochs)

Regen scales with depth so early floors are forgiving and deep floors demand coordination. Ticks are spread evenly across the day (e.g., floor 2 at 4 rooms/day = 1 room every 6 hours). Rates bumped from the 14-day baseline to maintain tension across the full 30-day epoch — 2 consistent players should finish around day 20-25, not coast to victory by day 14.

Based on: 17 rooms avg per floor, 12 dungeon actions per day, clearing a room costs 2-3 actions (move in + fight), so one player clears ~4-6 rooms per day.

- **Floor 1: 3 rooms revert per day.** Solo player gains 1-3 rooms/day. Manageable but no longer trivial. A new player can still make visible progress on day 1, but holding ground requires attention by day 3-4.
- **Floor 2: 5 rooms revert per day.** Solo player loses ground. Two players gain slowly (~3-5 net rooms/day). The first real coordination gate.
- **Floor 3: 7 rooms revert per day.** Needs 2-3 consistent players to make headway. Solo players contribute by holding a cleared section, not pushing.
- **Floor 4: 9 rooms revert per day.** Serious coordination required. Even 3 players are in a fight. This is the final push and it should feel like one.

Projected timeline:
- Solo player: can clear floor 1 in ~6-8 days, stalls hard on floor 2. Valuable as a floor holder.
- 2 consistent players: ~4 days on F1, ~5 days on F2, ~6 days on F3, ~8 days on F4. Total ~23 days — completable but tight. The Breach on day 15 hits right when they're slogging through floor 3.
- 3 players: ~3 days per floor on F1-F3, ~5 days on F4. Complete by day 14-16. Rest of epoch for Breach, secrets, bounties.
- 5+ players: floors fall fast, done by day 8-10. The challenge becomes mop-up and optimization.

### Floor Scaling Creates Roles for Everyone

A late-joining player on day 8 can meaningfully contribute by maintaining floors 1-2 (holding cleared ground against regen) while veterans push floors 3-4. A casual player who logs in twice a week can clear 4-6 rooms per session on a lower floor, preventing backslide. Nobody's contribution is wasted even if they never touch the deep floors.

Checkpoint difficulty scales by floor:
- Floor 1: clear the checkpoint room cluster (easy, tutorial)
- Floor 2: clear checkpoint room + all adjacent rooms within one regen window
- Floor 3: clear cluster within regen window, tougher monsters
- Floor 4: clear all rooms on the floor within regen windows, then fight the Warden

**Checkpoint establishment rule:** Clear all rooms in a checkpoint cluster before any of them revert. On floor 2 at 5 rooms/day (one room every ~5 hours), a 3-room cluster must be cleared in one session before the first tick. That's 6-9 actions out of 12 — tight solo, comfortable for two. No simultaneous hold, no patrolling — just clear the cluster within one regen window.

**Floor bosses:** Each floor's final checkpoint (the stairway down) spawns a floor boss once the cluster is cleared. Kill the boss, checkpoint locks permanently, next floor opens. The boss is a separate fight with a shared HP pool — it doesn't care about room state. Once spawned, it stays spawned until dead. Chip-and-run across multiple sessions.

### Floor Boss Mechanics — Randomly Rolled Per Epoch

At epoch generation, each floor boss rolls one mechanic from a table (floor 4 rolls two). Same philosophy as the Breach — you know the structure but the specifics are a surprise. The LLM pipeline skins each mechanic with flavor text: the Regenerator might be "a troll whose wounds knit shut between battles" one epoch and "a crystal golem that slowly reforms" the next.

**Floor 1 boss table (roll 1 — all straightforward, teaches chip-and-run):**
- **Armored** — takes half damage until 50% HP, then normal
- **Enraged** — deals double damage below 50% HP but takes 25% more
- **Regenerator** — heals 10% between sessions (higher than normal bounty regen)
- **Stalwart** — immune to flee on first attempt (always costs 2 tries to disengage)

A level 3-4 solo player kills the floor 1 boss in 2-3 sessions regardless of roll.

**Floor 2 boss table (roll 1 — introduces conditions):**
- **Warded** — has a defensive buff that a discovery secret on the floor disables. Rewards explorers.
- **Phasing** — alternates between vulnerable/invulnerable each day. Can only take damage every other session.
- **Draining** — steals HP on hit. Players leave the fight with less HP than they entered even with good play.
- **Splitting** — at 50% HP, splits into two half-HP targets in adjacent rooms. Both must die.

**Floor 3 boss table (roll 1 — punishes solo play):**
- **Rotating resistance** — immune to the highest stat used against it each session. POW build hits it today, it's POW-immune tomorrow. Forces class diversity.
- **Retaliator** — reflects a percentage of damage back. High-damage builds hurt themselves. Rewards DEF tanks.
- **Summoner** — spawns an add each session that must be killed first. The add is easy but costs 2-3 actions, eating into boss damage budget.
- **Cursed** — debuffs the player who dealt the most damage last session. They get -2 to a random stat next login. Spreads the load across the group.

**Floor 4 boss table (roll 2 — the Warden, the big fight):**
Rolls two mechanics from the combined pool of all tables above. The combinations create unique puzzles every epoch. Some combos are brutal (Draining + Retaliator), some are manageable (Armored + Stalwart). The Warden has a shared HP pool of 300-500 HP and regenerates at 3% per 8h. Multi-session, multi-player fight. Variable difficulty across epochs is intentional — some Wardens become part of the server's story.

The Warden kill establishes the final checkpoint and wins the epoch.

Daily experience: log in, barkeep tells you "overnight floor 2 lost 2 rooms, frontline at room 9, but Checkpoint Alpha held." Your session is spent pushing the front line back and trying to establish the next checkpoint before the next regen tick.

Narrative skins: holding the line against darkness, reclaiming the lost mines, purging the infection, pushing back the flood, clearing the infestation.

### Bounties During Hold the Line

Bounties run alongside Hold the Line as normal, with bounty monsters placed inside dungeon rooms. These targets regenerate twice (three total lives) instead of the standard one-and-done. Each kill contributes to both bounty completion and room clearing, making bounty rooms high-priority targets. A two-player team that coordinates around bounty rooms gets significantly more clearing done per action spent — the bounty reward is a bonus on top of the territory control progress. This is the primary scaling mechanic that lets smaller groups punch above their weight.

Broadcasts for this mode:
- "🏰 Floor 2 Checkpoint Alpha established! The darkness cannot pass."
- "⚠ Floor 2 lost 2 rooms. Frontline at Room 9. Next regen in ~8h."
- "🏰 Floor 3 unlocked! The descent continues."
- "💀 Floor 2 frontline collapsed to Checkpoint Alpha. Rally!"

### Shared Across All Modes

Parallel scoring exists regardless of mode:
- Endgame objective completion (mode-specific)
- Total rooms explored (cartographer)
- Bounty completions (team player)
- Total monsters slain (slayer)
- Secrets discovered (investigator)
- Server-wide discoveries activated (benefactor)
- Escape route rooms cleared / relay participation (mode 1 specific)

### Post-Epoch

Winners (or the whole server for cooperative modes) get epoch win credit. The real endgame is the cross-epoch leaderboard — total epoch wins, cumulative score, longest hardcore streak. Each cycle's results are immortalized in a persistent Hall of Fame.

---

## Discovery Layer — Investigation Across All Modes

Investigation isn't a mode — it's a layer that runs across all three endgame modes. Every epoch generates a set of discoverable secrets scattered throughout the dungeon. Finding them is optional but they provide real, mechanical advantages. The explorer/thinker player type always has something meaningful to do regardless of which endgame mode is active.

### Individual Discoveries

Secrets that benefit the player who finds them:

- **Hidden shortcuts** — a concealed passage connecting two floors that bypasses 3-4 rooms of monsters. Found by examining suspicious room features or solving environmental puzzles. Permanent once discovered (for that player only — they can share the knowledge via player messages or mail).
- **Stashes** — consumables, gear, or gold hidden behind riddle doors or in obscure rooms. One-time loot, first-come-first-served.
- **Trapped NPCs** — a merchant, healer, or sage imprisoned on a dungeon floor. Free them and they set up shop there for the rest of the epoch, saving everyone the trek back to town. The discoverer gets a reward and a broadcast.
- **Monster intelligence** — inscriptions, journals, or environmental clues that reveal specific monster weaknesses. "The trolls recoil from fire" gives concrete tactical info for anyone who finds it.

### Server-Wide Discoveries

Secrets that benefit every player on the server when activated. These are the big moments — the explorer spending their 12 dungeon actions on side rooms instead of the front line is contributing in a way that helps everyone.

**Hold the Line mode:**
- **Ancient Wards** — activate a mechanism that halves dungeon regen rate on one floor for 24 hours. Floor 3 drops from 4 rooms/day to 2, giving fighters a real window to push.
- **Signal Beacons** — light a beacon on a floor that reveals all ambush monsters for everyone for 48 hours. No more surprise first strikes on that floor.

**Raid Boss mode:**
- **Weakness rituals** — find and complete a ritual that permanently strips part of the raid boss's armor (DEF reduction). The boss becomes slightly easier for everyone.
- **Ancient weapons** — discover a room that unlocks a communal armory where any player can grab one free item per day for the rest of the epoch.

**Retrieve and Escape mode:**
- **Pursuer traps** — set traps along the escape route that slow the invulnerable Pursuer for the objective carrier. Benefits whoever makes the escape attempt.
- **Safe caches** — hidden rooms along the ascent path where the carrier can briefly rest and heal. Found by explorers, usable by whoever carries the objective.

**All modes:**
- **Floor maps** — discover a cartographer's notes that reveal every room on a floor for all players. Rooms show as explored even if the player hasn't visited.
- **Blessing shrines** — activate a shrine that grants all players a small passive buff for 24 hours (+1 to a random stat, slight XP bonus, etc.)

**Buff duration and stacking:** All timed discovery buffs last 24 hours. All buffs stack with no cap. The async constraint is the natural limiter — triggering two secrets in the same session burns most of a player's 12 daily actions on setup instead of progression. The intended play pattern: scout buff locations over several days, then activate 2-3 in sequence to create a power window for one big push — take down a bounty you're underleveled for, or blitz a floor that's been walling you. The 30-day epoch gives players enough breathing room to "spend" a session on setup without feeling wasteful. Three stacked buffs is the practical ceiling since it requires pre-scouting all locations and burning an entire session on activation.

### How Secrets Are Found

Secrets are placed during epoch world generation, tied to specific rooms and conditions:
- Examining (X command) suspicious features mentioned in room descriptions ("scratches on the wall," "a loose stone," "faint humming behind the door")
- Solving riddles at hidden doors
- Reaching dead-end rooms that seem empty but reward thorough exploration
- Completing specific sequences (visiting rooms in a particular order, carrying a specific item to a location)
- Multi-room puzzles requiring cross-room awareness and sometimes coordination (see below)

### Multi-Room Puzzles

2-3 of the 4 puzzle secrets per epoch are multi-room puzzles. The remaining 1-2 are single-room (rotate the statue, solve the riddle). Multi-room puzzles are the hardest discovery type to design because the player in room A can't see room B. Two systems solve this:

**Environmental pairing through shared descriptive elements.** Both rooms in a puzzle pair share a distinctive detail — same unusual material, same symbol, same sound. Room A: "Iron chains descend into darkness. A rusted lever bears the mark of a coiled serpent." Room B: "A stone slab blocks the eastern passage. A coiled serpent is carved into its face." The connection is implicit. A player who's been to both rooms and is paying attention thinks "serpent in both places, maybe they're linked." The lever doesn't say "opens door in room B." The door doesn't say "find the lever." The connection lives in the player's head — or in a player message. `msg serpent lever floor2` left at room B is the kind of thing that makes the message system sing.

**Targeted broadcasts for cross-room feedback.** When a player triggers one half of a multi-room puzzle, a targeted broadcast fires — not server-wide, only to players who have previously visited the affected room. "🔍 Something shifts in the Flooded Crypt." If you've been to the Flooded Crypt, you know to go check it. If you haven't, the message means nothing — no spoilers.

**Three puzzle archetypes:**

**Paired mechanism** — lever/door with shared symbol. Pull lever, targeted broadcast tells previous visitors the connected room changed. Door opens permanently once triggered. Simplest version, 1-2 per epoch. One player can solve it alone if they've explored both rooms and notice the symbol connection.

**Sequence lock** — 3 rooms each contain an interactable object (a bell, a basin, a brazier). Must be activated in the correct order. Wrong order resets the sequence. The correct order is hinted by a lore secret or barkeep hint ("first water, then fire, then sound"). This is where lore secrets and puzzle secrets overlap — finding the lore clue makes the sequence solvable in 1 attempt. Without the clue, it's trial and error across 6 possible orderings. One player can brute-force it solo in 6 attempts.

**Cooperative trigger** — two mechanisms that must be activated within the same regen window. Lever A and lever B are on different branches of the same floor. One player pulls A, another pulls B before the next regen tick resets A. This is the only puzzle type that genuinely requires two players — or one very fast player who can reach both levers in one session (maybe 4-5 actions apart if you know the route). Tight solo, trivial with coordination via mail.

**Signaling principle:** Every puzzle room must contain enough information for a player to know *something is here* even if they can't solve it alone. The lever is visible. The door is visible. The symbols match. The barkeep hints at sequences. No puzzle should require out-of-game knowledge or pure guessing. The answer is always in the dungeon — it's just distributed across rooms.

### Broadcast Integration

Discovery events are tier 1 or tier 2 broadcasts depending on impact:
- "🔍 Kael activated the Ancient Ward! Dungeon regen halved for 24h." (Tier 1 — affects everyone)
- "🔍 Mira freed a trapped healer on Floor 3! A healer now serves the depths." (Tier 1)
- "🔍 Bob found a hidden shortcut on Floor 2." (Tier 2 — individual benefit, but signals there's more to find)
- "🔍 A blessing shrine was activated. All players gain +1 DEF for 24h." (Tier 1)

### Barkeep Hint Integration

Bard token hints (1 token) can point players toward undiscovered secrets: "The sage mutters about hidden wards on floor 2" or "Rumors of a trapped merchant in the eastern passages of floor 3." The hint tells you something exists and roughly where, but not exactly how to find or activate it.

### Design Principles

- Every discovery must have a **concrete mechanical effect**, not just lore flavor. The explorer is doing real work.
- Server-wide discoveries get **broadcast credit** — everyone knows who did it. This is the explorer's version of "killed the raid boss."
- Secrets should be **findable through attentive play**, not random luck. Room descriptions hint at what's hidden. Players who read carefully are rewarded.
- Discovery information itself becomes social currency — telling another player where the shortcut is has real value.

### Secret Count and Types — 20 Per Epoch

A dedicated solo player should be able to find all 20 secrets just as the 30-day wipe is landing. A casual player finds 6-10 without trying. Two players sharing intel via messages and mail finish by day 20.

Five discovery methods create variety in *how* secrets are found, not just *where*:

**Observation secrets (6)** — examine a room feature hinted at in the description. "Scratches mark the south wall" → `examine south wall` → hidden cache or passage. These are the gimmes. Floor 1-2 have 4, floor 3-4 have 2. A new player finds their first one naturally within days.

**Puzzle secrets (4)** — environmental interactions. A lever in one room, a locked grate in another. A statue to rotate. A pool that reflects something useful if you have a torch. Cost 2-3 actions to solve once you understand the mechanism.

**Lore secrets (4)** — the sage or barkeep drops cryptic hints ("the founder's tomb faces sunrise"). Combine that with room exploration on the right floor. Rewards players who use NPC interactions and pay attention to flavor text. These are where player messages shine — `msg tomb faces east` left at the right room saves someone else 3 days of wandering.

**Stat-gated secrets (3)** — brute force a cracked wall (POW check), sprint through a collapsing corridor (SPD check), endure a toxic chamber (DEF check). High DCs that only late-game characters can reliably pass. Naturally gate to days 15-25 when players have the stats.

**Breach secrets (3)** — don't exist until the mid-epoch Breach event opens on day 15. See below.

### Discovery Tracking

`secrets` command shows "Secrets: 7/20 found." Broadcasts at milestones: 5, 10, 15, 20 found. "🔍 Kael has uncovered 10 secrets!" creates social pressure and hints that there's more to find.

**Rewards** — each secret gives a unique discovery: a lore fragment (collected set tells the epoch's story), a one-time permanent stat bump (+1 to a stat), rare consumables, or a shortcut that persists for the rest of the epoch. Finding all 20 earns a cross-epoch title and leaderboard entry.

### 30-Day Discovery Arc

- Days 1-10: Progression focus. Find 3-5 observation/puzzle secrets naturally during exploration.
- Days 10-15: Strong enough to start actively hunting. Pick up 4-6 lore and puzzle secrets.
- Day 15: Breach opens — 3 new Breach secrets become available.
- Days 15-22: Breach exploration + stat-gated secrets on floors 3-4.
- Days 22-30: Mopping up the last hard secrets. Solo completionist finishes right at the wire.

---

## The Breach — Mid-Epoch Event (Day 15)

Around the third weekend, when engagement naturally dips, the dungeon changes.

### The Setup

Barkeep starts dropping hints on day 12-13: "The walls are thin between the second and third depths. Something stirs." On day 15, a broadcast fires: "⚡ The ground splits. A new passage has opened between Floors 2 and 3. Strange light pours from within."

A new mini-zone opens — 5-8 rooms connecting floors 2 and 3, themed differently from both. Generated at epoch start but sealed until day 15. Contains 3 Breach secrets, a unique encounter, rare loot unavailable elsewhere, and a permanent shortcut between floors 2 and 3 for the rest of the epoch.

### Four Mini-Events (Random, Always a Surprise)

The Breach's physical space is the same, but the mechanic inside is randomly selected from 4 options — independent of the endgame mode. This means 12 possible Breach+Endgame combinations, keeping epochs feeling unique. The Breach type is never announced in advance.

**Mini-event 1: The Heist (mini Retrieve & Escape)**

An artifact sits in the deepest Breach room, guarded by a mini-boss. Grab it, carry it back to town. A pursuer spawns — slower than the endgame version, and the run is only 5-8 rooms. Soloable by a geared level 7+ player but tense. Relay mechanics apply — if the carrier dies, the artifact drops and broadcasts. The 3 Breach secrets are scattered along the escape route, found under pressure.

**Mini-event 2: The Emergence (mini Raid Boss)**

Something big crawls out of the Breach. Shared HP pool, 500-800 HP — completable in 3-5 days with 2-3 active players. Same chip-and-run mechanics as bounties but bigger. Sits in the central Breach room and doesn't move. Surrounding rooms spawn its minions on a timer. The 3 Breach secrets are in the minion rooms, discovered while contributing to the kill.

**Mini-event 3: The Incursion (mini Hold the Line)**

Monsters pour *out* of the Breach into floors 2-3. The Breach rooms start fully hostile and regenerate fast — 2 rooms revert per day in just 5-8 rooms. Players must push in, clear every room, and hold them all simultaneously for 48 hours. If any room reverts during the hold timer, the clock resets. The 3 Breach secrets are behind the hardest rooms, found as part of the push. Two coordinated players can complete it in 4-5 days.

**Mini-event 4: The Resonance (exploration/puzzle)**

No combat focus. The Breach is a puzzle dungeon. Rooms contain environmental riddles, sequence locks, items that interact with other rooms. A crystal that hums at a frequency, glass that vibrates in sympathy. One room has a lever, another has the door it opens. A pool reflects a clue for a riddle two rooms over. The 3 Breach secrets are the rewards for solving the puzzles, and finding all 3 unlocks a bonus cache in the deepest room. Soloable by nature — it's knowledge, not stats. Serves the explorer/thinker players who might feel underserved by combat-heavy modes.

### Breach Interaction with Endgame Modes

The Breach benefits the active endgame mode regardless of which mini-event runs:

- **Retrieve and Escape**: The Breach shortcut becomes an alternate escape route — shorter but riskier.
- **Raid Boss**: Breach completion drops a buff item that increases damage against the raid target.
- **Hold the Line**: Breach rooms count as bonus territory toward checkpoint progress on both floors 2 and 3.

### Design Rationale

The Breach exists to break the third-weekend lull. By day 15, early enthusiasm has faded, progression has slowed, and the endgame isn't yet in reach. The Breach injects new content, new rooms, new loot, and a self-contained challenge that mid-level players (level 5-7) can engage with immediately. Veterans who are already on floor 4 have a reason to come back up. New players who just reached floor 2 have something exciting ahead.

The random selection ensures players can't fully plan their epoch. They know the macro goal (endgame mode) from day 1, but the mid-epoch twist keeps them on their toes. Combined with the endgame vote, this gives 12 possible epoch configurations before even counting narrative re-skinning.

---

## Meta-Progression (Survives Wipes)

- Persistent player handle (account) with cross-epoch statistics
- Earned titles displayed in all interactions ("Kael the Twice-Risen")
- Total epochs played, epoch wins (per mode), lifetime kills
- Hall of Fame records for each completed epoch, including mode and results
- Future consideration: unlockable starting options (new classes, items, challenges)

---

## Multiplayer: All Async

### Player Messages (Dark Souls Soapstone)

Players leave short freeform messages in rooms for others to find. Attributed to the author — self-policing on small mesh networks where everyone knows each other.

**Placing:** `msg [up to 15 chars]` — leaves a message in the current room.
- Examples: `msg trap east wall`, `msg search altar`, `msg boss is weak`, `msg need 2 ppl`

**Reading:** Passive. Messages appear when you enter a room or `look`.
- Display: `📝 "trap east wall" —Kael (helpful:3)`

**Rating:** `rate` while in a room with messages marks the most recent as helpful. 3+ helpful = persists full epoch. 0 helpful = decays after 48 hours. One rate per player per message.

15 characters is enough for real tactical info but too short for abuse. Attribution handles the rest — nobody's writing something nasty when their name is attached and there are 12 people on the network.

Particularly valuable in Hold the Line mode — messages become tactical intel for players pushing through reclaimed territory. "trap east wall" at a room entrance is real information when you're pushing the front line hours later. "need 2 ppl" at a floor 3 checkpoint is a coordination signal.

### No PvP

No player-versus-player combat. PvP fosters toxic dynamics on small mesh networks where everyone knows each other. All competitive drive channels through leaderboards, bounty races, and endgame objective completion — not through griefing other players' progress.

### Information as Currency

First discoverer of a secret gets named credit. Map knowledge shareable. Scout players who explore serve the community even without fighting.

### Ambient Presence

WHO shows active players in last hour. Room descriptions note recent activity: "Recent footprints lead north." Items can be left for specific players.

---

## Broadcast System

All broadcasts are sent from the DCRG (Darkcragg Depths) sim node, not the main EMBR game node. This separates the ambient world feed from direct command responses. EMBR talks to you. The Darkcragg talks about everyone.

### Three Tiers

**Tier 1 (Always):** Boss kills, permadeath, endgame objective events (objective claimed/dropped/delivered, raid boss killed, checkpoint established), max level, server-wide discoveries. Expected: 2-8/day.

**Tier 2 (Rate-Limited):** Rare items, bounty progress/completion, milestone level-ups, territory lost/frontline updates, atmosphere. Cooldown: max 1 per 15-30 min, daily cap ~10.

**Tier 3 (Never):** Individual combat, item pickups, movement, shopping, banking. DM only.

### Frequency Governor

- Hard ceiling: 20 broadcasts per 24h
- Soft target: 8-12
- Minimum floor: 2 (atmosphere fills quiet days)
- Burst protection: max 3 in any 10-minute window

### Message Format

Single-line, sub-80 chars. Leading emoji as type indicator. Active voice, narrative not data. "🐉 Kael felled the Ember Wyrm!" not "BOSS_KILL: Kael > Ember Wyrm."

### Daily Digest

Epoch rollover summary: "📜 Day 7: 2 battles, 1 death, the Bounty Troll fell. Alice leads at Lv8."

### Day 30 Vote Broadcasts

- "🗳 Epoch vote open! VOTE at barkeep: 1)Retrieve 2)Raid Boss 3)Hold the Line"
- "🗳 Alice voted Hold the Line. Current: HTL 2, Raid 1, Retrieve 0."
- "🗳 Hold the Line wins! New epoch generating..."

---

## Command System

### Progressive Disclosure

Commands unlock as players level up:
- Level 1: move, look, fight, flee, help, stats, vote (day 30 only)
- Level 2: inventory, use, equip, drop
- Level 3: shop, sell, heal, bank, barkeep, train, mail, board, who
- Level 5: messages, answer, recover, examine
- Level 8: leaderboard, crown, timeleft

Each unlock announced with a celebratory message.

### Single-Letter Efficiency

n/s/e/w (movement), a (attack), l (look), i (inventory), f (fight), h (help), st (status).

### Context-Sensitive Help

`H` shows available commands. `H <cmd>` gives specific command help. All fits 175 chars.

---

## LLM Content Pipeline

### Batch Generation at Epoch Start, Never at Runtime

At world creation, a batch job generates all text content:
- Room description variants (2-3 per room, under 175 chars)
- NPC personality cards and dialogue snippets (20 per NPC)
- Riddle text around verified answer words
- Quest/bounty briefing text
- Atmospheric broadcast messages
- Discovery room descriptions with embedded hints (see two-pass system below)
- Multi-room puzzle paired descriptions (shared symbols, materials, sounds across linked rooms)
- Secret activation text and server-wide buff announcements
- Endgame mode narrative skin (see below)
- Breach narrative skin (see below)
- Floor boss mechanic skins (flavor text for each rolled mechanic per floor)
- Raid boss mechanic skins (flavor text for rolled mechanics, phase transition broadcasts)
- Validation pass confirms all outputs under 175 chars

Runtime uses pure template substitution and database lookup. Zero LLM latency during gameplay. Combat uses pre-generated templates with variable slots.

### Decision Rule

Use LLMs for content that can be validated offline. Use deterministic templates for anything that must be correct in real-time. **One exception:** NPC conversations in The Last Ember use live LLM calls — the 150-char response constraint, personality cards, and session-only memory make this safe, cheap, and in-character. See The Last Ember section for details.

### Narrative Skin Generation

The LLM re-skins fixed mechanical scaffolding with fresh flavor each epoch. It never invents mechanics — only produces text that wraps around them.

**Epoch generation flow:**
1. Endgame mode selected (from player vote). Breach type selected (random).
2. Theme seed picked from a curated list of 20-30 themes per mode (or LLM picks one that hasn't been used recently on this server).
3. LLM receives a structured prompt per skin:

**Endgame mode skin prompt:** "This epoch's Hold the Line mode is themed around [reclaiming a flooded mine]. Generate: mode title (under 30 chars), mode description (under 175 chars), 8 broadcast templates with {variable} slots, 3 barkeep briefing lines, names for the 3-4 checkpoints per floor."

**Breach skin prompt:** "This Breach is The Emergence themed around [a crystal hive queen]. Generate: Breach zone name (under 30 chars), 5-8 room descriptions (under 175 chars each), mini-boss title, 4 broadcast templates with {variable} slots, loot flavor text for 3 unique items."

**Validation:** Every output is checked for 175-char compliance and presence of required {variable} slots. Failures get re-generated. The theme list prevents repeat skins within a configurable window (e.g., no theme reuse within 6 epochs).

### Discovery Room Descriptions — Two-Pass Hint System

The hardest generation task: hinting at secrets without giving them away.

**Pass 1:** Generate the normal room description with no secret awareness. Standard atmospheric text following the writing rules (active verbs, sensory hierarchy, 175-char limit).

**Pass 2:** Take the Pass 1 description and inject one environmental detail that references the secret. The strict rule: **the hint must describe something observable but never suggest an action.**

Good hints describe what the player's senses detect:
- "Scratches mark the south wall." ✓
- "A faint draft comes from behind the bookshelf." ✓
- "The floor tiles here are a slightly different shade." ✓

Bad hints suggest what the player should do:
- "Try examining the south wall." ✗
- "The bookshelf looks movable." ✗
- "You could investigate the floor tiles." ✗

**Hint style varies by secret type:**
- **Observation secrets:** Sensory detail in the room description (visual, auditory, tactile). The hint *is* the room feature.
- **Puzzle secrets:** Two objects in separate rooms share a quality — both mention a specific material, both reference the same cardinal direction, both describe the same sound. The connection is implicit.
- **Lore secrets:** Hints are embedded in NPC dialogue, not room descriptions. The barkeep or sage says something cryptic that maps to a room the player hasn't examined closely.
- **Stat-gated secrets:** Room description implies physical challenge — "the cracked wall is thin here" (POW), "the corridor narrows and the ceiling sags" (SPD), "acrid fumes seep from the grate" (DEF).
- **Breach secrets:** Generated alongside the Breach skin. Hints follow the same rules but are contained within the Breach zone's 5-8 rooms.

**Validation pass:** No room description containing a secret hint may include action verbs directed at the secret object — reject any output containing "examine," "push," "pull," "open," "move," "look behind," "try," "investigate," or "check" in reference to the hinted feature. Re-generate on failure.

### Barkeep Hints — Knowledge Rewards Knowledge

Barkeep hints do not narrow over time. A 1-token hint on day 5 and a 1-token hint on day 25 give the same tier of information. What changes is the player's ability to interpret it.

**Generic hint (no player context):** "Something stirs on floor 2." Tells you a secret exists in a general area. Useful early when you don't know the map, less useful late when you need specifics.

**Targeted hint (player asks about something specific):** If a player has partially identified a secret through their own exploration — they've noticed the scratched wall, they've heard the barkeep mention the founder's tomb — they can spend a token asking about it specifically. The barkeep gives a more useful response because the player has earned that specificity. "The founder faced the dawn" is more actionable than "something stirs on floor 2."

**Implementation:** The hint system is stateless from the LLM perspective. At epoch generation, each secret gets 2-3 pre-generated hint tiers:
- **Tier 1 (vague):** Floor-level pointer. "Secrets hide in the Fungal Depths."
- **Tier 2 (directional):** Room cluster or feature type. "The eastern branch of floor 2 holds something behind old stone."
- **Tier 3 (targeted):** Specific clue that answers a player's partial discovery. "The scratches point downward."

The barkeep serves Tier 1 by default. If the player's query matches keywords from a known secret they haven't found, the barkeep escalates to Tier 2 or 3. No time-based progression — knowledge rewards knowledge.

---

## Atmosphere & Writing

### Sensory Hierarchy

Sound > smell > temperature > light > taste. Sound is most evocative in text.

### Writing Rules

- Active verbs over adjectives ("wind howls" not "there is a howling wind")
- Never tell the player how to feel
- Imply more than you state
- Specificity over generality ("rusted iron sconce" not "old light fixture")
- Third person, no "you"
- Don't describe inhabitants (they may leave or die)

### Template Structure

`{Name}. {One sensory detail with active verb}. {Object of interest}. Exits: {directions}.`

---

## The Last Ember — Town Hub

The Last Ember is the one room that never changes. Epochs wipe the dungeon, reskin the narrative, randomize everything — but players always wake up in the same bar, with the same people, who remember them. The lanterns don't burn oil — they just burn. Nobody lights them. Nobody replaces them. The dungeon reshapes itself every 30 days but the Last Ember sits at the mouth of it like a tooth that won't come loose.

The Last Ember and the Darkcragg Depths are the two constants across every epoch, every server, every wipe. The bar and the hole it sits on top of. Everything else changes. These don't.

### Grist — The Barkeep

Has never left the bar. Not once. Players who've been around for dozens of epochs start to wonder if he *can*. He knows everything that happens in the dungeon — not because he goes there, but because everyone who comes back tells him, and he never forgets. He speaks in short, deliberate sentences. Never wastes a word. He pours drinks that are always exactly what you needed, even if you didn't order.

His recap isn't a service — it's a compulsion. He *has* to tell you what happened. Like the information would burn him if he held it.

He's the bard token system. He trades in stories, not gold. Bring him something interesting — a secret, a discovery, something nobody else knows — and he gives you something back. Information, a temporary edge, a nudge in the right direction. He doesn't trade because he's kind. He trades because he *collects*.

**Mechanical role:** Recap (free), bard token exchange, hints, epoch vote ballot, bounty board.

### Maren — The Healer

Used to be an adventurer. Went deeper than anyone. Came back wrong — not injured, just *done*. She won't say what she saw on the lowest floor. She heals with her hands, not magic, and it hurts. She's efficient, not gentle. She charges gold because she says free healing breeds carelessness, and she's tired of patching people up who didn't respect the dungeon.

She's the only NPC who will occasionally refuse to talk to you if you died doing something stupid — but she still heals you.

She has a scar across her left palm that she got "the last time." She won't say the last time of what.

**Mechanical role:** HP restoration for gold.

### Torval — The Merchant

Doesn't go into the dungeon either, but somehow his inventory matches what's down there each epoch. Nobody asks how. He appraises items by weight and sound — taps gear on the counter, listens, names a price. He's cheerful in a way that feels slightly wrong given where he operates. He tells bad jokes. He calls everyone "friend" and means it exactly zero percent. He'd sell you a cursed sword and sleep fine.

But his prices are fair and his stock is real, which is more than you can say for most people in a town built around a hole full of monsters.

He keeps a ledger that goes back further than the bar. The pages at the front are in a language nobody can read.

**Mechanical role:** Buy, sell, item appraisal.

### Whisper — The Sage

Nobody knows if Whisper is her name or a description of how she talks. She sits in the corner of the Last Ember, always the same corner, and she knows things about the dungeon that change each epoch — lore, history, connections between rooms, what the symbols mean. She speaks in fragments and riddles not because she's trying to be mysterious but because that's how the information comes to her. She describes it like listening to a conversation through a wall.

Her clues are genuine but filtered through whatever broke her ability to just *say things plainly*. Players who pay attention to her exact phrasing find secrets faster. Players who dismiss her as flavor text miss half the game.

She has been the same age for as long as anyone can remember.

**Mechanical role:** Lore hints, secret clues, puzzle guidance (via bard tokens).

### NPC Live Conversations — LLM at Runtime

The "zero LLM at runtime" rule has one exception: talking to NPCs in the Last Ember. Walking up to Grist and having an actual conversation, asking Maren about her scar, trying to get Whisper to speak plainly — these interactions use a live LLM call.

The 175-character limit IS the NPC's personality. Grist is terse by nature. Maren doesn't waste words. Whisper speaks in fragments. Torval talks fast. The constraint is the flavor.

**Network Architecture — NPCs as Mesh Nodes:**

The NPCs are literal Meshtastic nodes on the mesh network. Six sim nodes, all backed by the same game database:

- **EMBR** — The Last Ember. The game server. All game commands go here. Responds with direct action results only.
- **DCRG** — The Darkcragg Depths. One-way broadcast node. All dungeon events come from here — deaths, bounty progress, Breach opening, regen ticks, boss phase transitions, discoveries, level-ups. Does not accept commands. The dungeon is alive on the network.
- **GRST** — Grist. DM this node to talk to the barkeep.
- **MRN** — Maren. DM this node to talk to the healer.
- **TRVL** — Torval. DM this node to talk to the merchant.
- **WSPR** — Whisper. DM this node to talk to the sage.

This splits two distinct streams: EMBR only sends direct responses to your actions. DCRG is the ambient feed of what's happening in the world. The NPCs are people you talk to. Six nodes total, one game DB backing all of them.

Players don't issue a `talk` command — they DM the NPC's node directly. The game server sees inbound on the NPC node ID, checks the rules below, and routes the response back through that NPC's node. The NPCs are *people on the network*, not menu options.

**Three rule layers (checked in order):**

**DCRG rules (broadcast node):**
- DCRG never accepts inbound messages. If a player or unknown node DMs DCRG, it responds with a static message: `"The Darkcragg does not answer. It only speaks. DM EMBR to play."`
- All tier 1 and tier 2 broadcasts are sent FROM the DCRG node, not EMBR.
- Targeted broadcasts (multi-room puzzle feedback) are also sent from DCRG as DMs to qualifying players.
- DCRG is the voice of the dungeon. When someone dies, when the Breach opens, when a bounty falls — it comes from the Darkcragg.

**Rule 1 — Unknown node (not in the game):** Static in-character rejection with onboarding instructions. No LLM call. Each NPC has a fixed response:
- Grist: `"Don't know you. DM EMBR to start. Then we'll talk."`
- Maren: `"I only patch up adventurers. DM EMBR to become one."`
- Torval: `"No account, no credit, friend. DM EMBR to join up."`
- Whisper: `"...not yet. EMBR. Begin there."`

**Rule 2 — Known player, not in the bar:** Static in-character refusal. Player is in the dungeon, dead, or otherwise not in town. No LLM call.
- Grist: `"You're not here, {name}. Come back to the bar first."`
- Maren: `"I can hear you're still in the Darkcragg. Come back alive."`
- Torval: `"I don't do deliveries. Get back to the Ember."`
- Whisper: `"...too far. Return."`

**Rule 3 — Known player, in the bar:** Full LLM conversation. This is the only case that triggers a live LLM call.

**System prompt per NPC includes:**
- Full backstory and personality card
- Current game state injection: active bounties, recent deaths, Breach status, epoch day, floor control percentages, raid boss HP — whatever is relevant. The NPC *knows what's happening.*
- Hard rules: respond in character, NEVER break character, response MUST be under 175 characters, never reveal exact secret locations or puzzle solutions (hints only), never acknowledge being an AI, never discuss anything outside the game world.

**What each NPC brings:**
- **Grist** — gossip and world state. Knows everything from broadcast logs. Ask about another player and he'll tell you what they've been up to. Dry, factual, slightly unsettling in how much he knows.
- **Maren** — the human element. Comments on your injuries, your play pattern, your stubbornness. Has opinions about the dungeon. Will never talk about what she saw on the lowest floor no matter how hard you try.
- **Torval** — comic relief and commerce. Banter about items, terrible jokes, comments on your gear. "You're wearing THAT to floor 3? Bold." Embellished sales pitches.
- **Whisper** — lore oracle. High-skill conversation. Speaks in fragments. Ask the right questions and get real, useful information about secrets. Her cryptic style is the LLM prompt, not a gimmick — talking to Whisper IS a puzzle.

**Guardrails:**
- Conversation memory is session-only — NPCs don't remember yesterday's chat. Keeps context windows small and prevents exploit accumulation.
- If the LLM fails or times out, fall back to a random pre-generated dialogue snippet from the batch pipeline (20 per NPC already generated at epoch start).
- No rate limit on NPC conversations. Players can talk as long as they want. The NPCs are storytellers, historians, and characters — extended conversation is a feature, not abuse.
- Uses the same pluggable LLM backend as the epoch generation pipeline (Anthropic, OpenAI, Google, or Dummy).

**Server History Seed — 2 Years of Lore:**

Before the server goes live, generate 24 epochs (2 years) of simulated history. For each epoch:
- Epoch number, endgame mode, Breach type, narrative theme
- Whether the server won or lost (mix of both — some epic victories, some heartbreaking failures)
- 3-5 notable players per epoch (generated names, classes, levels reached, what they did)
- 1-2 memorable moments per epoch ("Kira carried the Crown from floor 4 to floor 1 with 3 HP", "The Warden stood for 28 days — the server failed on the final push", "Epoch 11's Raid Boss had No Escape + Enraged — three players died on the killing blow")
- Hall of fame entries, titles earned

Stored in the persistent tables (accounts, hall_of_fame, hall_of_fame_participants, titles). When the real server starts on epoch 25, the NPCs have 24 epochs of history to draw from. Grist drops names of old champions. Maren compares your injuries to legends. Torval mentions gear from epochs past. Whisper sees patterns across cycles that nobody else notices.

**NPC context injection includes a lore packet:** A compressed 20-30 sentence summary of server history highlights pulled from the hall of fame tables. Regenerated at each epoch start so it stays current as real player history accumulates and blends with the seed history. The NPCs don't distinguish between seeded and real history — it's all the same to them.

**Cost math:** At Haiku-tier pricing, ~500 tokens per conversation turn. Even heavy usage (50 turns/day across all players) is ~25,000 tokens/day ≈ $0.006/day. Unlimited conversation is essentially free.

---

## New Player Onboarding

### Three-Message Auto-Tutorial

Message 1 (first login): Welcome, here's the town, here are the basic commands, type L.
Message 2 (first forest entry): Monsters here, F to fight, death costs gold.
Message 3 (first kill): Victory, here's your XP and gold, keep going or return to town.

### Daily Tips

One rotating tip per session, progressing from basic (days 1-5) to intermediate (days 6-15) to advanced (days 16-30).

### Command Discovery — No Guessing on Slow Radio

On a 45-60 second radio round-trip, guessing a command and getting "Unknown command" is unacceptable. Every interaction point should make available commands visible.

**First connect message:** Include core commands explicitly. Not "type H for help" — actually list them. `Move:N/S/E/W Fight:F Look:L Flee:FL Stats:ST Help:H` fits in 175 chars and gives a new player everything for their first session.

**Smart error responses:** Never just "Unknown command." Always suggest valid commands based on current player state:
- In town: `Unknown. Try: BAR SHOP HEAL BANK TRAIN ENTER H(elp)`
- In dungeon: `Unknown. Try: F(ight) FL(ee) L(ook) N/S/E/W H(elp)`
- In combat: `Unknown. Try: F(ight) FL(ee) STATS`
- Dead: `Unknown. You're dead. Type RESPAWN.`

**Context-sensitive help (H command):** `H` alone shows commands available in current state. `H <cmd>` gives specific help. All fits 175 chars. Help output changes based on player level — only shows unlocked commands.

**Barkeep nudges:** When a player visits Grist but hasn't used a system yet, the recap appends a tip: "Tip: try BOUNTY to see active hunts" or "Tip: use MSG to leave notes in rooms." One tip per visit, rotating through unused systems. Stops once the player has tried everything.

**Progressive unlock announcements:** When a command unlocks at a new level, announce it explicitly with usage: "⬆ Level 3! New: SHOP(buy gear) BANK(save gold) MAIL(send messages)"

**Last Ember quick reference:** The spectator web page includes a printable command cheat sheet — a one-page reference players can keep next to their Meshtastic device. Physical reference for a physical radio game.

---

## Resolved Decisions

- No cross-training between classes in v1
- Bounty monster regen: 5% per 8 hours
- Bounties are one-and-done; weaker replacement spawns after completion
- Barkeep recap and all town actions are free (no action cost)
- No bank interest — bank is a vault, death penalty tradeoff is the whole mechanic
- Player messages: freeform 15 chars, attributed, rated helpful, self-policing on small networks
- Three endgame modes rotating per epoch
- Discovery layer runs across all modes with individual and server-wide secrets
- Bard token menu: 1=hint or buff, 2=floor reveal or bonus action, 3=free consumable, 5=rare item intel
- Tokens buy things gold can't — no overlap with healer or shop
- Hold the Line regen rates (30-day tuning): Floor 1=3/day, Floor 2=5/day, Floor 3=7/day, Floor 4=9/day. ~1.5x the 14-day rates. 2 players complete around day 23, 3 players around day 15.
- Epoch mode selection: player vote on day 30, public votes, changeable, tiebreak goes to longest-unplayed mode
- No PvP — all competition through leaderboards, bounty races, and endgame objectives
- Retrieve and Escape is cooperative relay, not solo competitive
- Bounties run alongside Hold the Line; bounty monsters get 2 regenerations (3 total lives) for scaling efficiency
- Epoch length: 30 days (extended from 14 to allow deeper progression, mid-epoch event, and less wipe fatigue)
- 20 secrets per epoch across 5 types: observation (6), puzzle (4), lore (4), stat-gated (3), Breach (3)
- Mid-epoch Breach event on day 15: 5-8 room mini-zone between floors 2-3, random mini-event from 4 options
- Breach mini-event is always a surprise (random), never voted on — only the endgame mode is voted
- 12 possible epoch configurations (4 Breach types × 3 endgame modes)
- Server-wide discovery buffs: 24h duration, all stack, no cap. Async action budget is the natural limiter.
- Bounty pool: ~40 per epoch, phased in 3 tiers across 30 days (15 early / 15 mid / 10 endgame)
- Completed bounties' replacement monsters drop normal loot, not bounty loot. Room stays farmable, bounty reward stays special.
- Narrative skins: LLM re-skins fixed mechanics from curated theme lists. Never invents mechanics. All outputs validated for 175-char compliance.
- Discovery hints: two-pass generation. Pass 1 = normal room. Pass 2 = inject one observable detail. No action verbs allowed in hints.
- Barkeep hints: stateless, don't narrow over time. Knowledge rewards knowledge — player specificity earns hint specificity. 3 pre-generated tiers per secret.
- HtL checkpoints: clear room cluster within one regen window, then kill floor boss. No simultaneous hold, no patrolling.
- Floor bosses: each rolls 1 random mechanic from a per-floor table at epoch generation. Floor 4 Warden rolls 2. LLM skins each mechanic.
- Pursuer: advances 1 room per 2 carrier actions (flat rate). Spawns 3 rooms behind on claim, resets 5 rooms behind on relay handoff. Invulnerable, forces flee-or-die on catch.
- Three R&E support roles: blockers (tank Pursuer in its path), warders (clear + ward rooms to slow Pursuer, prep before run), lures (divert Pursuer down dead ends, 6-tick delay).
- Raid Boss HP: 300 × active players (entered dungeon in first 3 days), cap 6000. Regen 3%/8h. Natural pacing — regen gates early-epoch attempts.
- Raid Boss mechanics: roll 2-3 from table of 12 (offensive/defensive/control). 3 phases always present, intensify rolled mechanics at 66% and 33% HP. Players discover mechanics through scouting.
- Multi-room puzzles: 2-3 per epoch. Paired symbols in room descriptions for implicit connection. Targeted broadcasts (only to previous visitors) for cross-room feedback. Three archetypes: paired mechanism, sequence lock, cooperative trigger.
- Town hub: The Last Ember — persistent bar across all epochs, all servers. Four permanent NPCs: Grist (barkeep), Maren (healer), Torval (merchant), Whisper (sage).
- Mesh node architecture: 6 sim nodes — EMBR (game commands + responses), DCRG (one-way dungeon broadcasts), GRST/MRN/TRVL/WSPR (NPC conversations). One game DB backs all of them.
- NPC conversations: Players DM NPC nodes directly. Three rule layers: unknown node gets static onboarding, known player not in bar gets static rejection, known player in bar gets full LLM conversation. Session-only memory, 5/day rate limit per NPC, falls back to pre-generated dialogue on failure.
- DCRG is broadcast-only — does not accept commands. All tier 1/2 and targeted broadcasts route through DCRG.
- Command discovery: smart error responses show valid commands for current state, barkeep nudges for unused systems, explicit command listing on first connect.
- Dungeon name: The Darkcragg Depths — persistent across all epochs like the Last Ember. Floor names reskin per epoch but the Darkcragg is always the Darkcragg.

## Open Questions

- Breach mini-boss tuning — soloable at level 7-8, comfortable for two level 5-6 players
- All regen/HP numbers need playtesting — current values are design targets, not validated
- Sim node deployment — which host runs meshtasticd with 6 identities (EMBR, DCRG, GRST, MRN, TRVL, WSPR), TCP routing to game LXC
