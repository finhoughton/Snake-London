# Referee guide if the bot goes down

First, identify what is wrong. Important:

- **Games pause while the bot is down.** When the bot restarts, its clock (the _Time elapsed_ on `/map`) leaves the downtime out.
- **Multiple games:** do each step for all of them before moving on.

---

## What's wrong?

Run `/map` in a game thread.

| You see                                                              | It means                                                     | Go to                                                                                                              |
| -------------------------------------------------------------------- | ------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------ |
| `/map` works, but a player's command failed or got no reply          | Not an outage: the game refused the move. The bot doesn't always say why, so check the move is allowed |                                                                                                                    |
| `/map` gets no reply                                                 | The bot is down                                              | **1**                                                                                                              |
| The bot answers, but an objective is late or a veto period won't end | The bot's clock is wrong                                     | Restart the bot. Stop it normally, not with a force-kill or reboot, or you lose everything since the clock stopped. Moves made since the clock stopped reappear over the next few minutes, so tell teams not to redo them |
| Your local version fails the same way the VPS did                    | There is a fault in the code or a save                       | **1**, at the end                                                                                                  |
| Something in a game is wrong: coins, cards, a move made by mistake   | It needs a fix                                               | **3**                                                                                                              |

---

## 1. The bot is down

Plan: Pause all games, bring the bot up on your computer, load each game up from its backup, and restart everyone together.

If this is before the game has started, there's nothing to catch up, but still run each game through `admin.py` in step 4, with an empty changes file: that puts its save back in `save` with the right name. If they hadn't been created, skip steps 3 and 4 and create them on your copy.

### Step 1: Pause

Post in all game threads that the game is paused.

### Step 2: Get Anshul to switch the VPS off

Anshul can from their phone. Get them to confirm it's **staying** off: crashed servers often restart themselves, and two bots running at once for the same game will mess everything up. **Don't start your copy until they confirm.** Do steps 3 and 4 while you wait.

### Step 3: Download the backups

The bot posts each game's save to `#______` every ??? minutes.

1. Download the newest save for each game into a `backups` folder in the repo, **not** `save`.
2. Note the time each one was posted.
3. Empty the `save` folder.

### Step 4: Catch each game up

You need to patch the backup with everything that happened since that backup was made.

For each game, list the moves the bot confirmed after its backup was posted. `admin.py` replays them onto the backup and works out crashes, veto periods and objectives. Nothing else goes in.

Make a text file for each game, such as `game1.txt`, in the project root. It starts with the time the backup was posted, then has one move per line in time order. The order decides who reached a contested station first. The times must be in order, though lines can share a minute.

```
backup 14:03
14:05 Alpha complete Central harder
14:06 Beta request Liverpool Street
14:07 Alpha request Tottenham Court Road
14:07 Beta buy Curse
14:07 Beta choose Get a Melon
14:08 Beta veto
until 14:12
```

End the file with `until` and the time the bot went down. Without it, the game's clock carries on from the last move, and the minutes between that and the outage are lost, so veto periods run long.

You will need information from both the game thread and team threads.

**From the game thread:**

| The game thread says                                                            | You write                                                                         |
| ------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| _Alpha has extended their body to Bond Street, they are getting on the Central_ | `14:05 Alpha complete Central harder`                                             |
| _Beta has extended their neck to Liverpool Street_                              | `14:06 Beta request Liverpool Street`                                             |
| _Alpha has extended their neck to Tottenham Court Road_                         | `14:07 Alpha request Tottenham Court Road`                                        |
| _Beta vetoed their challenge at Liverpool Street_                               | `14:08 Beta veto`                                                                 |
| _Alpha has activated Jump on Holborn_                                           | `14:09 Alpha jump Holborn`                                                        |
| _Beta has cursed Alpha with Get a Melon_                                        | `14:10 Beta curse Alpha with Get a Melon`                                         |
| _Alpha has activated their Good Service_                                        | `14:11 Alpha play Good Service`                                                   |
| _Beta has activated their Retreat_                                              | `14:12 Beta play Retreat`                                                         |
| _Alpha has declared a win_                                                      | `14:13 Alpha declare`                                                             |
| _Beta has crashed_                                                              | Nothing: the script works out crashes from the moves. Check it flags the same one |

**From each team's thread,** where the bot confirms secret moves:

| The team's thread shows | You write                       |
| ----------------------- | ------------------------------- |
| They bought a powerup   | `14:07 Beta buy Curse`          |
| They kept a curse       | `14:07 Beta choose Get a Melon` |
| They played a Detour    | `14:12 Alpha detour Elizabeth`  |

The team's thread also says whether each completion was the easier or harder challenge. Put that on the end of the completion's line. The initial challenge doesn't need it.

**Run it** from a terminal in the repo folder:

```
python admin.py backups/1541016481268240495.json game1.txt
```

The number is the game's ID, which is also the backup's file name. The script reads each move back to you, flags any crash, shows where every team stands, writes `save/<ID>.json`, and renders `out/admin_<ID>.png`. Check the list and the map against the game and team threads from just before the outage, including any crashes announced there, and announce any new objective it shows. Also compare the challenges it shows with the ones the bot posted in each team's thread: if they differ, a move is missing or out of order.

**If it stops,** nothing is written. Fix the line and run it again. The usual causes:

- **A misspelt name.** It suggests the nearest.
- **A move that's already in the backup.** Leave it out. For moves in the same minute as the backup, the script lists the backup's last few moves.
- **A curse that isn't in the team's draw.** Usually a move is missing or out of order. If not, use the fix it suggests, such as `Beta give curse Get a Melon`.
- **Anything else that won't fit.** Leave it out and fix the result instead (section 3).
- **A timer that failed.** Use _If the script won't work for a game_, below. The same timer will stop the bot's clock once, when it comes due. Restart the bot and it's gone.

Keep the files. You'll need them if you end up running games by hand.

### Step 5: Start your copy

Once Anshul has confirmed the VPS is off, and you've caught up all games:

1. Check `save` holds exactly one file for each game.
2. From a terminal **in the repo folder**, run `python main.py`.
3. You need one `loaded game` output per game. The bot stops at the first failure and silently skips the rest. It also overwrites the save that failed with a cut-down copy, so fix whatever the last warning says, run `admin.py` again for that game, and then start again.

### Step 6: Check and restart

Post in each game thread:

> The bot is back and this game is up to date. Check the map, and your coins, cards and challenges (`/powerup hand` and `/challenge get`). Let me know if anything looks wrong.

`/map` only shows what's public: claims, Necks and lines. Each team has to check their own coins, cards and curses, because only they can see them.

**If a team says something's wrong,** check it against the message history, then:

1. Stop the bot with Ctrl+C. The bot only reads saves when it starts, and while it's running it writes its own copy over them every minute, so a fix made with the bot still running would be lost.
2. Fix that game's changes file: add the missing move, or a fix (section 3).
3. Run the same `admin.py` command again. It always starts from the untouched backup, so repeating it is safe, and it replaces that game's file in `save`.
4. Start the bot again, check every game loads, and ask the team to check again.

**Don't use `/game reload` instead.** As of writing it is current bugged and idk if Anshul will fix it before the game. It swaps a game's save into the running bot, but doesn't reconnect the game to Discord. The game stops posting to its thread, and the bot stops recognising anyone's team.

When both teams agree everything's right, set a restart time a few minutes ahead and let everyone know. Keep the time between starting the bot and the restart short: the bot's clock runs from the moment it starts, so veto periods and other timers count down even though the teams are paused.

### If the script won't work for a game

Normally `admin.py` adds the lost moves to the backup before the bot starts. If you can't get it to work for a game: start the bot on that game's backup as it is, and have the teams make the lost moves again themselves, through the bot.

1. **Load the backup as it is.** Before starting the bot, copy that game's backup into `save` yourself, which the script would normally do. Name it just the game's ID, like `1541016481268240495.json`.
2. **Tell the teams.** Once the bot is up, post in that game's thread: _"This game has gone back to 14:03. Nobody do anything until I say."_ Use the backup's time.
3. **Have them redo the lost moves.** Go through your list from step 4, calling each move in turn, and have the team make it again with the bot's commands. Wait for the bot to confirm each one before calling the next. The order decides who reached a contested station first, and the bot works out crashes as it goes.
4. **Skip plain vetoes.** Redoing a veto would start a fresh 15 minutes from now, and the bot won't let the team request again until it's up, even though they had already served part of it. Leave the veto out, and tell the team how much of their original period is left, not counting the pause. The bot will still show them the challenges from before the veto, but they carry on with the ones the veto gave them. A veto paid for with Good Service has no wait, so do redo that one, or they'd keep the card.
5. **Expect some draws to change.** Redoing moves, or skipping a veto, changes the random draws after them. If the bot now shows a team different challenges, they keep the ones they were doing. If a redone curse purchase draws different curses, they choose from the new ones.
6. **Check the crashes match.** The bot announces crashes as the moves are redone. They should be the same as the ones it announced before the outage. If they aren't, a move was redone out of order.
7. **Finish with step 6:** the teams check the map and their coins and cards, and you set a restart time.

### If the bot can't come back

This happens in one of two ways:

- **Anshul can't be reached.**
- **Your copy fails the same way the VPS did.** The problem is then in the code or a save, not the server, so restarting won't fix it. If the error mentions a game ID, take that save out of `save` and restart, so the other games come back as normal (step 6). Keep the error text for examination after the game if you can. When you run the broken game by hand, add `--out manual` to the command, so its save goes in a `manual` folder instead of the `save` folder the bot is using.

Run whatever can't go back on the bot by hand with `admin.py` (section 2). That never touches the bot, so it's safe even if the VPS comes back by itself.

**If you couldn't reach Anshul, mute the bot.** The VPS may restart by itself, running an old copy of the game: it would answer commands and post messages that no longer match. In the game channel's permissions, turn off _Use Application Commands_ for everyone, so players can't use its commands, and turn off _Send Messages in Threads_ for the bot, so it can't post. Every game and team thread in the channel follows. Ignore any backups a restarted VPS posts. Undo both settings once the bot is back, and get Anshul to switch the VPS off as soon as you reach them.

---

## 2. Running a game by hand

For when the bot can't come back. You become the bot: the teams send you their moves, `admin.py` works out what each one does, and you tell the teams what the bot would have told them.

**Start it** in live mode, with the game's changes file from section 1:

```
python admin.py backups/1541016481268240495.json game1.txt --live
```

It shows where everyone stands, then waits for you to type. Everything you enter is saved in the changes file, so you can type `quit` and start it again with the same command at any time, and it carries on where it left off. The game's clock runs to the present by itself.

**If you're running more than one game this way,** consider letting the teams handle their own purchases, to cut down on messages. They keep track of their own coins and cards, and only tell you about a card when they play it. You then enter a `give` fix for it just before the play, such as `Alpha give Jump`. After a completion, tell them the coins they earned, not their total, since the script won't know what they've spent. The script shows both, such as _+3 coins, 12 in all_. Two things still have to come to you as they happen:

- **Buying a curse,** because the script draws the curses they choose from.
- **Every card they play, Detour included,** even though Detour is secret, because it changes the line their next Neck is on.

This leaves the script wrong about each team's coins and cards, which only matters if the bot comes back (see _Going back to the bot_, below). If you do this, say so in your message to the teams.

Then post in the game thread:

> We're carrying on without the bot. Don't use its commands, even if it seems to be working again. Post each move in your team thread, and wait for me to confirm it.

**For each move a team sends you:**

1. **Type it and press Enter,** such as `Alpha request Farringdon`. It's stamped with the time now. If the team's message was a while ago, start with its time instead, such as `15:10 Alpha request Farringdon`. Enter moves in the order they were sent. If one turns up late, after a later move is already in, the script says how to add it.
2. **The script shows what the move did,** including the challenges a team is offered, and adds it to the file. If the move can't be done, it says why and leaves the file alone. Correct it and type it again.
3. **Tell the teams what happened:**

| Move                               | In the game thread                                                                         | In the team's thread                           |
| ---------------------------------- | ------------------------------------------------------------------------------------------ | ---------------------------------------------- |
| Request                            | _Alpha has extended their neck to Tottenham Court Road_                                    | The two challenges the script shows            |
| Veto                               | _Alpha vetoed their challenge at Tottenham Court Road_                                     | The two new challenges, and when the veto ends |
| Completion                         | _Alpha has extended their body to Tottenham Court Road, they are getting on the Elizabeth_ | Their coins                                    |
| Jump, Good Service, Retreat, curse | What was played, and where or on whom                                                      |                                                |
| Declaring a win                    | _Alpha has declared a win_                                                                 |                                                |
| Buying a curse                     | Nothing, it's secret                                                                       | The curses it drew, so they can choose one     |
| Buying anything else, keeping a curse, Detour | Nothing, they're secret                                                         | Confirm it                                     |

Announce crashes, new objectives and settled declarations. Post the map (`out/admin_<ID>.png`) after anything significant, and at least every half hour. Press Enter on its own every so often, and when a veto period or declared win is due to end, to see anything that has come due.

Two things the bot handled that the script doesn't:

- **Veto periods:** the script shows _vetoed until 14:23_. Refuse requests, completions and vetoes from that team until then.
- **Secrecy:** its output shows every team's coins, cards and challenges. Never paste it into a game thread.

### Going back to the bot

If the bot can come back part-way through:

1. **If the teams have been handling their own purchases,** put their coins and cards right first. The script's coins will be too high, since it never saw what they spent, and anything they bought but haven't played yet won't be in their hand. Ask each team for their coins and unplayed cards, and type them in as fixes (section 3), such as `Alpha coins 4` and `Alpha give Jump`.
2. **Type `quit`.** The game's latest save is already in `save`, because live mode writes it after every move. If you used `--out manual`, copy it from `manual` into `save`.
3. **Unmute the bot,** if you muted it.
4. **Carry on from step 5 of section 1:** start your copy once Anshul has confirmed the VPS is off, then check and restart as in step 6.

### If the script won't run either

The engine itself is broken, so run the game on paper. For each team, keep their Anchor, line, Neck, claims, coins and cards, and every move with its time. Two things to watch:

- **A completion can crash the other team,** if it claims a station in their live Neck. It's the easiest thing to miss.
- **Challenges:** pick from `challenges.json`, which lists each one's difficulty. Aim for about 4 for a one-station Neck, 5.5 for two, 7.5 for three, 8 for four and 9 for five or more. Offer one just below and one just above, and never repeat one for a team.

---

## 3. Fixing a game

`admin.py` can also correct a save, but only one the bot isn't using: the bot reads saves when it starts and overwrites them as it runs. So fix things while catching up (section 1) or running by hand (section 2), in the same file. Mid-game, Anshul would have to stop the bot, which pauses all games, send you the save, and load your fixed one. Keep that for things that matter.

**To look inside a save,** run `python admin.py backups/<ID>.json`. It shows where everyone stands, and a numbered history in game time:

```
  #3     0:03  Alpha requested Bond Street
  #4     0:04  Alpha vetoed
```

**Fixes** go in the changes file with no time in front:

| Problem                        | Fix                                                           |
| ------------------------------ | ------------------------------------------------------------- |
| Coins are wrong                | `Alpha coins 7`, or `Alpha coins +2` / `Alpha coins -1`       |
| Should have a card             | `Alpha give Detour`                                           |
| Should have a curse            | `Alpha give curse Get a Melon`                                |
| Shouldn't have a card          | `Alpha take Jump`, or `Alpha take curse Get a Melon`          |
| On the wrong line              | `Alpha line Victoria` (at their Anchor, with no challenge on) |
| Challenge should be called off | `Alpha cancel` (back to their Anchor, with no Retreat block)  |
| Veto period should end now     | `Alpha end veto`                                              |
| Has conceded                   | `Alpha out`                                                   |
| Shouldn't be out               | `Alpha back in` (back at their Anchor, with no challenge on)  |
| Objective missing or wrong     | `objective add Green Park`, or `objective remove Green Park`  |
| A move shouldn't have happened | `undo 4`, using the number from the history                   |

**Undo** takes a move out and rebuilds the game without it, so coins and cards come back and any crash it caused is undone. It also removes any timers the move started, such as a veto's 15 minutes. If a later move depended on it, the script names that move: undo it too, or use a different fix. `undo` only reaches moves in the save's history. To take out a line you wrote in the changes file, delete it from the file.

## Setup checklist

Do this the day before, running everything from the repo folder.

- [ ] Clone the repo, then run `git submodule update --init`
- [ ] Install the same Python version as the VPS, which must be 3.14. Create a virtual environment, then run `pip install -e ".[dev]"`
- [ ] Copy the `TOKEN` file over. It isn't in the repo.
- [ ] Check with Anshul that your copy matches the VPS (`git log -1`, and `git status` shows no changes), then don't update it.
- [ ] **Test it with Anshul.** They switch the VPS off. You run `python main.py`, create a test game in a spare channel, complete a challenge and run `/map`. Then run `/game end`, stop your bot, and Anshul switches the VPS back on. Never start `main.py` while the VPS is running.
- [ ] Empty `save` afterwards, or the test game will come back alongside the real ones.
